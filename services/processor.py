"""Processing pipeline: upload -> validate -> convert -> OCR -> AI -> parse -> validate -> duplicate -> persist."""

from __future__ import annotations

import datetime
import json
import logging
import time
from pathlib import Path
from typing import Callable

from sqlalchemy.exc import IntegrityError

from config import AppConfig
from database import repository
from database.database import Database, json_loads
from database.models import ProcessingLog
from services import ai_service, duplicate_service, invoice_parser, ocr_service, validation_service
from utils.file_utils import file_sha256, is_allowed_file, save_upload, validate_filename

logger = logging.getLogger("invoiceai.processor")

ProgressCallback = Callable[[int, int, str], None]  # (index, total, stage)


class ProcessingError(Exception):
    pass


class DuplicateUploadError(ProcessingError):
    def __init__(self, message: str, existing_invoice_id: int):
        super().__init__(message)
        self.existing_invoice_id = existing_invoice_id


class ProcessResult:
    def __init__(self):
        self.ok = False
        self.invoice_id: int | None = None
        self.message = ""
        self.error: str | None = None
        self.skipped = False
        self.status = "Failed"


def _log_update(database: Database, log_id: int, **fields) -> None:
    with database.session() as session:
        repository.update_log(session, log_id, **fields)
        session.commit()


def process_file(config: AppConfig, database: Database, file_path: Path, original_name: str = "", progress_cb: ProgressCallback | None = None) -> ProcessResult:
    """Process a single uploaded invoice file end to end."""
    result = ProcessResult()
    original_name = original_name or file_path.name
    total_steps = 6

    def progress(step: int, stage: str) -> None:
        if progress_cb:
            progress_cb(step, total_steps, stage)

    start = time.monotonic()
    start_iso = datetime.datetime.now().isoformat(timespec="seconds")

    valid, error = validate_filename(file_path.name, config.max_file_size_mb)
    if not valid or not is_allowed_file(file_path.name):
        result.error = error or "Unsupported file type"
        return result

    # ------------------------------------------------------------- file hash
    progress(1, "Validating file")
    try:
        sha = file_sha256(file_path)
    except OSError as exc:
        result.error = f"Could not read file: {exc}"
        return result
    size_bytes = file_path.stat().st_size

    with database.session() as session:
        existing = repository.uploaded_by_hash(session, sha)
        existing_invoices = repository.invoices_by_hash(session, sha)
        log = repository.add_log(
            session,
            original_name,
            file_path=str(file_path),
            file_hash=sha,
            start_time=start_iso,
            status="Processing",
            ocr_status="Pending",
            ai_status="Pending",
        )
        log_id = log.id
        session.commit()

        if existing_invoices:
            result.skipped = True
            result.ok = True
            result.invoice_id = existing_invoices[0].id
            result.status = "Skipped"
            result.message = f"Already processed (invoice #{existing_invoices[0].id}). Skipped."
            repository.update_log(session, log_id, end_time=datetime.datetime.now().isoformat(timespec="seconds"), status="Success", validation_status="Skipped")
            session.commit()
            return result

    # ------------------------------------------------------------- doc prep
    try:
        progress(2, "Converting document / OCR")
        client = ai_service.OllamaClient(config)
        supports_vision = client.supported_vision()
        # Preliminary mode: needed to decide whether to rasterize pages for vision.
        prelim_mode = config.resolve_ai_mode(supports_vision)
        t0 = time.monotonic()
        doc = ocr_service.process_document(str(file_path), config, need_images=(prelim_mode == "vision"))
        logger.info("OCR/doc stage: %.1fs | %d pages | engine=%s", time.monotonic() - t0, doc.page_count, doc.engine_used)
        page_count = doc.page_count
        combined = doc.combined_text()
        images: list[tuple[str, str | None]] = [(page.full_text(), page.image_path) for page in doc.pages]
        if not combined.strip() and not any(img for _, img in images):
            raise ProcessingError("No readable text or image content could be extracted from the document.")
        preview_path = ocr_service.render_preview(str(file_path), config)
    except ProcessingError as exc:
        result.error = str(exc)
        _log_update(database, log_id, end_time=datetime.datetime.now().isoformat(timespec="seconds"), status="Failed", ocr_status="Failed", error_message=str(exc))
        return result
    except Exception as exc:
        result.error = f"Document conversion failed: {exc}"
        _log_update(database, log_id, end_time=datetime.datetime.now().isoformat(timespec="seconds"), status="Failed", ocr_status="Failed", error_message=str(exc))
        return result

    _log_update(database, log_id, ocr_status="Success" if combined.strip() else "Empty")

