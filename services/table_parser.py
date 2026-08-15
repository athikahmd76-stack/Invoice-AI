"""Deterministic table parser for fixed-layout invoice PDFs (text layer).

Recognizes the Gadget League "Consolidated" e-invoice layout used by the
current vendor set and extracts every line item exactly from the PDF text:
invoice number/date, PO, full Bill-To address, SKU, 13-digit EAN, quantity,
unit price, taxable amount, tax rate, IGST and grand total.

Returns the canonical nested structure (same shape as
invoice_parser.normalize_ai_output) or None when the layout is not
recognized, so callers can fall back to the AI pipeline.
"""

from __future__ import annotations

import re
from typing import Any

from utils import formatting as fmt

ITEM_HEADER = "Sl Description of Goods HSN/SAC Part No. MRP Qty. Price per Amount"

_ITEM_RE = re.compile(
    r"^(\d+)\s+(.*?)\s+(\d{6,8})\s+([A-Za-z0-9][A-Za-z0-9\-\(\)\/\.]*)\s+"
    r"([\d,]+)\s+([\d,]+)\s+([\d,]+\.\d{2})\s+([A-Z]{2,4})\s+([\d,]+\.\d{2})$"
)
_EAN_RE = re.compile(r"^\d{13}$")
_AMOUNT_RE = re.compile(r"^[\d,]+\.\d{2}$")
_DATE_RE = re.compile(r"^\d{1,2}-[A-Za-z]{3}-\d{2}$")
_TAX_TABLE_RE = re.compile(r"^([\d,]+\.\d{2})\s+(\d{1,2}(?:\.\d+)?)%\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})$")
_ROUND_RE = re.compile(r"^(?:Less\s*:\s*)?Round Off\s*\(?-?\)?\s*([\d,]+\.\d{2})$")
_TOTAL_RE = re.compile(r"^Total\s+([\d,]+)\s+[A-Z]{2,4}\s+.*?([\d,]+\.\d{2})$")
_VENDOR_RE = re.compile(r"^(.*?)\s+Invoice No\.\s*Dated$")
_INV_LINE_RE = re.compile(r"^(\S+)\s+(\d{1,2}-[A-Za-z]{3}-\d{2})$")
_PHONE_RE = re.compile(r"[Mm]\.?\s*[\d,\s/\-]{6,}")
_GSTIN_SUFFIX_RE = re.compile(r"\s*GSTIN/UIN\s*:\s*\S+")
_GSTIN_RE = re.compile(r"GSTIN/UIN\s*:\s*([A-Z0-9]{15})")
_CID_RE = re.compile(r"\(cid:\d+\)")


def _num(value: str | None) -> float | None:
    return fmt.to_float(value)


def _date(value: str | None) -> str | None:
    return fmt.parse_date(value)


def _clean(value: str) -> str:
    return fmt.clean_text(value) or ""


def parse(text: str) -> dict | None:
    """Parse a Gadget League consolidated invoice text layer, or return None.

    Two text layouts are supported because the two text extractors used across
    the pipeline differ:
      * PyMuPDF ``get_text("text")`` (what ``ocr_service`` feeds us) emits each
        PDF cell on its own line, with the numeric columns in right-to-left
        order (Amount, UoM, Price, Qty, MRP, Part No., HSN, EAN).
      * pdfplumber joins the cells back into full rows.
    """
    if not text:
        return None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    # Strip page markers that ocr_service inserts between pages.
    lines = [ln for ln in lines if not re.match(r"^--- PAGE \d+ ---$", ln) and not re.match(r"^\(Page\s*\d+\)$", ln)]
    if not lines:
        return None
    if ITEM_HEADER in text:
        return _parse_row(lines)
    if _is_flat_layout(lines):
        return _parse_flat(lines)
    return None


def _is_flat_layout(lines: list[str]) -> bool:
    """Detect the flattened (one-cell-per-line) Gadget League layout."""
    present = set(lines)
    needed = {"Sl", "Description of Goods", "HSN/SAC", "Part No.", "MRP", "Qty.", "Price", "Amount"}
    return needed.issubset(present)


