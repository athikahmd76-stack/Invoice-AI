"""Repository layer: all database operations used by UI and services."""

from __future__ import annotations

import datetime as dt
from typing import Any, Iterable

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session, joinedload

from database.database import json_dumps, json_loads
from database.models import Invoice, InvoiceItem, ProcessingLog, TaxDetail, UploadedFile, Vendor

VALID_STATUSES = {"Extracted", "Validated", "Needs Review", "Failed", "Imported"}
VALID_DUP_STATUSES = {"None", "Duplicate", "Ignored"}


def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


# ====================================================================== vendor

def upsert_vendor(session: Session, vendor_data: dict) -> Vendor | None:
    gstin = (vendor_data.get("gstin") or "").strip().upper() or None
    name = (vendor_data.get("name") or "").strip() or None
    if not gstin and not name:
        return None
    vendor = None
    if gstin:
        vendor = session.execute(select(Vendor).where(Vendor.gstin == gstin)).scalar_one_or_none()
    if vendor is None and name:
        vendor = session.execute(select(Vendor).where(func.lower(Vendor.name) == name.lower())).scalar_one_or_none()
    if vendor is None:
        vendor = Vendor(gstin=gstin)
        session.add(vendor)
    for field in ("name", "legal_name", "pan", "address", "phone", "email"):
        value = vendor_data.get(field)
        if value:
            setattr(vendor, field, str(value).strip())
    return vendor


def list_vendors(session: Session, search: str | None = None) -> list[Vendor]:
    query = select(Vendor)
    if search:
        like = f"%{search.strip()}%"
        query = query.where(or_(Vendor.name.like(like), Vendor.gstin.like(like), Vendor.pan.like(like)))
    return list(session.execute(query.order_by(Vendor.name)).scalars().all())


def vendor_report(session: Session) -> list[dict]:
    rows = session.execute(
        select(
            Vendor.name,
            func.count(Invoice.id).label("invoice_count"),
            func.sum(Invoice.grand_total).label("total_value"),
            func.sum(func.coalesce(Invoice.cgst, 0) + func.coalesce(Invoice.sgst, 0) + func.coalesce(Invoice.igst, 0) + func.coalesce(Invoice.utgst, 0) + func.coalesce(Invoice.cess, 0)).label("total_tax"),
            func.avg(Invoice.grand_total).label("avg_value"),
        )
        .join(Invoice, Invoice.vendor_id == Vendor.id)
        .group_by(Vendor.id)
        .order_by(func.sum(Invoice.grand_total).desc().nullslast())
    ).all()
    return [{"vendor": r[0] or "Unknown", "invoice_count": int(r[1] or 0), "total_value": r[2], "total_tax": r[3], "avg_value": r[4]} for r in rows]


# ===================================================================== invoice

def _invoice_dump(invoice: Invoice) -> dict:
    return {
        "id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "invoice_date": invoice.invoice_date,
        "vendor_name": invoice.vendor_name,
        "po_number": invoice.po_number,
        "grand_total": invoice.grand_total,
        "tax": (invoice.cgst or 0) + (invoice.sgst or 0) + (invoice.igst or 0) + (invoice.utgst or 0) + (invoice.cess or 0),
        "status": invoice.status,
        "validation_status": invoice.validation_status,
        "duplicate_status": invoice.duplicate_status,
    }


