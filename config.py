"""Application configuration loaded from .env and persisted runtime settings."""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
DATA_DIR = BASE_DIR / "data"
SETTINGS_FILE = DATA_DIR / "settings.json"

DEFAULT_SETTINGS = {
    "ollama_base_url": "http://localhost:11434",
    "ollama_model": "qwen3-vl:8b",
    "ai_mode": "auto",
    "ai_temperature": 0.0,
    "ai_timeout_seconds": 900,
    "ai_max_tokens": 2500,
    "ai_num_ctx": 8192,
    "max_file_size_mb": 25,
    "ocr_engine": "auto",
    "ocr_language": "en",
    "ocr_enable_table": True,
    "enable_duplicate_check": True,
    "enable_validation": True,
    "enable_tax_intelligence": True,
}


def _env_bool(key: str, default: bool) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return str(val).strip().lower() in {"1", "true", "yes", "on"}


class AppConfig:
    """Central configuration. Runtime settings (Settings page) override .env values."""

    _instance: "AppConfig | None" = None

    def __init__(self) -> None:
        load_dotenv(ENV_FILE)
        self._runtime: dict = self._load_runtime()

    # ------------------------------------------------------------------ paths
    @property
    def base_dir(self) -> Path:
        return BASE_DIR

    @property
    def data_dir(self) -> Path:
        return DATA_DIR

    @property
    def upload_dir(self) -> Path:
        return BASE_DIR / "uploads"

    @property
    def export_dir(self) -> Path:
        return BASE_DIR / "exports"

    @property
    def log_dir(self) -> Path:
        return BASE_DIR / "logs"

    @property
    def model_dir(self) -> Path:
        return BASE_DIR / "models"

    @property
    def tmp_dir(self) -> Path:
        return DATA_DIR / "tmp"

    @property
    def preview_dir(self) -> Path:
        return DATA_DIR / "previews"

    # ------------------------------------------------------------ app basics
    @property
    def app_name(self) -> str:
        return os.getenv("APP_NAME", "InvoiceAI")

    @property
    def database_url(self) -> str:
        url = os.getenv("DATABASE_URL", "sqlite:///data/invoiceai.db")
        if url.startswith("sqlite:///"):
            parts = url.split("sqlite:///", 1)[1]
            p = Path(parts)
            if not p.is_absolute():
                p = BASE_DIR / p
            return f"sqlite:///{p.as_posix()}"
        return url

    @property
    def log_level(self) -> str:
        return os.getenv("LOG_LEVEL", "INFO").upper()

    # ---------------------------------------------------------------- Ollama
    @property
    def ollama_base_url(self) -> str:
        return str(self._runtime.get("ollama_base_url") or os.getenv("OLLAMA_BASE_URL") or DEFAULT_SETTINGS["ollama_base_url"]).rstrip("/")

    @property
    def ollama_model(self) -> str:
        return str(self._runtime.get("ollama_model") or os.getenv("OLLAMA_MODEL") or DEFAULT_SETTINGS["ollama_model"])

    @property
    def ai_mode(self) -> str:
        return str(self._runtime.get("ai_mode") or os.getenv("AI_MODE") or DEFAULT_SETTINGS["ai_mode"]).lower()

    @property
    def ai_temperature(self) -> float:
        try:
            return float(self._runtime.get("ai_temperature") or os.getenv("AI_TEMPERATURE") or DEFAULT_SETTINGS["ai_temperature"])
        except (TypeError, ValueError):
            return 0.0

    @property
    def ai_timeout_seconds(self) -> int:
        try:
            return int(self._runtime.get("ai_timeout_seconds") or os.getenv("AI_TIMEOUT_SECONDS") or DEFAULT_SETTINGS["ai_timeout_seconds"])
        except (TypeError, ValueError):
            return 900

    @property
    def ai_max_tokens(self) -> int:
        try:
            return int(self._runtime.get("ai_max_tokens") or os.getenv("AI_MAX_TOKENS") or DEFAULT_SETTINGS["ai_max_tokens"])
        except (TypeError, ValueError):
            return 2500

    @property
    def ai_num_ctx(self) -> int:
        try:
            return int(self._runtime.get("ai_num_ctx") or os.getenv("AI_NUM_CTX") or DEFAULT_SETTINGS["ai_num_ctx"])
        except (TypeError, ValueError):
            return 8192

    # ---------------------------------------------------------------- uploads
    @property
    def max_file_size_mb(self) -> int:
        try:
            return int(self._runtime.get("max_file_size_mb") or os.getenv("MAX_FILE_SIZE_MB") or DEFAULT_SETTINGS["max_file_size_mb"])
        except (TypeError, ValueError):
            return 25

    # -------------------------------------------------------------------- OCR
    @property
    def ocr_engine(self) -> str:
        return str(self._runtime.get("ocr_engine") or os.getenv("OCR_ENGINE") or DEFAULT_SETTINGS["ocr_engine"]).lower()

    @property
    def ocr_language(self) -> str:
        return str(self._runtime.get("ocr_language") or os.getenv("OCR_LANGUAGE") or DEFAULT_SETTINGS["ocr_language"])

    @property
    def ocr_enable_table(self) -> bool:
        val = self._runtime.get("ocr_enable_table")
        if val is None:
            val = os.getenv("OCR_ENABLE_TABLE")
        if val is None:
            return True
        return str(val).strip().lower() in {"1", "true", "yes", "on"}

    # ------------------------------------------------------------- switches
    @property
    def enable_duplicate_check(self) -> bool:
        val = self._runtime.get("enable_duplicate_check")
        if val is None:
            return _env_bool("ENABLE_DUPLICATE_CHECK", True)
        return bool(val)

    @property
    def enable_validation(self) -> bool:
        val = self._runtime.get("enable_validation")
        if val is None:
            return _env_bool("ENABLE_VALIDATION", True)
        return bool(val)

    @property
    def enable_tax_intelligence(self) -> bool:
        val = self._runtime.get("enable_tax_intelligence")
        if val is None:
            return _env_bool("ENABLE_TAX_INTELLIGENCE", True)
        return bool(val)

    # ------------------------------------------------------------- runtime
    @staticmethod
    def _load_runtime() -> dict:
        try:
            if SETTINGS_FILE.exists():
                with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                return {k: v for k, v in data.items() if k in DEFAULT_SETTINGS}
        except (OSError, ValueError):
            pass
        return {}

    def save_runtime(self, **kwargs) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        for key, value in kwargs.items():
            if key in DEFAULT_SETTINGS:
                self._runtime[key] = value
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as fh:
                json.dump(self._runtime, fh, indent=2)
        except OSError:
            pass

    def apply_runtime(self, mapping: dict) -> None:
        self._runtime.update({k: v for k, v in mapping.items() if k in DEFAULT_SETTINGS})

    def resolve_ai_mode(self, supports_vision: bool, has_text: bool = False) -> str:
        mode = self.ai_mode
        if mode == "auto":
            # Fast path: documents with a usable text layer are extracted in text
            # mode (no image processing -> much faster on CPU). Vision mode is
            # used only for scans / image-only documents.
            if supports_vision and not has_text:
                return "vision"
            return "text"
        return mode

    def ensure_dirs(self) -> None:
        for folder in (self.data_dir, self.upload_dir, self.export_dir, self.log_dir, self.model_dir, self.tmp_dir, self.preview_dir):
            folder.mkdir(parents=True, exist_ok=True)


def get_config() -> AppConfig:
    if AppConfig._instance is None:
        AppConfig._instance = AppConfig()
    return AppConfig._instance