def _parse_row(lines: list[str]) -> dict | None:
    vendor = _parse_vendor(lines)
    invoice = _parse_invoice(lines, vendor.get("name"))
    buyer = _parse_buyer(lines)
    items, taxable_total = _parse_items(lines)
    totals = _parse_totals(lines, taxable_total)

    if not items:
        return None
    # Confidence gate: item amounts must reconcile with the tax-table taxable
    # value when one is present, otherwise refuse and fall back to AI.
    table_taxable = totals.get("table_taxable")
    if table_taxable is not None and abs(table_taxable - taxable_total) > 1.0:
        return None

    rate = totals.get("rate")
    for item in items:
        if item.get("gst_pct") is None:
            item["gst_pct"] = rate

    return {
        "vendor": vendor,
        "invoice": invoice,
        "buyer": buyer,
        "items": items,
        "taxes": {
            "taxable_value": fmt.round_money(taxable_total) if taxable_total is not None else None,
            "cgst": None,
            "sgst": None,
            "igst": fmt.round_money(totals.get("igst")),
            "utgst": None,
            "cess": None,
        },
        "totals": {
            "subtotal": fmt.round_money(taxable_total) if taxable_total is not None else None,
            "discount": None,
            "round_off": fmt.round_money(totals.get("round_off")),
            "grand_total": fmt.round_money(totals.get("grand_total")),
            "amount_paid": None,
            "balance_due": None,
        },
        "payment": {},
        "confidence": {},
        "source_confidence": {},
        "tax_details": [
            {
                "tax_type": "IGST",
                "amount": fmt.round_money(totals.get("igst")),
                "rate": totals.get("rate"),
                "taxable_value": fmt.round_money(taxable_total) if taxable_total is not None else None,
                "source": "extracted",
            }
        ]
        if totals.get("igst")
        else [],
    }


# ------------------------------------------------------------- flattened layout

_FLAT_ITEM_START_RE = re.compile(r"^(\d+)\s+(\S.*)$")
_FLAT_INT_RE = re.compile(r"^[\d,]+$")
_FLAT_UOM_RE = re.compile(r"^[A-Z]{2,4}$")
_FLAT_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-\(\)\/\.]*$")
_FLAT_RATE_RE = re.compile(r"^(\d{1,2}(?:\.\d+)?)%$")


def _flat_vendor(lines: list[str]) -> dict:
    result: dict[str, Any] = {"name": None, "legal_name": None, "gstin": None, "pan": None, "address": None, "phone": None, "email": None}
    for i, ln in enumerate(lines):
        if ln != "e-Invoice" and not ln.startswith("Tax Invoice e-Invoice"):
            continue
        if i + 1 < len(lines):
            result["name"] = _clean(lines[i + 1]) or None
            addr = []
            for j in range(i + 2, len(lines)):
                nxt = lines[j]
                if nxt.startswith(("GSTIN/UIN", "State Name", "CIN:", "E-Mail", "PAN No")):
                    break
                addr.append(nxt)
            if addr:
                result["address"] = _clean(" ".join(addr)) or None
        break
    for ln in lines:
        g = _GSTIN_RE.search(ln)
        if g:
            result["gstin"] = g.group(1)
            break
    return result


def _flat_invoice(lines: list[str], vendor_name: str | None) -> dict:
    result: dict[str, Any] = {
        "number": None, "date": None, "due_date": None, "po_number": None, "po_date": None,
        "grn_number": None, "delivery_note": None, "eway_bill": None, "currency": "INR",
        "payment_terms": None, "place_of_supply": None, "vendor_name": vendor_name,
    }
    for i, ln in enumerate(lines):
        if ln == "Invoice No." and i + 1 < len(lines):
            result["number"] = _clean(lines[i + 1]) or None
        elif ln == "Dated" and i + 1 < len(lines):
            mm = _DATE_RE.match(lines[i + 1])
            if mm:
                result["date"] = _date(mm.group(0))
        elif ln == "PO. No." and i + 1 < len(lines):
            nxt = lines[i + 1]
            if not nxt.startswith(("Dated", "Docket", "PO.Date", "Invoice No.")):
                result["po_number"] = _clean(nxt) or None
        elif ln == "PO.Date" and i + 1 < len(lines):
            mm = _DATE_RE.match(lines[i + 1])
            if mm:
                result["po_date"] = _date(mm.group(0))
    return result


