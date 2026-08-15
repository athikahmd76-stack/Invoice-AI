"""Invoice validation: totals reconciliation and line-item checks."""

from __future__ import annotations

import logging

from utils import formatting as fmt

logger = logging.getLogger("invoiceai.validation")

TOLERANCE = 0.5


def _close(a: float | None, b: float | None) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(a - b) <= TOLERANCE


def _check(checks: list[dict], name: str, passed: bool, detail: str, expected=None, actual=None, diff=None) -> None:
    checks.append(
        {
            "name": name,
            "status": "ok" if passed else "mismatch",
            "detail": detail,
            "expected": expected,
            "actual": actual,
            "diff": diff,
        }
    )


def validate(parsed: dict) -> dict:
    """Validate extracted values. Never modify invoice values; report discrepancies."""
    checks: list[dict] = []
    totals = parsed.get("totals") or {}
    taxes = parsed.get("taxes") or {}
    items = parsed.get("items") or []

    subtotal = totals.get("subtotal")
    discount = totals.get("discount") or 0.0
    round_off = totals.get("round_off") or 0.0
    grand_total = totals.get("grand_total")
    total_tax = sum(taxes.get(k) or 0.0 for k in ("cgst", "sgst", "igst", "utgst", "cess"))

    # ------------------------------------------------------ totals equation
    calc_grand = None
    if subtotal is not None:
        calc_grand = fmt.round_money(subtotal + total_tax - discount + round_off)
        _check(checks, "Grand Total", _close(calc_grand, grand_total),
               f"Calculated: ₹{fmt.format_inr(calc_grand)} vs Invoice: ₹{fmt.format_inr(grand_total) if grand_total is not None else '—'}",
               expected=calc_grand, actual=grand_total,
               diff=fmt.round_money(calc_grand - grand_total) if grand_total is not None else None)

    # ------------------------------------------------------- line items vs total
    if items:
        sum_lines = fmt.round_money(sum((i.get("line_total") or 0.0) for i in items))
        sum_taxable = fmt.round_money(sum((i.get("taxable_value") or 0.0) for i in items))
        if grand_total is not None:
            _check(checks, "Line Items vs Grand Total", _close(sum_lines, grand_total),
                   f"Σ Line Totals: ₹{fmt.format_inr(sum_lines)} vs Invoice Grand Total: ₹{fmt.format_inr(grand_total)}",
                   expected=sum_lines, actual=grand_total,
                   diff=fmt.round_money(sum_lines - grand_total))
        if subtotal is not None:
            _check(checks, "Line Items vs Subtotal", _close(sum_lines, subtotal),
                   f"Σ Line Totals: ₹{fmt.format_inr(sum_lines)} vs Subtotal: ₹{fmt.format_inr(subtotal)}",
                   expected=sum_lines, actual=subtotal,
                   diff=fmt.round_money(sum_lines - subtotal))
        if taxes.get("taxable_value") is None and sum_taxable:
            _check(checks, "Taxable Value", True, f"Taxable value derived from line items: ₹{fmt.format_inr(sum_taxable)}", expected=sum_taxable, actual=None, diff=None)

    # ------------------------------------------------------- qty x rate = line total
    qty_mismatches = 0
    for item in items:
        qty, rate, line_total = item.get("quantity"), item.get("unit_price"), item.get("line_total")
        if qty is not None and rate is not None and line_total is not None:
            computed = fmt.round_money(qty * rate)
            if not _close(computed, line_total):
                qty_mismatches += 1
                _check(checks, f"Line {item.get('line_no', '?')} Qty × Rate",
                       False,
                       f"₹{fmt.format_inr(qty)} × ₹{fmt.format_inr(rate)} = ₹{fmt.format_inr(computed)} vs line total ₹{fmt.format_inr(line_total)}",
                       expected=computed, actual=line_total, diff=fmt.round_money(computed - line_total))

    # --------------------------------------------------------------- tax split
    if total_tax and grand_total is not None and subtotal is not None:
        implied = fmt.round_money((subtotal - discount + round_off) + total_tax)
        _check(checks, "Tax Components", abs(implied - (grand_total or 0)) <= TOLERANCE,
               f"Subtotal + Tax - Discount + Round Off = ₹{fmt.format_inr(implied)} vs Grand Total ₹{fmt.format_inr(grand_total)}",
               expected=implied, actual=grand_total, diff=fmt.round_money(implied - grand_total))

    # ---------------------------------------------------------------- status
    mismatch_count = len([c for c in checks if c["status"] == "mismatch"])
    if mismatch_count:
        validation_status = "Mismatch"
    else:
        validation_status = "Matched"

    status = "Validated" if validation_status == "Matched" else "Needs Review"
    return {
        "status": status,
        "validation_status": validation_status,
        "checks": checks,
        "mismatch_count": mismatch_count,
        "computed_grand_total": calc_grand,
    }