def create_invoice(
    session: Session,
    parsed: dict,
    validation: dict,
    duplicate: dict,
    meta: dict,
) -> Invoice:
    vendor_data = parsed.get("vendor") or {}
    invoice_data = parsed.get("invoice") or {}
    buyer_data = parsed.get("buyer") or {}
    totals = parsed.get("totals") or {}
    taxes = parsed.get("taxes") or {}
    payment = parsed.get("payment") or {}
    items = parsed.get("items") or []
    tax_details = parsed.get("tax_details") or []
    confidence = parsed.get("confidence") or {}

    vendor = upsert_vendor(session, vendor_data)

    invoice = Invoice(
        vendor_id=vendor.id if vendor else None,
        vendor_name=invoice_data.get("vendor_name") or (vendor_data.get("name") if vendor else None),
        vendor_gstin=vendor_data.get("gstin"),
        vendor_pan=vendor_data.get("pan"),
        vendor_address=vendor_data.get("address"),
        vendor_phone=vendor_data.get("phone"),
        vendor_email=vendor_data.get("email"),
        invoice_number=invoice_data.get("number"),
        invoice_date=invoice_data.get("date"),
        due_date=invoice_data.get("due_date"),
        po_number=invoice_data.get("po_number"),
        po_date=invoice_data.get("po_date"),
        grn_number=invoice_data.get("grn_number"),
        delivery_note=invoice_data.get("delivery_note"),
        eway_bill=invoice_data.get("eway_bill"),
        currency=invoice_data.get("currency") or "INR",
        payment_terms=invoice_data.get("payment_terms"),
        place_of_supply=invoice_data.get("place_of_supply"),
        buyer_name=buyer_data.get("name"),
        buyer_address=buyer_data.get("address"),
        buyer_gstin=buyer_data.get("gstin"),
        shipping_address=buyer_data.get("shipping_address"),
        billing_address=buyer_data.get("billing_address"),
        subtotal=totals.get("subtotal"),
        discount=totals.get("discount"),
        taxable_value=taxes.get("taxable_value"),
        cgst=taxes.get("cgst"),
        sgst=taxes.get("sgst"),
        igst=taxes.get("igst"),
        utgst=taxes.get("utgst"),
        cess=taxes.get("cess"),
        round_off=totals.get("round_off"),
        grand_total=totals.get("grand_total"),
        amount_paid=totals.get("amount_paid"),
        balance_due=totals.get("balance_due"),
        bank_name=payment.get("bank_name"),
        bank_account=payment.get("bank_account"),
        ifsc=payment.get("ifsc"),
        upi=payment.get("upi"),
        status=validation.get("status") or "Extracted",
        validation_status=validation.get("validation_status"),
        validation_checks=json_dumps(validation.get("checks")),
        confidence_json=json_dumps(confidence),
        duplicate_status=duplicate.get("status") or "None",
        duplicate_of_id=duplicate.get("duplicate_of_id"),
        fingerprint=duplicate.get("fingerprint"),
        file_hash=meta.get("file_hash"),
        original_filename=meta.get("original_filename"),
        file_path=meta.get("file_path"),
        page_count=meta.get("page_count"),
        preview_path=meta.get("preview_path"),
        ai_model=meta.get("ai_model"),
        ai_mode=meta.get("ai_mode"),
        ocr_engine=meta.get("ocr_engine"),
        raw_json=json_dumps(parsed),
        processed_at=now_iso(),
    )
    session.add(invoice)

    for i, item in enumerate(items):
        invoice.items.append(
            InvoiceItem(
                line_no=item.get("line_no") if item.get("line_no") is not None else i + 1,
                product_name=item.get("product_name"),
                product_description=item.get("product_description"),
                sku=item.get("sku"),
                product_code=item.get("product_code"),
                item_code=item.get("item_code"),
                ean=item.get("ean"),
                upc=item.get("upc"),
                barcode=item.get("barcode"),
                hsn=item.get("hsn"),
                quantity=item.get("quantity"),
                free_quantity=item.get("free_quantity"),
                uom=item.get("uom"),
                unit_price=item.get("unit_price"),
                mrp=item.get("mrp"),
                discount_pct=item.get("discount_pct"),
                discount_amount=item.get("discount_amount"),
                taxable_value=item.get("taxable_value"),
                gst_pct=item.get("gst_pct"),
                cgst_pct=item.get("cgst_pct"),
                sgst_pct=item.get("sgst_pct"),
                igst_pct=item.get("igst_pct"),
                cgst_amount=item.get("cgst_amount"),
                sgst_amount=item.get("sgst_amount"),
                igst_amount=item.get("igst_amount"),
                cess_amount=item.get("cess_amount"),
                line_total=item.get("line_total"),
            )
        )
        if i > 5000:
            break

    for td in tax_details:
        invoice.tax_details.append(
            TaxDetail(
                tax_type=td.get("tax_type"),
                taxable_value=td.get("taxable_value"),
                rate=td.get("rate"),
                amount=td.get("amount"),
                source=td.get("source", "extracted"),
            )
        )

    session.flush()
    return invoice