def _flat_buyer(lines: list[str]) -> dict:
    result: dict[str, Any] = {"name": None, "gstin": None, "address": None, "shipping_address": None, "billing_address": None}
    for i, ln in enumerate(lines):
        if ln != "Bill To :" and not ln.startswith("Bill To :"):
            continue
        if i + 1 < len(lines):
            result["name"] = _clean(lines[i + 1]) or None
        name = result["name"]
        addr = ""
        for j in range(i + 2, len(lines)):
            nxt = lines[j]
            if nxt.startswith("GSTIN/UIN") or nxt.startswith("State Name") or nxt.startswith("Delivered To :"):
                if nxt.startswith("GSTIN/UIN"):
                    g = _GSTIN_RE.search(nxt)
                    if g:
                        result["gstin"] = g.group(1)
                    elif j + 1 < len(lines):
                        m2 = re.match(r"^\s*:?\s*([A-Z0-9]{15})$", lines[j + 1])
                        if m2:
                            result["gstin"] = m2.group(1)
                break
            part = _GSTIN_SUFFIX_RE.sub("", nxt)
            part = _PHONE_RE.sub("", part)
            if name:
                part = re.sub(re.escape(name), "", part, flags=re.IGNORECASE)
            part = part.replace("Æ", " ").strip(" ,-")
            if not part:
                continue
            if part in addr:
                continue
            addr = _merge_address(addr, part)
        if addr:
            result["address"] = _clean(addr) or None
            result["billing_address"] = result["address"]
        break
    return result


def _flat_items(lines: list[str]) -> tuple[list[dict], float | None]:
    start = None
    for i, ln in enumerate(lines):
        if ln == "Sl" and i + 1 < len(lines) and lines[i + 1] == "Description of Goods":
            j = i
            while j < len(lines) and lines[j] != "No.":
                j += 1
            start = j + 1
            break
    if start is None:
        return [], None
    items: list[dict] = []
    total = 0.0
    i = start
    n = len(lines)
    while i < n:
        m = _FLAT_ITEM_START_RE.match(lines[i])
        if not m:
            break
        item: dict[str, Any] = {"line_no": int(m.group(1)), "product_name": m.group(2) or None}
        desc = []
        i += 1
        while i < n:
            if _AMOUNT_RE.match(lines[i]):
                break
            desc.append(lines[i])
            i += 1
        if i >= n or not _AMOUNT_RE.match(lines[i]):
            break
        amount = _num(lines[i])
        i += 1
        uom = None
        if i < n and _FLAT_UOM_RE.match(lines[i]):
            uom = lines[i]
            i += 1
        price = None
        if i < n and _AMOUNT_RE.match(lines[i]):
            price = _num(lines[i])
            i += 1
        qty = None
        if i < n and _FLAT_INT_RE.match(lines[i]):
            qty = _num(lines[i])
            i += 1
        mrp = None
        if i < n and _FLAT_INT_RE.match(lines[i]):
            mrp = _num(lines[i])
            i += 1
        sku = None
        if i < n and _FLAT_CODE_RE.match(lines[i]):
            sku = lines[i]
            i += 1
        hsn = None
        if i < n and re.fullmatch(r"\d{6,8}", lines[i]):
            hsn = lines[i]
            i += 1
        ean = None
        if i < n and re.fullmatch(r"\d{13}", lines[i]):
            ean = lines[i]
            i += 1
        name_parts = [item.get("product_name") or ""] + desc
        item["product_name"] = _clean(" ".join(name_parts)) or None
        item.update({
            "product_description": None,
            "hsn": hsn,
            "sku": sku,
            "product_code": sku,
            "mrp": mrp,
            "quantity": qty,
            "unit_price": price,
            "uom": uom,
            "taxable_value": amount,
            "line_total": amount,
            "gst_pct": None,
            "ean": ean,
        })
        items.append(item)
        total += amount or 0.0
    return items, round(total, 2)


