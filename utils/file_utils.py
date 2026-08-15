"""Safe file handling: sanitization, validation, hashing, storage."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def extension_of(filename: str) -> str:
    return Path(filename or "").suffix.lower()


def is_allowed_file(filename: str) -> bool:
    return extension_of(filename) in ALLOWED_EXTENSIONS


def is_image_file(filename: str) -> bool:
    return extension_of(filename) in IMAGE_EXTENSIONS


def sanitize_filename(filename: str, max_length: int = 80) -> str:
    """Strip path components and dangerous characters; never trust upload names."""
    if not filename:
        return "unnamed_file"
    name = Path(filename.replace("\\", "/")).name
    name = re.sub(r"[^\w.\- ]", "_", name)
    name = re.sub(r"\s+", "_", name)
    name = name.strip("._")
    if len(name) > max_length:
        stem = Path(name).stem[:60]
        suffix = Path(name).suffix[:10]
        name = stem + suffix
    return name or "unnamed_file"


def unique_path(directory: Path, filename: str) -> Path:
    """Return a non-colliding path inside directory."""
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / filename
    counter = 2
    while candidate.exists():
        candidate = directory / f"{Path(filename).stem}_{counter}{Path(filename).suffix}"
        counter += 1
    return candidate


def file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def check_size(bytes_size: int, max_file_size_mb: int) -> tuple[bool, str]:
    limit = max_file_size_mb * 1024 * 1024
    if bytes_size > limit:
        return False, f"File exceeds the maximum size of {max_file_size_mb} MB"
    return True, ""


def validate_filename(filename: str, max_file_size_mb: int, size_bytes: int = 0) -> tuple[bool, str]:
    if not is_allowed_file(filename):
        return False, f"Unsupported file type. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
    if size_bytes and not check_size(size_bytes, max_file_size_mb)[0]:
        return False, check_size(size_bytes, max_file_size_mb)[1]
    return True, ""


def save_upload(filename: str, data: bytes, upload_dir: Path) -> Path:
    safe = sanitize_filename(filename)
    path = unique_path(upload_dir, safe)
    if isinstance(data, str):
        data = data.encode("utf-8")
    path.write_bytes(data)
    return path


def safe_read_binary(path: Path) -> bytes:
    return path.read_bytes()