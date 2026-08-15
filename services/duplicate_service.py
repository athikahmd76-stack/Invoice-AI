"""Duplicate invoice detection via vendor GSTIN + invoice number fingerprint."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import Invoice
from utils.formatting import normalize_fingerprint


def compute_fingerprint(vendor_gstin: str | None, invoice_number: str | None) -> str | None:
    gstin = normalize_fingerprint(vendor_gstin)
    number = normalize_fingerprint(invoice_number)
    if not gstin and not number:
        return None
    if gstin and number:
        return f"{gstin}|{number}"
    return number or gstin


def check_duplicate(session: Session, parsed: dict, exclude_invoice_id: int | None = None) -> dict:
    """Look for an existing invoice with the same fingerprint."""
    invoice_data = parsed.get("invoice") or {}
    vendor_data = parsed.get("vendor") or {}
    gstin = vendor_data.get("gstin")
    number = invoice_data.get("number")
    fingerprint = compute_fingerprint(gstin, number)

    result = {
        "status": "None",
        "fingerprint": fingerprint,
        "duplicate_of_id": None,
        "matched_invoice": None,
    }
    if not fingerprint:
        return result

    query = select(Invoice).where(Invoice.fingerprint == fingerprint)
    if exclude_invoice_id:
        query = query.where(Invoice.id != exclude_invoice_id)
    existing = session.execute(query.limit(1)).scalars().first()
    if existing:
        result["status"] = "Duplicate"
        result["duplicate_of_id"] = existing.id
        result["matched_invoice"] = {
            "id": existing.id,
            "invoice_number": existing.invoice_number,
            "invoice_date": existing.invoice_date,
            "vendor_name": existing.vendor_name,
            "grand_total": existing.grand_total,
            "status": existing.status,
        }
    return result


def update_fingerprint(session: Session, invoice_id: int) -> None:
    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        return
    invoice.fingerprint = compute_fingerprint(invoice.vendor_gstin, invoice.invoice_number)
    session.flush()