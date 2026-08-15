"""Normalize raw AI JSON into the internal invoice structure, with tax intelligence."""

from __future__ import annotations

import logging
from typing import Any

from utils import formatting as fmt

logger = logging.getLogger("invoiceai.parser")

KEY_ALIASES = {
    "product_name": ["product_name", "item_name", "product", "item", "description", "item_description", "particulars", "particular", "material", "goods", "item description"],
    "product_description": ["product_description", "item_description", "description", "details", "specification"],
    "sku": ["sku", "sku_code", "sku_id", "product_code", "item_code", "code"],
    "product_code": ["product_code", "item_code", "code", "sku"],
    "item_code": ["item_code", "product_code", "code"],
    "ean": ["ean", "ean_code", "ean13"],
    "upc": ["upc", "upc_code"],
    "barcode": ["barcode", "bar_code"],
    "hsn": ["hsn", "hsn_code", "hsn_sac", "sac", "sac_code", "hsn/sac", "gst hsn"],
    "quantity": ["quantity", "qty", "qty_ordered", "quantity_ordered", "qty_box", "qty_unit"],
    "free_quantity": ["free_quantity", "free_qty", "scheme_qty", "qty_free"],
    "uom": ["uom", "unit", "unit_of_measure", "measure", "uom_short"],
    "unit_price": ["unit_price", "rate", "unit_rate", "price", "price_per_unit", "rate_per_unit", "basic_rate"],
    "mrp": ["mrp", "maximum_retail_price", "list_price"],
    "discount_pct": ["discount_pct", "discount_percent", "disc_%", "discount_%", "gst_disc", "trade_disc"],
    "discount_amount": ["discount_amount", "discount", "disc_amt", "trade_discount"],
    "taxable_value": ["taxable_value", "taxable_amount", "taxable", "assessable_value", "gross_value"],
    "gst_pct": ["gst_pct", "gst", "gst_rate", "gst_%", "tax_rate", "tax_percent", "cgst+sgst", "gst_rate_%"],
    "cgst_pct": ["cgst_pct", "cgst_rate", "cgst_%", "cgst"],
    "sgst_pct": ["sgst_pct", "sgst_rate", "sgst_%", "sgst"],
    "igst_pct": ["igst_pct", "igst_rate", "igst_%", "igst"],
    "cgst_amount": ["cgst_amount", "cgst_amt", "cgst"],
    "sgst_amount": ["sgst_amount", "sgst_amt", "sgst"],
    "igst_amount": ["igst_amount", "igst_amt", "igst"],
    "cess_amount": ["cess_amount", "cess", "cess_amt"],
    "line_total": ["line_total", "amount", "line_amount", "total", "net_amount", "value", "final_amount", "gross_amount"],
}


def _pick(d: dict, keys: list[str]) -> Any:
    for key in keys:
        if key in d and d[key] is not None:
            return d[key]
    return None


def _pick_value(d: dict, canonical: str) -> Any:
    aliases = [canonical] + KEY_ALIASES.get(canonical, [])
    for alias in aliases:
        if alias in d and d[alias] is not None:
            return d[alias]
    for lkey, lval in d.items():
        if lkey and canonical and lkey.strip().lower().replace(" ", "_") == canonical:
            return lval
    return None


def _num(d: dict, canonical: str) -> float | None:
    return fmt.to_float(_pick_value(d, canonical))


def _str(d: dict, canonical: str) -> str | None:
    return fmt.clean_text(_pick_value(d, canonical))


def _int(d: dict, canonical: str) -> int | None:
    return fmt.to_int(_pick_value(d, canonical))