def get_invoice(session: Session, invoice_id: int) -> Invoice | None:
    return session.execute(
        select(Invoice).options(joinedload(Invoice.items), joinedload(Invoice.tax_details)).where(Invoice.id == invoice_id)
    ).scalars().first()


def update_invoice_fields(session: Session, invoice_id: int, fields: dict) -> None:
    invoice = get_invoice(session, invoice_id)
    if invoice is None:
        return
    allowed = {
        "vendor_name", "vendor_gstin", "vendor_pan", "vendor_address", "vendor_phone", "vendor_email",
        "invoice_number", "invoice_date", "due_date", "po_number", "po_date", "grn_number", "delivery_note",
        "eway_bill", "currency", "payment_terms", "place_of_supply",
        "buyer_name", "buyer_address", "buyer_gstin",
        "subtotal", "discount", "taxable_value", "cgst", "sgst", "igst", "utgst", "cess", "round_off",
        "grand_total", "amount_paid", "balance_due",
        "bank_name", "bank_account", "ifsc", "upi", "status",
    }
    changed = False
    for key, value in fields.items():
        if key in allowed:
            setattr(invoice, key, value)
            if key == "vendor_gstin":
                invoice.fingerprint = None
            changed = True
    if changed:
        session.flush()


def update_invoice_items(session: Session, invoice_id: int, items: Iterable[dict]) -> None:
    invoice = get_invoice(session, invoice_id)
    if invoice is None:
        return
    for old in list(invoice.items):
        session.delete(old)
    for i, item in enumerate(items):
        invoice.items.append(
            InvoiceItem(
                line_no=item.get("line_no") if item.get("line_no") is not None else i + 1,
                product_name=item.get("product_name"),
                product_description=item.get("product_description"),
                sku=item.get("sku"),
                product_code=item.get("product_code"),
                item_code=item.get("item_code"),
                ean=item.get("ean"),
                upc=item.get("upc"),
                barcode=item.get("barcode"),
                hsn=item.get("hsn"),
                quantity=item.get("quantity"),
                free_quantity=item.get("free_quantity"),
                uom=item.get("uom"),
                unit_price=item.get("unit_price"),
                mrp=item.get("mrp"),
                discount_pct=item.get("discount_pct"),
                discount_amount=item.get("discount_amount"),
                taxable_value=item.get("taxable_value"),
                gst_pct=item.get("gst_pct"),
                cgst_pct=item.get("cgst_pct"),
                sgst_pct=item.get("sgst_pct"),
                igst_pct=item.get("igst_pct"),
                cgst_amount=item.get("cgst_amount"),
                sgst_amount=item.get("sgst_amount"),
                igst_amount=item.get("igst_amount"),
                cess_amount=item.get("cess_amount"),
                line_total=item.get("line_total"),
            )
        )
    session.flush()


def delete_invoice(session: Session, invoice_id: int) -> None:
    session.execute(ProcessingLog.__table__.update().where(ProcessingLog.invoice_id == invoice_id).values(invoice_id=None))
    invoice = session.execute(select(Invoice).where(Invoice.id == invoice_id)).scalar_one_or_none()
    if invoice:
        session.delete(invoice)
    session.flush()


def set_invoice_status(session: Session, invoice_id: int, status: str, **extra) -> None:
    if status not in VALID_STATUSES:
        return
    invoice = session.get(Invoice, invoice_id)
    if invoice:
        invoice.status = status
        for k, v in extra.items():
            setattr(invoice, k, v)
        session.flush()


def set_duplicate_status(session: Session, invoice_id: int, status: str) -> None:
    status = status if status in VALID_DUP_STATUSES else "None"
    invoice = session.get(Invoice, invoice_id)
    if invoice:
        invoice.duplicate_status = status
        if status != "Duplicate":
            invoice.duplicate_of_id = None
        session.flush()


