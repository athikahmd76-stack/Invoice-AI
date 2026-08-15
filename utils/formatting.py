"""Numeric, currency and date formatting helpers (Indian invoice conventions)."""

from __future__ import annotations

import datetime as dt
import math
import re
from typing import Any

_NUM_CLEAN = re.compile(r"[^0-9.\-]")


def to_float(value: Any) -> float | None:
    """Parse a value like '₹ 1,42,100.50', '12100', 12100.0, '-8' into a float."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in ("amount", "value", "rate", "total"):
            if key in value and value[key] is not None:
                result = to_float(value[key])
                if result is not None:
                    return result
        return None
    text = str(value).strip()
    if not text or text.lower() in {"null", "none", "n/a", "-", "--", "na"}:
        return None
    text = text.replace("₹", "").replace("Rs.", "").replace("Rs", "").replace("INR", "").replace(",", "").replace("%", "").strip()
    text = re.sub(r"[^\d.\-]", "", text)
    if not text or text in {"-", ".", "-."}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def to_int(value: Any) -> int | None:
    result = to_float(value)
    if result is None:
        return None
    return int(round(result))


def to_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"null", "none", "n/a", "-", "--"}:
        return None
    return text


def parse_date(value: Any) -> str | None:
    """Return an ISO date string (YYYY-MM-DD) or None."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 20000101:
        text = str(int(value))
        if len(text) == 8:
            return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    text = str(value).strip().strip("'\"").replace("/", "-").replace(".", "-")
    text = re.sub(r"\s+", " ", text)
    if not text or text.lower() in {"null", "none", "n/a"}:
        return None
    candidates = []
    if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", text):
        candidates.append("%Y-%m-%d")
        candidates.append("%Y-%m-%d %H:%M:%S")
    elif re.fullmatch(r"\d{1,2}-\d{1,2}-\d{4}", text):
        candidates.append("%d-%m-%Y")
        candidates.append("%m-%d-%Y")
    elif re.fullmatch(r"\d{1,2}-\d{1,2}-\d{2}", text):
        candidates.append("%d-%m-%y")
        candidates.append("%m-%d-%y")
    for fmt in candidates:
        try:
            return dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    for fmt in ("%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%d-%b-%Y", "%d-%b-%y", "%d %b %y"):
        try:
            return dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_percent(value: Any) -> float | None:
    """Parse rate text like '18%', '9+9', '9 + 9', 'CGST 9%' into a rate."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip()
    if not text or text.lower() in {"null", "none", "n/a", "-"}:
        return None
    text = text.replace("%", "")
    match = re.search(r"(\d+(?:\.\d+)?)\s*[+]?\s*(\d+(?:\.\d+)?)", text)
    if match:
        a, b = float(match.group(1)), float(match.group(2))
        return a + b if abs(a - b) < 0.51 else a
    match = re.search(r"\d+(?:\.\d+)?", text)
    if match:
        return float(match.group(0))
    return None


def round_money(value: float | None, digits: int = 2) -> float | None:
    if value is None:
        return None
    if math.isnan(value) or math.isinf(value):
        return None
    return round(value + 1e-9, digits)


def inr(value: float | None, decimals: int = 2) -> str:
    if value is None:
        return "—"
    return "₹ " + format_inr(value, decimals)


def format_inr(value: float, decimals: int = 2) -> str:
    """Indian digit-grouping format: 142100.5 -> 1,42,100.50"""
    negative = value < 0
    value = abs(value)
    text = f"{value:.{decimals}f}"
    if "." in text:
        whole, frac = text.split(".")
    else:
        whole, frac = text, ""
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts) + "," + tail
    result = whole if not frac else f"{whole}.{frac}"
    return "-" + result if negative else result


def lakhs(value: float | None) -> str:
    if value is None:
        return "₹ 0.00 Lakh"
    return f"₹ {value / 100000:.2f} Lakh"


def format_number(value: float | None) -> str:
    if value is None:
        return "—"
    if value == int(value):
        return f"{int(value):,}"
    return f"{value:,.2f}"


def format_date(value: str | None, fmt: str = "%d %b %Y") -> str:
    if not value:
        return "—"
    try:
        return dt.date.fromisoformat(str(value)).strftime(fmt)
    except ValueError:
        return str(value)


def confidence_label(score: Any) -> str:
    score = to_float(score)
    if score is None:
        return "Unknown"
    if score >= 90:
        return "High"
    if score >= 70:
        return "Medium"
    return "Low"


def confidence_color(label: str) -> str:
    return {"High": "#1f8b4c", "Medium": "#b8860b", "Low": "#c62828", "Unknown": "#888888"}.get(label, "#888888")


def clean_text(value: Any) -> str | None:
    text = to_str(value)
    if text is None:
        return None
    text = re.sub(r"\s{2,}", " ", text)
    return text


def normalize_fingerprint(value: Any) -> str:
    text = to_str(value)
    if not text:
        return ""
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def file_size_label(size_bytes: int) -> str:
    if size_bytes is None:
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.0f} {unit}" if unit == "B" else f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"