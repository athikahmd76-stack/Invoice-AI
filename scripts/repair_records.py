"""Re-parse stored invoice PDFs with the deterministic table parser and update
existing records in place (fixes dates, addresses, EANs, quantities, prices).

Usage:  python scripts/repair_records.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pdfplumber  # noqa: E402

from config import get_config  # noqa: E402
from database import repository  # noqa: E402
from database.database import Database, json_dumps  # noqa: E402
from database.models import Invoice, InvoiceItem, TaxDetail  # noqa: E402
from services import table_parser  # noqa: E402
from services.duplicate_service import update_fingerprint  # noqa: E402
from sqlalchemy import select  # noqa: E402


def extract_text(path: Path) -> str:
    with pdfplumber.open(path) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


def apply(session, invoice: Invoice, parsed: dict) -> None:
    vendor = repository.upsert_vendor(session, parsed.get("vendor") or {})
    invoice.vendor_id = vendor.id if vendor else None
    invoice.vendor_name = parsed["vendor"].get("name") or parsed["invoice"].get("vendor_name")
    invoice.vendor_gstin = parsed["vendor"].get("gstin")
    invoice.vendor_pan = parsed["vendor"].get("pan")
    invoice.vendor_address = parsed["vendor"].get("address")

    invoice.invoice_number = parsed["invoice"].get("number")
    invoice.invoice_date = parsed["invoice"].get("date")
    invoice.due_date = parsed["invoice"].get("due_date")
    invoice.po_number = parsed["invoice"].get("po_number")
    invoice.po_date = parsed["invoice"].get("po_date")

    invoice.buyer_name = parsed["buyer"].get("name")
    invoice.buyer_gstin = parsed["buyer"].get("gstin")
    invoice.buyer_address = parsed["buyer"].get("address")
    invoice.billing_address = parsed["buyer"].get("billing_address")
    invoice.shipping_address = parsed["buyer"].get("shipping_address")

    invoice.subtotal = parsed["totals"].get("subtotal")
    invoice.discount = parsed["totals"].get("discount")
    invoice.round_off = parsed["totals"].get("round_off")
    invoice.grand_total = parsed["totals"].get("grand_total")

    invoice.taxable_value = parsed["taxes"].get("taxable_value")
    invoice.cgst = parsed["taxes"].get("cgst")
    invoice.sgst = parsed["taxes"].get("sgst")
    invoice.igst = parsed["taxes"].get("igst")
    invoice.utgst = parsed["taxes"].get("utgst")
    invoice.cess = parsed["taxes"].get("cess")

    invoice.raw_json = json_dumps(parsed)

    for old in list(invoice.items):
        session.delete(old)
    for i, item in enumerate(parsed.get("items") or []):
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
    for old in list(invoice.tax_details):
        session.delete(old)
    for td in parsed.get("tax_details") or []:
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
    update_fingerprint(session, invoice.id)
    session.flush()


def main() -> None:
    config = get_config()
    db = Database(config)
    updated = skipped = 0
    with db.session() as session:
        invoices = session.execute(select(Invoice).order_by(Invoice.id)).scalars().all()
        for invoice in invoices:
            path = Path(invoice.file_path) if invoice.file_path else None
            if not path or not path.exists():
                print(f"SKIP #{invoice.id} {invoice.invoice_number}: file missing ({invoice.file_path})")
                skipped += 1
                continue
            parsed = table_parser.parse(extract_text(path))
            if parsed is None:
                print(f"SKIP #{invoice.id} {invoice.invoice_number}: layout not matched ({path.name})")
                skipped += 1
                continue
            apply(session, invoice, parsed)
            print(
                f"FIXED #{invoice.id} {parsed['invoice']['number']} | date={parsed['invoice']['date']} | "
                f"po={parsed['invoice']['po_number']} | items={len(parsed['items'])} | "
                f"grand={parsed['totals']['grand_total']} | file={path.name}"
            )
            updated += 1
        session.commit()
    print(f"\nDone: {updated} updated, {skipped} skipped")


if __name__ == "__main__":
    main()