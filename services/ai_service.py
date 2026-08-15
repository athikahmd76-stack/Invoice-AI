"""Ollama integration: connectivity checks, model listing, invoice extraction."""

from __future__ import annotations

import base64
import io
import json
import logging
import re
import time
from typing import Any

import requests
from PIL import Image

from config import AppConfig

logger = logging.getLogger("invoiceai.ai")

# Model capabilities are fetched from Ollama on every file during a batch; a
# short TTL cache avoids the repeated /api/tags round-trips. Keyed by
# (base_url, model) so switching settings in the UI still takes effect quickly.
_CAPABILITY_CACHE_TTL = 60.0
_capability_cache: dict[tuple[str, str], tuple[float, list[str]]] = {}

SYSTEM_PROMPT = """You are an invoice extraction engine. Extract only data explicitly printed on the invoice; never invent values. Omit any field not found (no null keys). Preserve invoice numbers, SKU/HSN codes and decimals exactly. Extract every line item in original order - every row of the item table becomes one item object, never skip or merge rows. For GST: fill IGST for inter-state invoices, or CGST and SGST for intra-state - never all three. quantity = number of units (an integer), unit_price = price per single unit, line_total = total amount for the line. grand_total = the final payable amount (usually the line labelled "Total" or "Rs. Total"). Return valid JSON only - no markdown, no extra text. Use INR when the invoice is Indian and no currency is printed."""


class OllamaError(Exception):
    pass


class ModelNotInstalledError(OllamaError):
    pass


class InvalidJsonError(OllamaError):
    pass


class OllamaClient:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.timeout = config.ai_timeout_seconds

    # ------------------------------------------------------------ connection
    def ping(self) -> bool:
        try:
            resp = requests.get(f"{self.config.ollama_base_url}/", timeout=5)
            return resp.status_code < 500
        except requests.RequestException:
            return False

    def list_models(self) -> list[str]:
        try:
            resp = requests.get(f"{self.config.ollama_base_url}/api/tags", timeout=15)
            resp.raise_for_status()
            data = resp.json()
            return [str(m.get("name", "")) for m in data.get("models", []) if m.get("name")]
        except (requests.RequestException, ValueError):
            return []

    def model_capabilities(self, model: str | None = None) -> list[str]:
        target = model or self.config.ollama_model
        key = (self.config.ollama_base_url, target)
        now = time.monotonic()
        hit = _capability_cache.get(key)
        if hit and now - hit[0] < _CAPABILITY_CACHE_TTL:
            return hit[1]
        caps: list[str] = []
        try:
            resp = requests.get(f"{self.config.ollama_base_url}/api/tags", timeout=15)
            resp.raise_for_status()
            data = resp.json()
            for m in data.get("models", []):
                if m.get("name") == target:
                    caps = [str(c) for c in m.get("capabilities", [])]
                    break
        except (requests.RequestException, ValueError):
            pass
        _capability_cache[key] = (now, caps)
        return caps

    def model_exists(self, model: str) -> bool:
        return model in self.list_models()

    def supported_vision(self, models: list[str] | None = None) -> bool:
        caps = self.model_capabilities()
        if caps:
            return "vision" in caps
        # Capabilities lists are unreliable in some Ollama versions (e.g. gemma3
        # reports only "completion"); fall back to model-name hints. Only the
        # SELECTED model is considered - installed-but-unselected models must
        # not make a text model look vision-capable.
        names = models if models is not None else self.list_models()
        vision_hints = ("vl", "vision", "llava", "bakllava", "minicpm", "moondream", "gemma3", "gemma4")
        selected = (self.config.ollama_model or "").lower()
        if any(hint in selected for hint in vision_hints):
            return True
        return any((m or "").lower() == selected for m in names)

    # --------------------------------------------------------------- calls
    def generate(self, prompt: str, images: list[str] | None = None, temperature: float | None = None) -> str:
        payload: dict[str, Any] = {
            "model": self.config.ollama_model,
            "prompt": prompt,
            "stream": True,
            # A strict JSON-schema grammar makes small models emit the root
            # closing brace and stop (done_reason=stop) instead of padding to
            # the num_predict cap (done_reason=length, ~2500 tokens, which on a
            # CPU machine wasted 400+ seconds and truncated big invoices
            # mid-item, silently dropping line items).
            "format": FORMAT_SCHEMA,
            # `think` must be top-level (inside `options` it is ignored and
            # reasoning models burn the whole token budget on thinking).
            "think": False,
            "options": {
                "temperature": temperature if temperature is not None else self.config.ai_temperature,
                "num_predict": self.config.ai_max_tokens,
                "num_ctx": self.config.ai_num_ctx,
            },
        }
        if images:
            payload["images"] = images
        url = f"{self.config.ollama_base_url}/api/generate"
        try:
            resp = requests.post(url, json=payload, timeout=self.timeout, stream=True)
        except requests.RequestException as exc:
            raise OllamaError(f"Could not reach Ollama at {self.config.ollama_base_url}: {exc}") from exc
        if resp.status_code == 404 and "model" in (resp.text or "").lower():
            raise ModelNotInstalledError(
                f"Model '{self.config.ollama_model}' is not installed. Run: ollama pull {self.config.ollama_model}"
            )
        if resp.status_code == 400:
            raise OllamaError(f"Ollama rejected the request. Try a different model or install a vision model. Details: {resp.text[:300]}")
        if resp.status_code >= 400:
            raise OllamaError(f"Ollama error {resp.status_code}: {resp.text[:300]}")
        # Stream the response and abort the request as soon as the emitted text
        # is a complete JSON object. The model usually continues emitting after
        # the root object closes (trailing padding); closing the connection here
        # saves the remaining generation time with no accuracy cost.
        acc: list[str] = []
        try:
            for raw_line in resp.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                try:
                    obj = json.loads(raw_line)
                except (ValueError, TypeError):
                    continue
                piece = obj.get("response", "")
                if piece:
                    acc.append(piece)
                    text = "".join(acc)
                    if _is_complete_json(text):
                        break
                if obj.get("done"):
                    break
        finally:
            resp.close()
        return "".join(acc).strip()