def _flat_totals(lines: list[str], taxable_total: float | None) -> dict:
    result: dict[str, Any] = {"igst": None, "cgst": None, "sgst": None, "round_off": None, "grand_total": None, "rate": None, "table_taxable": None}
    # GST totals: "IGST" line immediately followed by a money amount.
    for i, ln in enumerate(lines):
        m = re.match(r"^(IGST|CGST|SGST|UTGST)$", ln)
        if not m or i + 1 >= len(lines) or not _AMOUNT_RE.match(lines[i + 1]):
            continue
        key = {"IGST": "igst", "CGST": "cgst", "SGST": "sgst", "UTGST": "utgst"}[m.group(1)]
        if result.get(key) is None:
            result[key] = _num(lines[i + 1])
    # Round off: "Round Off" line followed by value such as "(-)0.39".
    for i, ln in enumerate(lines):
        if ln != "Round Off" or i + 1 >= len(lines):
            continue
        val_m = re.search(r"([\d,]+\.\d{2})", lines[i + 1])
        if val_m:
            neg = "-" in lines[i + 1].replace(" ", "")
            result["round_off"] = -_num(val_m.group(1)) if neg else _num(val_m.group(1))
    # Grand total: first "Total" line followed by a money line ("i 7,94,916.00").
    for i, ln in enumerate(lines):
        if ln != "Total" or i + 1 >= len(lines):
            continue
        m2 = re.match(r"^[^\d]*([\d,]+\.\d{2})$", lines[i + 1])
        if m2:
            result["grand_total"] = _num(m2.group(1))
            break
    # Rate + tax-table taxable: "18%" line, next money line is the taxable value.
    for i, ln in enumerate(lines):
        m = _FLAT_RATE_RE.match(ln)
        if not m:
            continue
        result["rate"] = _num(m.group(1))
        for j in range(i + 1, len(lines)):
            if _AMOUNT_RE.match(lines[j]):
                result["table_taxable"] = _num(lines[j])
                break
        break
    return result


def _parse_flat(lines: list[str]) -> dict | None:
    vendor = _flat_vendor(lines)
    invoice = _flat_invoice(lines, vendor.get("name"))
    buyer = _flat_buyer(lines)
    items, taxable_total = _flat_items(lines)
    totals = _flat_totals(lines, taxable_total)

    if not items:
        return None
    table_taxable = totals.get("table_taxable")
    if table_taxable is not None and abs(table_taxable - taxable_total) > 1.0:
        return None

    rate = totals.get("rate")
    for item in items:
        if item.get("gst_pct") is None:
            item["gst_pct"] = rate

    return {
        "vendor": vendor,
        "invoice": invoice,
        "buyer": buyer,
        "items": items,
        "taxes": {
            "taxable_value": fmt.round_money(taxable_total) if taxable_total is not None else None,
            "cgst": fmt.round_money(totals.get("cgst")),
            "sgst": fmt.round_money(totals.get("sgst")),
            "igst": fmt.round_money(totals.get("igst")),
            "utgst": None,
            "cess": None,
        },
        "totals": {
            "subtotal": fmt.round_money(taxable_total) if taxable_total is not None else None,
            "discount": None,
            "round_off": fmt.round_money(totals.get("round_off")),
            "grand_total": fmt.round_money(totals.get("grand_total")),
            "amount_paid": None,
            "balance_due": None,
        },
        "payment": {},
        "confidence": {},
        "source_confidence": {},
        "tax_details": [
            {
                "tax_type": tax_type,
                "amount": fmt.round_money(totals.get(key)),
                "rate": totals.get("rate"),
                "taxable_value": fmt.round_money(taxable_total) if taxable_total is not None else None,
                "source": "extracted",
            }
            for tax_type, key in (("IGST", "igst"), ("CGST", "cgst"), ("SGST", "sgst"))
            if totals.get(key)
        ],
    }


