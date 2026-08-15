"""Document processing: page conversion, PDF text layer, OCR (PaddleOCR)."""

from __future__ import annotations

import logging
import os

from config import AppConfig
from utils.file_utils import is_image_file

logger = logging.getLogger("invoiceai.ocr")

try:  # PyMuPDF >= 1.24.14: canonical module name is pymupdf
    import pymupdf as fitz  # noqa: F401
except ImportError:  # pragma: no cover
    import fitz  # noqa: F401

TEXT_LAYER_MIN_CHARS = 60


class PageDoc:
    def __init__(self, page_no: int, text: str = "", tables: list[str] | None = None, image_path: str | None = None, has_text_layer: bool = False):
        self.page_no = page_no
        self.text = text or ""
        self.tables = tables or []
        self.image_path = image_path
        self.has_text_layer = has_text_layer

    def full_text(self) -> str:
        parts = [self.text]
        parts.extend(self.tables)
        return "\n".join(p for p in parts if p)


class DocumentResult:
    def __init__(self):
        self.pages: list[PageDoc] = []
        self.engine_used: str = "none"
        self.page_count: int = 0

    def add_page(self, page: PageDoc) -> None:
        self.pages.append(page)
        self.page_count = len(self.pages)

    def combined_text(self) -> str:
        blocks = []
        for page in self.pages:
            blocks.append(f"--- PAGE {page.page_no} ---")
            blocks.append(page.full_text())
        return "\n".join(blocks)


# ------------------------------------------------------------------ PaddleOCR

_PADDLE_ENGINE = None
_PADDLE_API = "old"


def paddle_available() -> bool:
    try:
        import paddle  # noqa: F401
        import paddleocr  # noqa: F401
        return True
    except Exception:
        return False


def _get_paddle_engine(config: AppConfig):
    global _PADDLE_ENGINE, _PADDLE_API
    if _PADDLE_ENGINE is not None:
        return _PADDLE_ENGINE
    try:
        from paddleocr import PaddleOCR
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"PaddleOCR is not installed properly: {exc}") from exc

    os.environ.setdefault("PADDLE_PDX_MODEL_DIR", str(config.model_dir))
    kwargs: dict = {"lang": config.ocr_language, "show_log": False}
    try:
        _PADDLE_ENGINE = PaddleOCR(lang=config.ocr_language, use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False, show_log=False)
        _PADDLE_API = "new"
    except TypeError:
        _PADDLE_ENGINE = PaddleOCR(use_angle_cls=True, lang=config.ocr_language, show_log=False)
        _PADDLE_API = "old"
    return _PADDLE_ENGINE


def _ocr_image_new(engine, image_path: str) -> list[str]:
    """PaddleOCR 3.x style: engine.predict returns a list of result objects."""
    results = engine.predict(image_path)
    lines: list[str] = []
    for result in results:
        if result is None:
            continue
        texts = getattr(result, "rec_texts", None)
        if texts:
            lines.extend(str(t) for t in texts if t)
            continue
        dicts = result if isinstance(result, dict) else None
        if isinstance(dicts, dict):
            texts = dicts.get("rec_texts") or dicts.get("texts") or []
            lines.extend(str(t) for t in texts if t)
    return [ln for ln in lines if ln.strip()]


def _ocr_image_old(engine, image_path: str) -> list[str]:
    """PaddleOCR 2.x style: engine.ocr returns [[[box, (text, score)], ...]]"""
    result = engine.ocr(image_path, cls=True)
    lines: list[str] = []
    if not result:
        return lines
    for page in result:
        if not page:
            continue
        for entry in page:
            if not entry or len(entry) < 2:
                continue
            text = entry[1][0] if isinstance(entry[1], (list, tuple)) else entry[1]
            if text:
                lines.append(str(text).strip())
    return lines


def ocr_image(image_path: str, config: AppConfig) -> list[str]:
    engine = _get_paddle_engine(config)
    if _PADDLE_API == "new":
        return _ocr_image_new(engine, image_path)
    return _ocr_image_old(engine, image_path)


def _order_ocr_lines(lines: list[tuple]) -> list[str]:
    """Best-effort top-to-bottom, left-to-right ordering of recognized lines."""
    if not lines:
        return []
    boxes = [ln for ln in lines if ln]
    return [ln[0] for ln in boxes]


# --------------------------------------------------------------- document flow

def _load_pdf(path: str):
    return fitz.open(path)