def encode_images_for_vision(pages_text_images: list[tuple[str, str | None]], max_width: int = 1600, max_pages: int = 8) -> list[str]:
    """Encode page images (path) to base64 JPEG strings for the vision model."""
    encoded: list[str] = []
    for _, image_path in pages_text_images[:max_pages]:
        if not image_path:
            continue
        try:
            with Image.open(image_path) as img:
                if img.width > max_width:
                    ratio = max_width / img.width
                    img = img.resize((max_width, int(img.height * ratio)))
                buffer = io.BytesIO()
                img.convert("RGB").save(buffer, format="JPEG", quality=85)
                encoded.append(base64.b64encode(buffer.getvalue()).decode("ascii"))
        except Exception as exc:
            logger.warning("Image encode failed for %s: %s", image_path, exc)
    return encoded


def build_prompt(text: str, schema_note: str = "", max_chars: int = 20000) -> str:
    content = text.strip() or "(No text layer available - the document is a scan; read everything from the attached page images.)"
    schema = schema_note or COMPACT_SCHEMA
    return f"""{SYSTEM_PROMPT}

Extract the structured invoice data from this document.

DOCUMENT CONTENT:
{content[:max_chars]}

Return ONLY a JSON object matching the schema below. Include a key only when its value is actually printed in the document - omit keys that are absent (never output null values).
Do not copy the example placeholder values - read the document and fill in what is actually printed.

JSON_SCHEMA:
{schema}"""


COMPACT_SCHEMA = """{
  "vendor": {"name": null, "gstin": null, "address": null},
  "invoice": {"number": null, "date": null, "due_date": null, "po_number": null, "po_date": null, "eway_bill": null, "currency": "INR", "payment_terms": null, "place_of_supply": null},
  "buyer": {"name": null, "gstin": null},
  "items": [{"line_no": 1, "product_name": null, "product_description": null, "sku": null, "hsn": null, "quantity": null, "uom": null, "unit_price": null, "gst_pct": null, "line_total": null}],
  "taxes": {"taxable_value": null, "cgst": null, "sgst": null, "igst": null, "cess": null},
  "totals": {"subtotal": null, "discount": null, "round_off": null, "grand_total": null, "amount_paid": null, "balance_due": null},
  "payment": {"bank_name": null, "bank_account": null, "ifsc": null, "upi": null}
}"""