def _parse_vendor(lines: list[str]) -> dict:
    result: dict[str, Any] = {"name": None, "legal_name": None, "gstin": None, "pan": None, "address": None, "phone": None, "email": None}
    for i, ln in enumerate(lines):
        m = _VENDOR_RE.match(ln)
        if m:
            result["name"] = _clean(m.group(1)) or None
            addr_parts = []
            for j in range(i + 1, len(lines)):
                nxt = lines[j]
                if nxt.startswith("Despatch by") or nxt.startswith("GSTIN/UIN") or nxt.startswith("State Name") or nxt.startswith("PO. No.") or _DATE_RE.match(nxt) and len(nxt) < 12:
                    if nxt.startswith("GSTIN/UIN"):
                        g = _GSTIN_RE.search(nxt)
                        if g:
                            result["gstin"] = g.group(1)
                    continue
                if _CID_RE.search(nxt) or "CIN:" in nxt or "PAN No" in nxt or "E-Mail" in nxt:
                    break
                addr_parts.append(nxt)
            if addr_parts:
                result["address"] = _clean(" ".join(addr_parts)) or None
            break
    if result["gstin"] is None:
        for ln in lines:
            g = _GSTIN_RE.search(ln)
            if g:
                result["gstin"] = g.group(1)
                break
    return result


def _parse_invoice(lines: list[str], vendor_name: str | None) -> dict:
    result: dict[str, Any] = {
        "number": None, "date": None, "due_date": None, "po_number": None, "po_date": None,
        "grn_number": None, "delivery_note": None, "eway_bill": None, "currency": "INR",
        "payment_terms": None, "place_of_supply": None, "vendor_name": vendor_name,
    }
    for i, ln in enumerate(lines):
        m = _VENDOR_RE.match(ln)
        if m and i + 1 < len(lines):
            nxt = lines[i + 1]
            mm = _INV_LINE_RE.match(nxt)
            if mm:
                result["number"] = mm.group(1)
                result["date"] = _date(mm.group(2))
            break
    # PO number / PO date between "PO. No. PO.Date" and "CIN:"
    in_po = False
    for ln in lines:
        if "PO. No. PO.Date" in ln or ln.startswith("PO. No."):
            in_po = True
            continue
        if "CIN:" in ln:
            break
        if not in_po:
            continue
        mm = re.match(r"^([\d\/\s\-A-Za-z.]+?)\s+(\d{1,2}-[A-Za-z]{3}-\d{2})$", ln)
        if mm:
            result["po_number"] = _clean(mm.group(1)) or None
            result["po_date"] = _date(mm.group(2))
        elif re.fullmatch(r"[\d\/\-\s]+", ln) and len(ln) < 40:
            result["po_number"] = _clean(ln) or None
    return result


def _parse_buyer(lines: list[str]) -> dict:
    result: dict[str, Any] = {"name": None, "gstin": None, "address": None, "shipping_address": None, "billing_address": None}
    for i, ln in enumerate(lines):
        if ln != "Bill To :" and not ln.startswith("Bill To :"):
            continue
        if i > 0:
            result["name"] = _clean(lines[i - 1]) or None
        name = result["name"]
        addr = ""
        for j in range(i + 1, len(lines)):
            nxt = lines[j]
            g = _GSTIN_RE.search(nxt)
            if g:
                result["gstin"] = g.group(1)
            part = _GSTIN_SUFFIX_RE.sub("", nxt)
            part = _PHONE_RE.sub("", part)
            if "State Name :" in part:
                break
            if "GSTIN/UIN" in part:
                continue
            if name:
                part = re.sub(re.escape(name), "", part, flags=re.IGNORECASE)
            part = part.replace("Æ", " ").strip(" ,-")
            if not part:
                continue
            if part in addr:
                continue
            addr = _merge_address(addr, part)
        if addr:
            result["address"] = _clean(addr) or None
            result["billing_address"] = result["address"]
        break
    return result