def _invoice_filters(query, filters: dict):
    if filters.get("search"):
        like = f"%{filters['search'].strip()}%"
        query = query.where(
            or_(
                Invoice.invoice_number.like(like),
                Invoice.vendor_name.like(like),
                Invoice.po_number.like(like),
                Invoice.vendor_gstin.like(like),
                Invoice.buyer_name.like(like),
                Invoice.invoice_number.like(like),
            )
        )
    if filters.get("invoice_no"):
        query = query.where(Invoice.invoice_number.like(f"%{filters['invoice_no'].strip()}%"))
    if filters.get("vendor"):
        query = query.where(Invoice.vendor_name.like(f"%{filters['vendor'].strip()}%"))
    if filters.get("po_number"):
        query = query.where(Invoice.po_number.like(f"%{filters['po_number'].strip()}%"))
    if filters.get("gstin"):
        query = query.where(Invoice.vendor_gstin.like(f"%{filters['gstin'].strip()}%"))
    if filters.get("date_from"):
        query = query.where(Invoice.invoice_date >= str(filters["date_from"]))
    if filters.get("date_to"):
        query = query.where(Invoice.invoice_date <= str(filters["date_to"]))
    if filters.get("status"):
        query = query.where(Invoice.status == filters["status"])
    if filters.get("validation_status"):
        query = query.where(Invoice.validation_status == filters["validation_status"])
    if filters.get("duplicate_status"):
        query = query.where(Invoice.duplicate_status == filters["duplicate_status"])
    if filters.get("sku") or filters.get("product"):
        term = filters.get("sku") or filters.get("product")
        like = f"%{term.strip()}%"
        sub = select(InvoiceItem.invoice_id).where(or_(InvoiceItem.sku.like(like), InvoiceItem.product_name.like(like))).distinct().subquery()
        query = query.where(Invoice.id.in_(select(sub.c.invoice_id)))
    return query