def normalize_item(raw: dict | None, line_no: int) -> dict:
    raw = raw or {}
    item: dict[str, Any] = {}
    if not isinstance(raw, dict):
        return {"line_no": line_no}

    for field in ("product_name", "product_description", "sku", "product_code", "item_code", "ean", "upc", "barcode", "hsn", "uom"):
        item[field] = _str(raw, field)
    for field in (
        "quantity", "free_quantity", "unit_price", "mrp", "discount_pct", "discount_amount",
        "taxable_value", "gst_pct", "cgst_pct", "sgst_pct", "igst_pct",
        "cgst_amount", "sgst_amount", "igst_amount", "cess_amount", "line_total",
    ):
        item[field] = fmt.round_money(_num(raw, field)) if field.endswith(("_amount", "line_total", "taxable_value", "unit_price", "mrp", "discount_amount", "cgst_amount", "sgst_amount", "igst_amount", "cess_amount")) else _num(raw, field)

    item["line_no"] = _int(raw, "line_no") or line_no

    # rate splitting when the model reports gst as a combined string
    if item.get("gst_pct") is None:
        raw_rate = _pick_value(raw, "gst_pct")
        if raw_rate is not None:
            item["gst_pct"] = fmt.parse_percent(raw_rate)

    # compute missing line total from qty x unit price when clearly derivable
    if item.get("line_total") is None and item.get("quantity") is not None and item.get("unit_price") is not None:
        item["line_total"] = fmt.round_money(item["quantity"] * item["unit_price"])
    if item.get("taxable_value") is None and item.get("line_total") is not None:
        item["taxable_value"] = item["line_total"]

    item["line_total"] = fmt.round_money(item.get("line_total"))
    return item


def split_taxes(item: dict, mode_igst: bool | None = None) -> dict:
    """Derive CGST/SGST/IGST split amounts for an item when possible."""
    rate = item.get("gst_pct") or item.get("cgst_pct") or item.get("sgst_pct") or item.get("igst_pct")
    taxable = item.get("taxable_value")
    result = {}
    if rate is None or taxable is None:
        return result
    base = taxable
    has_igst = item.get("igst_pct") is not None or item.get("igst_amount") is not None or mode_igst is True
    if has_igst:
        item.setdefault("igst_pct", rate)
        result["igst_pct"] = rate
    else:
        half = rate / 2
        item.setdefault("cgst_pct", half)
        item.setdefault("sgst_pct", half)
        result["cgst_pct"] = half
        result["sgst_pct"] = half

    if item.get("cgst_amount") is None and item.get("cgst_pct") is not None:
        result["cgst_amount"] = fmt.round_money(base * item["cgst_pct"] / 100)
    if item.get("sgst_amount") is None and item.get("sgst_pct") is not None:
        result["sgst_amount"] = fmt.round_money(base * item["sgst_pct"] / 100)
    if item.get("igst_amount") is None and item.get("igst_pct") is not None:
        result["igst_amount"] = fmt.round_money(base * item["igst_pct"] / 100)
    return result


def apply_tax_intelligence(parsed: dict) -> None:
    """Fill missing CGST/SGST/IGST amounts at invoice level from items."""
    items = parsed.get("items") or []
    taxes = parsed.setdefault("taxes", {}) or {}
    totals = parsed.setdefault("totals", {}) or {}

    item_tax = {"cgst": 0.0, "sgst": 0.0, "igst": 0.0, "cess": 0.0}
    for item in items:
        for key in ("cgst_amount", "sgst_amount", "igst_amount", "cess_amount"):
            value = item.get(key)
            if value is not None:
                item_tax[key.replace("_amount", "").replace("amount", "")] += value

    changed = False
    for key in ("cgst", "sgst", "igst", "cess"):
        if taxes.get(key) is None and item_tax[key]:
            taxes[key] = fmt.round_money(item_tax[key])
            changed = True

    # IGST and CGST/SGST are mutually exclusive in GST: prefer IGST when the
    # model reported all three (common on inter-state e-invoices).
    if taxes.get("igst") and (taxes.get("cgst") or taxes.get("sgst")):
        taxes["cgst"] = None
        taxes["sgst"] = None
        changed = True

    if totals.get("taxable_value") is None and taxes.get("taxable_value") is None:
        total_taxable = sum((i.get("taxable_value") or 0.0) for i in items)
        if total_taxable:
            taxes["taxable_value"] = fmt.round_money(total_taxable)
            changed = True

    if totals.get("subtotal") is None and item_tax and any(items):
        total_lines = sum((i.get("line_total") or 0.0) for i in items)
        totals["subtotal"] = fmt.round_money(total_lines)
        changed = True

    # tax_details for storage
    tax_details = []
    tax_type_map = [("cgst", "CGST"), ("sgst", "SGST"), ("igst", "IGST"), ("cess", "CESS")]
    for key, label in tax_type_map:
        if taxes.get(key):
            tax_details.append({"tax_type": label, "amount": taxes[key], "rate": None, "taxable_value": taxes.get("taxable_value"), "source": "extracted"})
    if taxes.get("taxable_value") is None and any(items):
        pass
    parsed["tax_details"] = tax_details
    return None