def _convert_pdf_pages(path: str, config: AppConfig, max_pages: int = 60, need_images: bool = True) -> tuple[list, list[list[str]]]:
    """Render PDF pages to images. Returns (page_images, text_layer).

    Pages are only rasterized when required: for vision mode, OCR, or pages
    without a usable text layer. Pages with a good text layer get ``None``
    when images are not needed, saving significant render time.
    """
    images: list = []
    text_layers: list[list[str]] = []
    try:
        doc = fitz.open(path)
    except Exception as exc:
        raise RuntimeError(f"Could not open PDF: {exc}") from exc
    try:
        for page_no in range(min(doc.page_count, max_pages)):
            page = doc.load_page(page_no)
            lines = [ln.strip() for ln in page.get_text("text").splitlines() if ln.strip()]
            text_layers.append(lines)
            if not need_images and len(" ".join(lines)) >= TEXT_LAYER_MIN_CHARS:
                images.append(None)
                continue
            pix = page.get_pixmap(dpi=150)
            images.append(pix)
    finally:
        doc.close()
    return images, text_layers


def process_document(path: str, config: AppConfig, progress_cb=None, need_images: bool = True) -> DocumentResult:
    """Convert a PDF/image into PageDoc list: text layer and/or OCR content."""
    result = DocumentResult()
    is_pdf = path.lower().endswith(".pdf")
    engine_requested = config.ocr_engine  # auto | paddle | text | none

    want_paddle = engine_requested in ("auto", "paddle")
    use_paddle = want_paddle and paddle_available()
    if want_paddle and not use_paddle and engine_requested == "paddle":
        raise RuntimeError("PaddleOCR is not installed. Install with: pip install paddlepaddle paddleocr")

    if is_pdf:
        try:
            images, text_layers = _convert_pdf_pages(path, config, need_images=need_images or use_paddle)
        except Exception as exc:
            raise RuntimeError(f"PDF conversion failed: {exc}") from exc
    elif is_image_file(path):
        images, text_layers = [path], [[]]
    else:
        raise RuntimeError("Unsupported file type for document processing.")

    total = len(images)
    if total == 0:
        raise RuntimeError("Document contains no readable pages.")
    result.page_count = total

    for idx, image in enumerate(images):
        if progress_cb:
            progress_cb(idx, total)
        page_no = idx + 1
        text_lines = text_layers[idx] if idx < len(text_layers) else []
        has_layer = len(" ".join(text_lines)) >= TEXT_LAYER_MIN_CHARS

        page = PageDoc(page_no=page_no, has_text_layer=has_layer)

        image_path = None
        if image is not None and hasattr(image, "tobytes"):  # fitz pixmap
            image_path = os.path.join(config.tmp_dir, f"page_{page_no}.png")
            image.save(image_path)
        elif image is not None:
            image_path = image

        if not has_layer and use_paddle and image_path:
            try:
                lines = ocr_image(image_path, config)
                page.text = "\n".join(lines)
                result.engine_used = "paddle"
            except Exception as exc:
                logger.warning("OCR failed on page %s: %s", page_no, exc)
                page.text = ""
        else:
            page.text = "\n".join(text_lines)
            if has_layer and result.engine_used in ("", "none"):
                result.engine_used = "pdf-text"
            if not has_layer and not use_paddle and result.engine_used in ("", "none"):
                result.engine_used = "none"

        result.add_page(page)
        page.image_path = image_path

    if result.engine_used == "none" and use_paddle:
        result.engine_used = "paddle"
    return result


def render_preview(path: str, config: AppConfig, width: int = 1200) -> str | None:
    """Create a JPEG preview of the first page for display. Returns path or None."""
    preview_dir = config.preview_dir
    preview_dir.mkdir(parents=True, exist_ok=True)
    from utils.file_utils import file_sha256

    try:
        key = file_sha256(path.__class__(path) if isinstance(path, str) else path)[:16]
    except Exception:
        key = "unknown"
    preview_path = preview_dir / f"preview_{key}.jpg"
    if preview_path.exists():
        return str(preview_path)
    try:
        if str(path).lower().endswith(".pdf"):
            doc = fitz.open(path)
            if doc.page_count == 0:
                return None
            pix = doc.load_page(0).get_pixmap(dpi=130)
            pix.save(str(preview_path))
            doc.close()
        else:
            from PIL import Image

            img = Image.open(path)
            ratio = width / img.width
            img = img.resize((width, int(img.height * ratio)))
            img.convert("RGB").save(preview_path, "JPEG", quality=85)
        return str(preview_path)
    except Exception as exc:
        logger.warning("Preview render failed: %s", exc)
        return None