def query_invoices(session: Session, filters: dict | None = None, page: int = 1, page_size: int = 25) -> tuple[int, list[dict]]:
    filters = filters or {}
    base = select(Invoice)
    base = _invoice_filters(base, filters)
    count = session.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    rows = (
        session.execute(
            base.order_by(Invoice.invoice_date.desc().nullslast(), Invoice.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return count, [_invoice_dump(inv) for inv in rows]


def recent_invoices(session: Session, limit: int = 8) -> list[dict]:
    rows = (
        session.execute(select(Invoice).order_by(Invoice.id.desc()).limit(limit))
        .scalars()
        .all()
    )
    return [_invoice_dump(inv) for inv in rows]


def distinct_vendors(session: Session) -> list[str]:
    rows = session.execute(select(Invoice.vendor_name).where(Invoice.vendor_name.isnot(None)).distinct().order_by(Invoice.vendor_name)).scalars().all()
    return [str(r) for r in rows]


def invoice_rows_for_export(session: Session, filters: dict | None = None) -> list[dict]:
    filters = filters or {}
    query = select(Invoice)
    query = _invoice_filters(query, filters)
    invoices = session.execute(query.order_by(Invoice.invoice_date.desc().nullslast(), Invoice.id.desc())).scalars().all()
    return [_invoice_dump(inv) for inv in invoices]


def invoice_item_rows_for_export(session: Session, filters: dict | None = None) -> list[dict]:
    filters = filters or {}
    inv_query = select(Invoice.id).distinct()
    inv_query = _invoice_filters(inv_query, filters)
    ids = [r[0] for r in session.execute(inv_query).all()]
    if not ids:
        return []
    rows = session.execute(
        select(InvoiceItem, Invoice)
        .join(Invoice, InvoiceItem.invoice_id == Invoice.id)
        .where(InvoiceItem.invoice_id.in_(ids))
        .order_by(InvoiceItem.invoice_id, InvoiceItem.line_no)
    ).all()
    result = []
    for item, invoice in rows:
        result.append({
            "invoice_id": item.invoice_id,
            "invoice_number": invoice.invoice_number if invoice else None,
            "invoice_date": invoice.invoice_date if invoice else None,
            "vendor_name": invoice.vendor_name if invoice else None,
            "line_no": item.line_no,
            "product_name": item.product_name,
            "product_description": item.product_description,
            "sku": item.sku,
            "product_code": item.product_code,
            "item_code": item.item_code,
            "ean": item.ean,
            "upc": item.upc,
            "barcode": item.barcode,
            "hsn": item.hsn,
            "quantity": item.quantity,
            "free_quantity": item.free_quantity,
            "uom": item.uom,
            "unit_price": item.unit_price,
            "mrp": item.mrp,
            "discount_pct": item.discount_pct,
            "discount_amount": item.discount_amount,
            "taxable_value": item.taxable_value,
            "gst_pct": item.gst_pct,
            "cgst_pct": item.cgst_pct,
            "sgst_pct": item.sgst_pct,
            "igst_pct": item.igst_pct,
            "cgst_amount": item.cgst_amount,
            "sgst_amount": item.sgst_amount,
            "igst_amount": item.igst_amount,
            "cess_amount": item.cess_amount,
            "line_total": item.line_total,
        })
    return result


def valid_tax_rows(session: Session) -> list[dict]:
    rows = session.execute(select(TaxDetail).order_by(TaxDetail.invoice_id)).scalars().all()
    return [
        {
            "invoice_id": t.invoice_id,
            "tax_type": t.tax_type,
            "rate": t.rate,
            "taxable_value": t.taxable_value,
            "amount": t.amount,
            "source": t.source,
        }
        for t in rows
    ]


# ================================================================== dashboard

def dashboard_stats(session: Session) -> dict:
    invoice_count = session.execute(select(func.count(Invoice.id))).scalar_one()
    item_count = session.execute(select(func.count(InvoiceItem.id))).scalar_one()
    vendor_count = session.execute(select(func.count(func.distinct(Invoice.vendor_name))).where(Invoice.vendor_name.isnot(None))).scalar_one()
    total_value = session.execute(select(func.sum(Invoice.grand_total))).scalar_one() or 0.0
    total_tax = session.execute(
        select(func.sum(func.coalesce(Invoice.cgst, 0) + func.coalesce(Invoice.sgst, 0) + func.coalesce(Invoice.igst, 0) + func.coalesce(Invoice.utgst, 0) + func.coalesce(Invoice.cess, 0)))
    ).scalar_one() or 0.0
    duplicate_count = session.execute(select(func.count(Invoice.id)).where(Invoice.duplicate_status == "Duplicate")).scalar_one()
    error_count = session.execute(select(func.count(ProcessingLog.id)).where(ProcessingLog.status == "Failed")).scalar_one()
    validated = session.execute(select(func.count(Invoice.id)).where(Invoice.validation_status == "Matched")).scalar_one()
    needs_review = session.execute(select(func.count(Invoice.id)).where(Invoice.status.in_(["Needs Review", "Failed"]))).scalar_one()
    return {
        "invoice_count": invoice_count,
        "item_count": item_count,
        "vendor_count": vendor_count,
        "total_value": float(total_value),
        "total_tax": float(total_tax),
        "duplicate_count": duplicate_count,
        "error_count": error_count,
        "validated": validated,
        "needs_review": needs_review,
    }


def monthly_trend(session: Session) -> list[dict]:
    rows = session.execute(
        select(
            func.substr(Invoice.invoice_date, 1, 7).label("month"),
            func.count(Invoice.id),
            func.sum(Invoice.grand_total),
            func.sum(func.coalesce(Invoice.cgst, 0) + func.coalesce(Invoice.sgst, 0) + func.coalesce(Invoice.igst, 0) + func.coalesce(Invoice.utgst, 0) + func.coalesce(Invoice.cess, 0)),
        )
        .where(Invoice.invoice_date.isnot(None))
        .group_by("month")
        .order_by("month")
    ).all()
    return [{"month": r[0], "invoice_count": int(r[1]), "total_value": r[2], "total_tax": r[3]} for r in rows]


def sku_report(session: Session) -> list[dict]:
    rows = session.execute(
        select(
            InvoiceItem.sku,
            InvoiceItem.product_name,
            func.count(func.distinct(InvoiceItem.invoice_id)).label("invoice_count"),
            func.sum(func.coalesce(InvoiceItem.quantity, 0)).label("quantity"),
            func.sum(func.coalesce(InvoiceItem.line_total, 0)).label("value"),
        )
        .where(InvoiceItem.sku.isnot(None))
        .group_by(InvoiceItem.sku)
        .order_by(func.sum(InvoiceItem.line_total).desc().nullslast())
        .limit(200)
    ).all()
    return [
        {"sku": r[0], "product_name": r[1], "invoice_count": int(r[2]), "quantity": r[3], "value": r[4]}
        for r in rows
    ]


def tax_summary(session: Session) -> dict:
    row = session.execute(
        select(
            func.sum(func.coalesce(Invoice.cgst, 0)),
            func.sum(func.coalesce(Invoice.sgst, 0)),
            func.sum(func.coalesce(Invoice.igst, 0)),
            func.sum(func.coalesce(Invoice.utgst, 0)),
            func.sum(func.coalesce(Invoice.cess, 0)),
        )
    ).one()
    total = (row[0] or 0) + (row[1] or 0) + (row[2] or 0) + (row[3] or 0) + (row[4] or 0)
    return {"cgst": row[0] or 0, "sgst": row[1] or 0, "igst": row[2] or 0, "utgst": row[3] or 0, "cess": row[4] or 0, "total": total}


# ====================================================================== logs

def add_log(session: Session, file_name: str, **fields) -> ProcessingLog:
    log = ProcessingLog(file_name=file_name)
    for key, value in fields.items():
        if hasattr(log, key):
            setattr(log, key, value)
    session.add(log)
    session.flush()
    return log


def update_log(session: Session, log_id: int, **fields) -> None:
    log = session.get(ProcessingLog, log_id)
    if log is None:
        return
    for key, value in fields.items():
        if hasattr(log, key):
            setattr(log, key, value)
    session.flush()


def failed_logs(session: Session, limit: int = 100) -> list[dict]:
    rows = (
        session.execute(select(ProcessingLog).where(ProcessingLog.status == "Failed").order_by(ProcessingLog.id.desc()).limit(limit))
        .scalars()
        .all()
    )
    return [
        {
            "id": log.id,
            "file_name": log.file_name,
            "file_path": log.file_path,
            "status": log.status,
            "error_message": log.error_message,
            "start_time": log.start_time,
        }
        for log in rows
    ]


def logs_for_export(session: Session, limit: int = 2000) -> list[dict]:
    rows = session.execute(select(ProcessingLog).order_by(ProcessingLog.id.desc()).limit(limit)).scalars().all()
    return [
        {
            "file_name": log.file_name,
            "upload_date": log.upload_date,
            "start_time": log.start_time,
            "end_time": log.end_time,
            "duration_ms": log.duration_ms,
            "ocr_status": log.ocr_status,
            "ai_status": log.ai_status,
            "validation_status": log.validation_status,
            "model_used": log.model_used,
            "status": log.status,
            "error_message": log.error_message,
            "retry_count": log.retry_count,
        }
        for log in rows
    ]


def mark_retry(session: Session, log_id: int) -> None:
    log = session.get(ProcessingLog, log_id)
    if log:
        log.retry_count += 1
        log.status = "Pending"
        log.error_message = None
        session.flush()


# =============================================================== uploaded files

def uploaded_by_hash(session: Session, file_hash: str) -> UploadedFile | None:
    return session.execute(select(UploadedFile).where(UploadedFile.file_hash == file_hash)).scalars().first()


def add_uploaded_file(session: Session, file_path, original_name, stored_name, size_bytes, file_hash, invoice_id=None) -> UploadedFile:
    record = UploadedFile(
        original_name=original_name,
        stored_name=stored_name,
        file_path=str(file_path),
        size_bytes=size_bytes,
        file_hash=file_hash,
        extension=file_path.suffix.lower(),
        invoice_id=invoice_id,
        status="Processed" if invoice_id else "Uploaded",
    )
    session.add(record)
    session.flush()
    return record


def invoices_by_hash(session: Session, file_hash: str) -> list[Invoice]:
    return list(session.execute(select(Invoice).where(Invoice.file_hash == file_hash)).scalars().all())


def clear_all_data(session: Session) -> dict:
    """Permanently delete every persisted record (FK-safe order). Returns per-table counts."""
    counts: dict[str, int] = {}
    for model in (TaxDetail, InvoiceItem, Invoice, ProcessingLog, UploadedFile, Vendor):
        counts[model.__tablename__] = session.execute(delete(model)).rowcount
    session.flush()
    return counts