def _flat_lookup(raw: dict, *keys: str) -> Any:
    """Look up a value in a flat dict using several candidate keys (case-insensitive)."""
    lowered = {str(k).strip().lower().replace(" ", "_").replace("-", "_").replace("/", "_"): v for k, v in raw.items()}
    for key in keys:
        norm = str(key).strip().lower().replace(" ", "_").replace("-", "_").replace("/", "_")
        if norm in lowered and lowered[norm] is not None:
            return lowered[norm]
    return None


def _normalize_flat(raw: dict) -> dict:
    """Convert a flat model response into the canonical nested structure."""
    result: dict[str, Any] = {"vendor": {}, "invoice": {}, "buyer": {}, "items": [], "taxes": {}, "totals": {}, "payment": {}, "confidence": {}}

    result["vendor"] = {
        "name": fmt.clean_text(_flat_lookup(raw, "seller_name", "vendor_name", "supplier_name", "vendor", "supplier", "name")),
        "legal_name": fmt.clean_text(_flat_lookup(raw, "legal_name", "vendor_legal_name")),
        "gstin": fmt.clean_text(_flat_lookup(raw, "gstin_seller", "vendor_gstin", "seller_gstin", "gstin", "gst_no", "supplier_gstin")),
        "pan": fmt.clean_text(_flat_lookup(raw, "seller_pan", "vendor_pan", "pan")),
        "address": fmt.clean_text(_flat_lookup(raw, "seller_s_address", "seller_address", "vendor_address", "seller_address_details")),
        "phone": fmt.clean_text(_flat_lookup(raw, "seller_phone", "vendor_phone", "phone", "phone_no")),
        "email": fmt.clean_text(_flat_lookup(raw, "seller_email", "vendor_email", "email")),
    }

    result["invoice"] = {
        "number": fmt.clean_text(_flat_lookup(raw, "invoice_number", "invoice_no", "inv_no", "inv_number", "invoice", "bill_no")),
        "date": fmt.parse_date(_flat_lookup(raw, "invoice_date", "inv_date", "date", "bill_date", "invoice_dt")),
        "due_date": fmt.parse_date(_flat_lookup(raw, "due_date", "payment_due_date")),
        "po_number": fmt.clean_text(_flat_lookup(raw, "po_number", "po_no", "purchase_order", "purchase_order_no")),
        "po_date": fmt.parse_date(_flat_lookup(raw, "po_date", "purchase_order_date")),
        "grn_number": fmt.clean_text(_flat_lookup(raw, "grn_number", "grn_no")),
        "delivery_note": fmt.clean_text(_flat_lookup(raw, "delivery_note", "dn_number", "d_note")),
        "eway_bill": fmt.clean_text(_flat_lookup(raw, "eway_bill", "ewaybill", "eway_bill_no", "eway_bill_number")),
        "currency": fmt.clean_text(_flat_lookup(raw, "currency")) or "INR",
        "payment_terms": fmt.clean_text(_flat_lookup(raw, "payment_terms", "terms")),
        "place_of_supply": fmt.clean_text(_flat_lookup(raw, "place_of_supply", "pos", "place_of_supply_code")),
        "vendor_name": fmt.clean_text(_flat_lookup(raw, "seller_name", "vendor_name", "supplier_name")),
    }

    result["buyer"] = {
        "name": fmt.clean_text(_flat_lookup(raw, "buyer_name", "bill_to_name", "consignee", "customer_name", "party_name")),
        "gstin": fmt.clean_text(_flat_lookup(raw, "gstin_buyer", "buyer_gstin", "bill_to_gstin", "customer_gstin")),
        "address": fmt.clean_text(_flat_lookup(raw, "bill_to_address", "buyer_address", "bill_address")),
        "shipping_address": fmt.clean_text(_flat_lookup(raw, "ship_to_address", "shipping_address", "ship_address")),
        "billing_address": fmt.clean_text(_flat_lookup(raw, "bill_to_address", "billing_address")),
    }

    raw_items = raw.get("items")
    if not isinstance(raw_items, list):
        raw_items = raw.get("line_items")
    if isinstance(raw_items, list):
        result["items"] = [normalize_item(it, i + 1) for i, it in enumerate(raw_items) if isinstance(it, dict)]

    result["taxes"] = {
        "taxable_value": fmt.round_money(fmt.to_float(_flat_lookup(raw, "taxable_value", "taxable_amount", "assessable_value"))),
        "cgst": fmt.round_money(fmt.to_float(_flat_lookup(raw, "cgst", "cgst_amount", "cgst_amt"))),
        "sgst": fmt.round_money(fmt.to_float(_flat_lookup(raw, "sgst", "sgst_amount", "sgst_amt"))),
        "igst": fmt.round_money(fmt.to_float(_flat_lookup(raw, "igst", "igst_amount", "igst_amt"))),
        "utgst": fmt.round_money(fmt.to_float(_flat_lookup(raw, "utgst", "utgst_amount"))),
        "cess": fmt.round_money(fmt.to_float(_flat_lookup(raw, "cess", "cess_amount", "cess_amt"))),
    }

    result["totals"] = {
        "subtotal": fmt.round_money(fmt.to_float(_flat_lookup(raw, "subtotal", "total_before_tax", "total_before_taxes"))),
        "discount": fmt.round_money(fmt.to_float(_flat_lookup(raw, "discount", "total_discount", "discount_amount"))),
        "round_off": fmt.round_money(fmt.to_float(_flat_lookup(raw, "round_off", "roundoff", "round_off_amount"))),
        "grand_total": fmt.round_money(fmt.to_float(_flat_lookup(raw, "grand_total", "invoice_total", "total_amount", "total", "amount_payable", "invoice_value"))),
        "amount_paid": fmt.round_money(fmt.to_float(_flat_lookup(raw, "amount_paid", "paid_amount"))),
        "balance_due": fmt.round_money(fmt.to_float(_flat_lookup(raw, "balance_due", "balance", "amount_due"))),
    }

    result["payment"] = {
        "bank_name": fmt.clean_text(_flat_lookup(raw, "bank_name", "bank")),
        "bank_account": fmt.clean_text(_flat_lookup(raw, "bank_account", "account_number", "bank_account_number")),
        "ifsc": fmt.clean_text(_flat_lookup(raw, "ifsc", "ifsc_code")),
        "upi": fmt.clean_text(_flat_lookup(raw, "upi", "upi_id")),
    }

    conf_raw = raw.get("confidence")
    if isinstance(conf_raw, dict):
        for key, value in conf_raw.items():
            score = fmt.to_float(value)
            if score is not None:
                result["confidence"][str(key)] = int(round(score))
    return result