def _merge_address(addr: str, frag: str) -> str:
    """Join address fragments, merging overlapping tails (PDF column wrap)."""
    for k in range(min(len(addr), len(frag)), 0, -1):
        if addr[-k:] == frag[:k]:
            return (addr + frag[k:]).strip()
    return f"{addr} {frag}".strip()


def _parse_items(lines: list[str]) -> tuple[list[dict], float | None]:
    start = next((i for i, ln in enumerate(lines) if ITEM_HEADER in ln), None)
    if start is None:
        return [], None
    items: list[dict] = []
    current: dict | None = None
    desc_lines: list[str] = []
    total = 0.0
    totals_started = False
    for ln in lines[start + 1:]:
        m = _ITEM_RE.match(ln)
        if m:
            if current is not None:
                _finish_item(current, desc_lines)
                total += current.get("taxable_value") or 0.0
            current = {
                "line_no": int(m.group(1)),
                "product_name": _clean(m.group(2)) or None,
                "hsn": m.group(3),
                "sku": m.group(4),
                "product_code": m.group(4),
                "mrp": _num(m.group(5)),
                "quantity": _num(m.group(6)),
                "unit_price": _num(m.group(7)),
                "uom": m.group(8),
                "taxable_value": _num(m.group(9)),
                "line_total": _num(m.group(9)),
                "gst_pct": None,
                "ean": None,
            }
            items.append(current)
            desc_lines = []
            totals_started = False
            continue
        if current is None:
            continue
        if totals_started:
            continue
        if _EAN_RE.match(ln):
            current["ean"] = ln
            continue
        if _AMOUNT_RE.match(ln):
            totals_started = True
            continue
        if ln.startswith(("IGST ", "CGST ", "SGST ", "UTGST ", "Total ", "Round Off", "Less :", "Amount Chargeable", "Taxable", "Value", "continued to page", "This is a Computer")) or _TAX_TABLE_RE.match(ln):
            totals_started = True
            continue
        desc_lines.append(ln)
    if current is not None:
        _finish_item(current, desc_lines)
        total += current.get("taxable_value") or 0.0
    return items, round(total, 2)


def _finish_item(item: dict, desc_lines: list[str]) -> None:
    if desc_lines:
        name = item.get("product_name") or ""
        item["product_name"] = _clean(" ".join([name] + desc_lines)) or None


def _parse_totals(lines: list[str], taxable_total: float | None) -> dict:
    result: dict[str, Any] = {"igst": None, "cgst": None, "sgst": None, "round_off": None, "grand_total": None, "rate": None, "table_taxable": None}
    for ln in lines:
        m = _TAX_TABLE_RE.match(ln)
        if m:
            result["table_taxable"] = _num(m.group(1))
            result["rate"] = _num(m.group(2))
            result["igst"] = _num(m.group(3))
            continue
        m = _ROUND_RE.match(ln)
        if m:
            result["round_off"] = _num(m.group(1))
            continue
        m = _TOTAL_RE.match(ln)
        if m:
            result["grand_total"] = _num(m.group(2))
            continue
        m = re.match(r"^(IGST|CGST|SGST|UTGST|CESS)\s+([\d,]+\.\d{2})$", ln)
        if m:
            key = {"IGST": "igst", "CGST": "cgst", "SGST": "sgst", "UTGST": "utgst", "CESS": "cess"}[m.group(1)]
            if result.get(key) is None:
                result[key] = _num(m.group(2))
    if taxable_total is None and result.get("table_taxable") is not None:
        result["grand_total"] = result["grand_total"]
    return result