# ------------------------------------------------------------- AI extraction
    # Fast path: documents with a usable text layer are extracted in text mode
    # (no image processing) -> 10-50x faster on CPU-only machines.
    mode = config.resolve_ai_mode(supports_vision, has_text=len(combined.strip()) >= 120)
    if mode == "text" and not combined.strip():
        message = (
            "No readable text could be extracted from this document (it appears to be a scan). "
            "Install PaddleOCR (pip install paddlepaddle paddleocr) or select a vision model "
            "such as qwen3-vl in Settings."
        )
        result.error = message
        _log_update(database, log_id, end_time=datetime.datetime.now().isoformat(timespec="seconds"), status="Failed", ai_status="Failed", error_message=message)
        return result
    try:
        progress(3, f"AI extraction ({config.ollama_model})")
        from services import table_parser

        deterministic = table_parser.parse(combined) if combined.strip() else None
        if deterministic is not None:
            raw, mode_used, model_used = deterministic, "text", "rule-based"
            logger.info("Table parser matched (deterministic) — skipping AI")
        else:
            if mode == "vision":
                encoded = ai_service.encode_images_for_vision(images)
            else:
                encoded = []
            t0 = time.monotonic()
            raw, mode_used = ai_service.extract_invoice(combined, encoded, config, mode=mode)
            model_used = config.ollama_model
        logger.info("AI extract: %.1fs | mode=%s | json=%d chars", time.monotonic() - t0, mode_used, len(json.dumps(raw)))
    except ai_service.ModelNotInstalledError as exc:
        result.error = str(exc)
        _log_update(database, log_id, end_time=datetime.datetime.now().isoformat(timespec="seconds"), status="Failed", ai_status="Failed", error_message=str(exc), model_used=config.ollama_model)
        return result
    except ai_service.OllamaError as exc:
        result.error = str(exc)
        _log_update(database, log_id, end_time=datetime.datetime.now().isoformat(timespec="seconds"), status="Failed", ai_status="Failed", error_message=str(exc), model_used=config.ollama_model)
        return result
    except Exception as exc:
        result.error = f"AI extraction failed: {exc}"
        _log_update(database, log_id, end_time=datetime.datetime.now().isoformat(timespec="seconds"), status="Failed", ai_status="Failed", error_message=str(exc), model_used=config.ollama_model)
        return result

    _log_update(database, log_id, ai_status="Success", model_used=model_used)

    # ------------------------------------------------------------- parse + intelligence
    progress(4, "Building structured data")
    try:
        parsed = invoice_parser.normalize_ai_output(raw)
        if config.enable_tax_intelligence:
            invoice_parser.apply_tax_intelligence(parsed)
    except Exception as exc:
        result.error = f"Parsing failed: {exc}"
        _log_update(database, log_id, end_time=datetime.datetime.now().isoformat(timespec="seconds"), status="Failed", error_message=str(exc))
        return result

    # ------------------------------------------------------------- validation
    validation = {"status": "Extracted", "validation_status": None, "checks": [], "mismatch_count": 0} if not config.enable_validation else validation_service.validate(parsed)

    # ------------------------------------------------------------- duplicates
    duplicate = {"status": "None", "fingerprint": None, "duplicate_of_id": None}
    if config.enable_duplicate_check:
        with database.session() as session:
            duplicate = duplicate_service.check_duplicate(session, parsed)

    # ------------------------------------------------------------- persist
    progress(5, "Saving to database")
    try:
        with database.session() as session:
            invoice = repository.create_invoice(
                session,
                parsed,
                validation,
                duplicate,
                {
                    "file_hash": sha,
                    "original_filename": original_name,
                    "file_path": str(file_path),
                    "page_count": page_count,
                    "preview_path": preview_path,
                    "ai_model": model_used,
                    "ai_mode": mode_used,
                    "ocr_engine": doc.engine_used,
                },
            )
            repository.add_uploaded_file(session, file_path, original_name, file_path.name, size_bytes, sha, invoice_id=invoice.id)
            repository.update_log(
                session, log_id,
                end_time=datetime.datetime.now().isoformat(timespec="seconds"),
                duration_ms=int((time.monotonic() - start) * 1000),
                status="Success",
                validation_status=validation.get("validation_status"),
                model_used=model_used,
                invoice_id=invoice.id,
            )
            session.commit()
            invoice_id = invoice.id
    except IntegrityError as exc:
        with database.session() as session:
            repository.update_log(session, log_id, status="Failed", error_message=f"Database conflict: {exc.orig if exc.orig else exc}")
            session.commit()
        result.error = "Database conflict while saving."
        return result
    except Exception as exc:
        with database.session() as session:
            repository.update_log(session, log_id, status="Failed", error_message=f"Save failed: {exc}")
            session.commit()
        result.error = f"Save failed: {exc}"
        return result

    progress(6, "Complete")
    result.ok = True
    result.invoice_id = invoice_id
    result.status = validation.get("status", "Extracted")
    result.message = f"Invoice saved as #{invoice_id} ({validation.get('validation_status', '')})"
    return result


def retry_failed_log(database: Database, log_id: int, config: AppConfig) -> int | None:
    """Re-process a failed file. Returns new invoice id or None."""
    with database.session() as session:
        log = session.get(ProcessingLog, log_id)
        if log is None or not log.file_path:
            return None
        path = Path(log.file_path)
        name = log.file_name
        repository.mark_retry(session, log_id)
        session.commit()
    if not path.exists():
        return None
    result = process_file(config, database, path, original_name=name)
    return result.invoice_id