def normalize_ai_output(raw: dict) -> dict:
    raw = raw or {}
    # Detect a flat model response (no nested groups) and normalize it
    if "vendor" not in raw and "invoice" not in raw and "buyer" not in raw and "items" not in raw and "totals" not in raw:
        keys = {str(k).lower() for k in raw.keys()}
        if any(("invoice_number" in keys, "gstin_seller" in keys, "seller_name" in keys, "grand_total" in keys, "invoice_no" in keys)):
            return _normalize_flat(raw)
    parsed: dict[str, Any] = {
        "vendor": {},
        "invoice": {},
        "buyer": {},
        "items": [],
        "taxes": {},
        "totals": {},
        "payment": {},
        "confidence": {},
        "source_confidence": {},
    }

    vendor_raw = raw.get("vendor") or {}
    for field in ("name", "legal_name", "gstin", "pan", "address", "phone", "email"):
        parsed["vendor"][field] = fmt.clean_text(vendor_raw.get(field)) if isinstance(vendor_raw, dict) else None

    invoice_raw = raw.get("invoice") or {}
    if isinstance(invoice_raw, dict):
        parsed["invoice"]["number"] = fmt.clean_text(_pick_value(invoice_raw, "number") or _pick_value(raw, "number"))
        parsed["invoice"]["date"] = fmt.parse_date(_pick_value(invoice_raw, "date") or _pick_value(raw, "date"))
        parsed["invoice"]["due_date"] = fmt.parse_date(_pick_value(invoice_raw, "due_date"))
        parsed["invoice"]["po_number"] = fmt.clean_text(_pick_value(invoice_raw, "po_number") or _pick_value(raw, "po_number"))
        parsed["invoice"]["po_date"] = fmt.parse_date(_pick_value(invoice_raw, "po_date"))
        parsed["invoice"]["grn_number"] = fmt.clean_text(_pick_value(invoice_raw, "grn_number"))
        parsed["invoice"]["delivery_note"] = fmt.clean_text(_pick_value(invoice_raw, "delivery_note"))
        parsed["invoice"]["eway_bill"] = fmt.clean_text(_pick_value(invoice_raw, "eway_bill"))
        parsed["invoice"]["currency"] = fmt.clean_text(_pick_value(invoice_raw, "currency")) or "INR"
        parsed["invoice"]["payment_terms"] = fmt.clean_text(_pick_value(invoice_raw, "payment_terms"))
        parsed["invoice"]["place_of_supply"] = fmt.clean_text(_pick_value(invoice_raw, "place_of_supply"))
        parsed["invoice"]["vendor_name"] = fmt.clean_text(_pick_value(invoice_raw, "vendor_name"))

    buyer_raw = raw.get("buyer") or {}
    if isinstance(buyer_raw, dict):
        for field in ("name", "gstin", "address", "shipping_address", "billing_address"):
            parsed["buyer"][field] = fmt.clean_text(buyer_raw.get(field))

    items_raw = raw.get("items")
    if isinstance(items_raw, list):
        line_no = 1
        for item_raw in items_raw:
            item = normalize_item(item_raw, line_no)
            split_taxes(item)
            # item-level validation: qty x price vs line total is left to validation service
            parsed["items"].append(item)
            line_no += 1

    taxes_raw = raw.get("taxes") or {}
    if isinstance(taxes_raw, dict):
        for field in ("taxable_value", "cgst", "sgst", "igst", "utgst", "cess"):
            value = _pick_value(taxes_raw, field)
            parsed["taxes"][field] = fmt.round_money(fmt.to_float(value))

    totals_raw = raw.get("totals") or {}
    if isinstance(totals_raw, dict):
        for field in ("subtotal", "discount", "round_off", "grand_total", "amount_paid", "balance_due"):
            value = _pick_value(totals_raw, field)
            parsed["totals"][field] = fmt.round_money(fmt.to_float(value))

    payment_raw = raw.get("payment") or {}
    if isinstance(payment_raw, dict):
        for field in ("bank_name", "bank_account", "ifsc", "upi"):
            parsed["payment"][field] = fmt.clean_text(payment_raw.get(field))

    conf_raw = raw.get("confidence")
    if isinstance(conf_raw, dict):
        for key, value in conf_raw.items():
            score = fmt.to_float(value)
            if score is not None:
                parsed["confidence"][str(key)] = int(round(score))

    # grand total fallback: amount_paid / balance_due relationships are left as extracted
    if parsed["totals"].get("grand_total") is None and parsed["totals"].get("subtotal") is not None:
        taxes = parsed["taxes"]
        total_tax = sum(taxes.get(k) or 0.0 for k in ("cgst", "sgst", "igst", "utgst", "cess"))
        discount = parsed["totals"].get("discount") or 0.0
        round_off = parsed["totals"].get("round_off") or 0.0
        calculated = (parsed["totals"]["subtotal"] or 0.0) + total_tax - discount + round_off
        if calculated:
            parsed["totals"]["grand_total"] = fmt.round_money(calculated)

    if parsed["invoice"].get("vendor_name") is None and parsed["vendor"].get("name"):
        parsed["invoice"]["vendor_name"] = parsed["vendor"]["name"]

    return parsed