# Strict JSON-schema grammar sent as Ollama's `format`. Unlike a plain "json"
# format (which qwen3:1.7b pads to num_predict=2500 tokens, truncating large
# invoices mid-item), the constrained grammar forces the root object to close
# and the model to stop naturally after the last item, extracting every row.
FORMAT_SCHEMA = {
    "type": "object",
    "properties": {
        "vendor": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "gstin": {"type": "string"}, "address": {"type": "string"}},
        },
        "invoice": {
            "type": "object",
            "properties": {
                "number": {"type": "string"},
                "date": {"type": "string"},
                "due_date": {"type": "string"},
                "po_number": {"type": "string"},
                "po_date": {"type": "string"},
                "eway_bill": {"type": "string"},
                "currency": {"type": "string"},
                "payment_terms": {"type": "string"},
                "place_of_supply": {"type": "string"},
            },
        },
        "buyer": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "gstin": {"type": "string"}},
        },
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "line_no": {"type": "integer"},
                    "product_name": {"type": "string"},
                    "product_description": {"type": "string"},
                    "sku": {"type": "string"},
                    "hsn": {"type": "string"},
                    "quantity": {"type": "number"},
                    "uom": {"type": "string"},
                    "unit_price": {"type": "number"},
                    "gst_pct": {"type": "number"},
                    "line_total": {"type": "number"},
                },
            },
        },
        "taxes": {
            "type": "object",
            "properties": {
                "taxable_value": {"type": "number"},
                "cgst": {"type": "number"},
                "sgst": {"type": "number"},
                "igst": {"type": "number"},
                "cess": {"type": "number"},
            },
        },
        "totals": {
            "type": "object",
            "properties": {
                "subtotal": {"type": "number"},
                "discount": {"type": "number"},
                "round_off": {"type": "number"},
                "grand_total": {"type": "number"},
                "amount_paid": {"type": "number"},
                "balance_due": {"type": "number"},
            },
        },
        "payment": {
            "type": "object",
            "properties": {
                "bank_name": {"type": "string"},
                "bank_account": {"type": "string"},
                "ifsc": {"type": "string"},
                "upi": {"type": "string"},
            },
        },
    },
    "required": ["vendor", "invoice", "buyer", "items", "taxes", "totals", "payment"],
}


def _is_complete_json(text: str) -> bool:
    """True when the accumulated streamed text is a complete JSON object."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return False
    return isinstance(data, dict)


def extract_json(raw: str) -> dict:
    """Parse the model response, tolerating code fences, stray text and truncation."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise InvalidJsonError("Model response did not contain a JSON object.")
    chunk = text[start : end + 1]
    data = None
    try:
        data = json.loads(chunk)
    except (ValueError, TypeError):
        repaired = re.sub(r"(?<=[,\{])(\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', chunk)
        try:
            data = json.loads(repaired)
        except (ValueError, TypeError):
            # last resort: trim trailing garbage, bounded attempts
            candidates = sorted(set(int(len(chunk) * (1 - (0.1 * (i + 1)))) for i in range(9)) | {len(chunk) // 2}, reverse=True)
            for cut in candidates:
                if cut < len(chunk) * 0.3:
                    break
                try:
                    data = json.loads(chunk[:cut])
                    break
                except (ValueError, TypeError):
                    continue
    if not isinstance(data, dict):
        raise InvalidJsonError("Extracted JSON is not an object.")
    return data


def extract_invoice(data_text: str, images_base64: list[str], config: AppConfig, mode: str = "vision") -> tuple[dict, str]:
    """Run the extraction through the configured Ollama model.

    Returns (extracted_data, effective_mode). In "auto"/"vision" mode, if the
    model rejects images (no vision support), it retries once in text mode so
    processing continues instead of failing.
    """
    client = OllamaClient(config)
    # Keep the prompt small enough to fit the configured context window. Vision
    # mode leaves room for the page images, which are the source of truth.
    prompt = build_prompt(data_text, max_chars=12000 if mode == "vision" else 20000)
    if mode == "vision":
        prompt += (
            "\n\nIMPORTANT: The attached page images are the source of truth. "
            "Scanned invoices are expected: extract every visible field and every line item from the images, "
            "even when the print is faint, rotated or low quality. "
            "The text below is OCR output that may contain errors; prefer the images when they differ."
        )
    elif mode == "text":
        prompt += "\n\nNo images are attached; use only the document content above."
    else:
        raise ValueError(f"Unknown AI mode: {mode}")

    try:
        raw = client.generate(prompt, images=images_base64 if mode == "vision" else None)
    except OllamaError as exc:
        if mode == "vision" and "image" in str(exc).lower():
            logger.warning("Model rejected images (%s); retrying in text mode.", exc)
            prompt = build_prompt(data_text, max_chars=20000)
            prompt += "\n\nNo images are attached; use only the document content above."
            raw = client.generate(prompt, images=None)
            return extract_json(raw), "text"
        raise
    return extract_json(raw), mode