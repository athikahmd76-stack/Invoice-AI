"""Invoice details page: header, vendor/buyer, summary, line items, tax, validation, confidence, original doc."""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import streamlit as st

from database.database import json_loads
from database import repository
from services.duplicate_service import update_fingerprint
from utils import formatting as fmt
from ui.common import badge, money, section_title


def _kv_row(label: str, value, key: str = "") -> None:
    display = fmt.clean_text(value) if value is not None else "—"
    st.markdown(f"**{label}:** {display}", unsafe_allow_html=True)


def _detail_rows(invoice, mapping: list[tuple[str, str]]) -> str:
    parts = []
    for label, attr in mapping:
        if attr is None:
            continue
        value = getattr(invoice, attr, None)
        display = fmt.clean_text(value) if value is not None else "—"
        if label in ("Invoice Date", "Due Date", "PO Date") and value:
            display = fmt.format_date(value)
        parts.append(f"**{label}:** {display}")
    return "<br>".join(parts)


def _render_header(invoice) -> None:
    status_badges = (
        f"{badge(invoice.status)} {badge(invoice.validation_status or '—')} {badge('Duplicate' if invoice.duplicate_status == 'Duplicate' else invoice.duplicate_status)}"
    )
    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown(
            f"""
            <div class="ia-card">
                <div style="font-size:20px;font-weight:800;color:#172b4d;">{invoice.invoice_number or 'Invoice #' + str(invoice.id)}</div>
                <div class="ia-muted">Date: {fmt.format_date(invoice.invoice_date)} &nbsp;•&nbsp; Vendor: {invoice.vendor_name or '—'}</div>
                <div style="margin-top:8px;">{status_badges}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with col2:
        st.markdown(
            f"""
            <div class="ia-card" style="text-align:center;">
                <div class="ia-kpi-label">Grand Total</div>
                <div class="ia-kpi-value" style="font-size:24px;">{money(invoice.grand_total)}</div>
                <div class="ia-muted">{invoice.currency or 'INR'} &nbsp;•&nbsp; Processed {fmt.format_date(invoice.processed_at)[:10] if invoice.processed_at else '—'}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def _render_sections(invoice) -> None:
    c1, c2, c3 = st.columns(3)
    with c1:
        with st.container(border=True):
            section_title("Vendor Information")
            st.markdown(_detail_rows(invoice, [
                ("Name", "vendor_name"), ("Legal Name", None), ("GSTIN", "vendor_gstin"), ("PAN", "vendor_pan"),
                ("Address", "vendor_address"), ("Phone", "vendor_phone"), ("Email", "vendor_email"),
            ]), unsafe_allow_html=True)
    with c2:
        with st.container(border=True):
            section_title("Buyer Information")
            st.markdown(_detail_rows(invoice, [
                ("Buyer Name", "buyer_name"), ("Buyer GSTIN", "buyer_gstin"), ("Address", "buyer_address"),
                ("Billing Address", "billing_address"), ("Shipping Address", "shipping_address"),
            ]), unsafe_allow_html=True)
    with c3:
        with st.container(border=True):
            section_title("PO / Order Information")
            st.markdown(_detail_rows(invoice, [
                ("PO Number", "po_number"), ("PO Date", "po_date"), ("GRN Number", "grn_number"),
                ("Delivery Note", "delivery_note"), ("E-Way Bill", "eway_bill"),
                ("Payment Terms", "payment_terms"), ("Place of Supply", "place_of_supply"),
            ]), unsafe_allow_html=True)

    with st.container(border=True):
        section_title("Invoice Summary")
        cols = st.columns(4)
        summary_items = [
            ("Subtotal", money(invoice.subtotal), "subtotal"),
            ("Discount", money(invoice.discount), "discount"),
            ("Taxable Value", money(invoice.taxable_value), "taxable_value"),
            ("CGST", money(invoice.cgst), "cgst"),
            ("SGST", money(invoice.sgst), "sgst"),
            ("IGST", money(invoice.igst), "igst"),
            ("UTGST", money(invoice.utgst), "utgst"),
            ("CESS", money(invoice.cess), "cess"),
            ("Round Off", money(invoice.round_off), "round_off"),
            ("Grand Total", money(invoice.grand_total), "grand_total"),
            ("Amount Paid", money(invoice.amount_paid), "amount_paid"),
            ("Balance Due", money(invoice.balance_due), "balance_due"),
        ]
        for idx, (label, value, _attr) in enumerate(summary_items):
            with cols[idx % 4]:
                st.metric(label, value)
        if any([invoice.bank_name, invoice.bank_account, invoice.ifsc, invoice.upi]):
            st.markdown(
                f"**Bank:** {invoice.bank_name or '—'} &nbsp;•&nbsp; **A/C:** {invoice.bank_account or '—'} &nbsp;•&nbsp; "
                f"**IFSC:** {invoice.ifsc or '—'} &nbsp;•&nbsp; **UPI:** {invoice.upi or '—'}",
                unsafe_allow_html=True,
            )

    # -------------------------------------------------------------- line items
    with st.container(border=True):
        section_title(f"Line Items ({len(invoice.items)})")
        st.dataframe(_items_df(invoice.items), use_container_width=True, hide_index=True)

    # -------------------------------------------------------------- tax details
    if invoice.tax_details:
        with st.container(border=True):
            section_title("Tax Summary")
            tdf = pd.DataFrame([
                {"Type": t.tax_type, "Rate %": t.rate if t.rate is not None else "—", "Taxable Value": money(t.taxable_value), "Amount": money(t.amount), "Source": t.source}
                for t in invoice.tax_details
            ])
            st.dataframe(tdf, use_container_width=True, hide_index=True)

    # -------------------------------------------------------------- validation
    checks = json_loads(invoice.validation_checks) or []
    with st.container(border=True):
        section_title("Validation & Reconciliation")
        if checks:
            for check in checks:
                ok = check.get("status") == "ok"
                icon = "✓" if ok else "⚠"
                cls = "ia-check-ok" if ok else "ia-check-bad"
                expected = money(check.get("expected")) if check.get("expected") is not None else "—"
                actual = money(check.get("actual")) if check.get("actual") is not None else "—"
                diff = money(check.get("diff")) if check.get("diff") is not None else "—"
                st.markdown(
                    f'<div class="ia-check-item"><span class="{cls}">{icon} {check.get("name", "")}</span>'
                    f' &nbsp;<span class="ia-muted">{check.get("detail", "")}</span>'
                    f'<br><span class="ia-muted">Calculated: {expected} &nbsp;•&nbsp; Invoice: {actual} &nbsp;•&nbsp; Difference: {diff}</span></div>',
                    unsafe_allow_html=True,
                )
            if invoice.validation_status:
                st.markdown(f"**Overall:** {badge(invoice.validation_status)}", unsafe_allow_html=True)
        else:
            st.info("No validation checks recorded for this invoice.")

    # -------------------------------------------------------------- confidence
    confidence = json_loads(invoice.confidence_json) or {}
    with st.container(border=True):
        section_title("Confidence")
        if confidence:
            rows = []
            for field, score in confidence.items():
                label = fmt.confidence_label(score)
                rows.append({"Field": field, "Score": score, "Confidence": label})
            cdf = pd.DataFrame(rows)
            st.dataframe(cdf, use_container_width=True, hide_index=True)
            low_fields = [r["Field"] for r in rows if r["Confidence"] == "Low"]
            if low_fields:
                st.warning(f"Low-confidence fields — review: {', '.join(low_fields)}")
        else:
            st.info("No confidence scores recorded.")

    # -------------------------------------------------------------- original doc
    with st.container(border=True):
        col_a, col_b = st.columns(2)
        with col_a:
            section_title("Original Document")
            if invoice.preview_path and Path(invoice.preview_path).exists():
                st.image(invoice.preview_path, caption=f"Preview — {invoice.original_filename or invoice.file_path or ''}", use_container_width=True)
            else:
                st.info("No preview available.")
        with col_b:
            section_title("Original File")
            if invoice.file_path and Path(invoice.file_path).exists():
                data = Path(invoice.file_path).read_bytes()
                st.download_button(
                    "Download Original",
                    data=data,
                    file_name=invoice.original_filename or Path(invoice.file_path).name,
                    use_container_width=True,
                )
            else:
                st.info("Original file not stored (imported record).")


def _items_df(items) -> pd.DataFrame:
    if not items:
        return pd.DataFrame()
    rows = []
    for item in items:
        rows.append({
            "Line": item.line_no,
            "Product": item.product_name,
            "SKU": item.sku,
            "HSN": item.hsn,
            "Qty": item.quantity,
            "UOM": item.uom,
            "Unit Price": money(item.unit_price),
            "Discount": money(item.discount_amount),
            "Taxable": money(item.taxable_value),
            "GST%": item.gst_pct,
            "CGST": money(item.cgst_amount),
            "SGST": money(item.sgst_amount),
            "IGST": money(item.igst_amount),
            "Total": money(item.line_total),
        })
    return pd.DataFrame(rows)


def _edit_form(invoice, session_factory) -> None:
    with st.form("invoice_edit_form"):
        st.markdown("##### Correct extracted fields")
        c1, c2, c3 = st.columns(3)
        with c1:
            vendor_name = st.text_input("Vendor Name", value=invoice.vendor_name or "")
            invoice_number = st.text_input("Invoice Number", value=invoice.invoice_number or "")
            invoice_date = st.text_input("Invoice Date (YYYY-MM-DD)", value=invoice.invoice_date or "")
        with c2:
            vendor_gstin = st.text_input("Vendor GSTIN", value=invoice.vendor_gstin or "")
            po_number = st.text_input("PO Number", value=invoice.po_number or "")
            due_date = st.text_input("Due Date (YYYY-MM-DD)", value=invoice.due_date or "")
        with c3:
            buyer_name = st.text_input("Buyer Name", value=invoice.buyer_name or "")
            buyer_gstin = st.text_input("Buyer GSTIN", value=invoice.buyer_gstin or "")
            place_of_supply = st.text_input("Place of Supply", value=invoice.place_of_supply or "")
        with st.container():
            c1, c2, c3, c4 = st.columns(4)
            subtotal = c1.text_input("Subtotal", value=str(invoice.subtotal) if invoice.subtotal is not None else "")
            discount = c2.text_input("Discount", value=str(invoice.discount) if invoice.discount is not None else "")
            total_tax = c3.text_input("Total Tax", value=str((invoice.cgst or 0) + (invoice.sgst or 0) + (invoice.igst or 0) + (invoice.utgst or 0) + (invoice.cess or 0)) if invoice.grand_total is not None else "")
            grand_total = c4.text_input("Grand Total", value=str(invoice.grand_total) if invoice.grand_total is not None else "")
        submitted = st.form_submit_button("Save Changes", type="primary")
        if submitted:
            fields = {
                "vendor_name": vendor_name.strip() or None,
                "vendor_gstin": vendor_gstin.strip() or None,
                "invoice_number": invoice_number.strip() or None,
                "invoice_date": fmt.parse_date(invoice_date),
                "due_date": fmt.parse_date(due_date),
                "po_number": po_number.strip() or None,
                "buyer_name": buyer_name.strip() or None,
                "buyer_gstin": buyer_gstin.strip() or None,
                "place_of_supply": place_of_supply.strip() or None,
                "subtotal": fmt.to_float(subtotal),
                "discount": fmt.to_float(discount),
                "grand_total": fmt.to_float(grand_total),
            }
            if fmt.to_float(total_tax) is not None and fields["grand_total"] is not None and fields["subtotal"] is not None:
                fields["igst"] = fmt.to_float(total_tax)
                fields["cgst"] = None
                fields["sgst"] = None
            with session_factory() as session:
                repository.update_invoice_fields(session, invoice.id, fields)
                update_fingerprint(session, invoice.id)
                session.commit()
            st.success("Changes saved.")
            st.rerun()


def render(invoice_id: int, session_factory) -> None:
    with session_factory() as session:
        invoice = repository.get_invoice(session, invoice_id)
        if invoice is None:
            st.error("Invoice not found.")
            return
        _render_header(invoice)
        _render_sections(invoice)

    st.markdown("<br>", unsafe_allow_html=True)
    with st.expander("Manual Review — Edit Extracted Data", expanded=False):
        _edit_form(invoice, session_factory)

    # duplicate actions
    if invoice.duplicate_status == "Duplicate" and invoice.duplicate_of_id:
        with st.container(border=True):
            section_title("Possible Duplicate Invoice")
            st.warning(
                f"This invoice matches invoice **#{invoice.duplicate_of_id}** "
                f"({invoice.invoice_number}) — same vendor GSTIN and invoice number."
            )
            c1, c2, c3, c4 = st.columns(4)
            if c1.button("Keep (clear warning)"):
                with session_factory() as session:
                    repository.set_duplicate_status(session, invoice.id, "Ignored")
                    session.commit()
                st.rerun()
            if c3.button("Delete This Invoice"):
                with session_factory() as session:
                    repository.delete_invoice(session, invoice.id)
                    session.commit()
                st.session_state["ia_page_target"] = "Records"
                st.session_state.pop("ia_open_select", None)
                st.rerun()
            c4.markdown("<span class='ia-muted'>Deleting removes this record and its line items.</span>", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("← Back to Records"):
        st.session_state["ia_page_target"] = "Records"
        st.session_state.pop("ia_open_select", None)
        st.rerun()