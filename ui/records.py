"""Records page: search, filters, paginated table, detail navigation, exports."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from database import repository
from services.export_service import complete_report_bytes, excel_bytes
from ui.common import badge, money, section_title

PAGE_SIZE = 25
FILTER_KEYS = ["search", "invoice_no", "vendor", "po_number", "gstin", "status", "validation_status", "duplicate_status", "date_from", "date_to"]


def _default_filters() -> dict:
    defaults: dict = {key: None for key in FILTER_KEYS}
    defaults["status"] = "All"
    defaults["validation_status"] = "All"
    defaults["duplicate_status"] = "All"
    return defaults


def render(session_factory) -> None:
    with session_factory() as session:
        vendors = repository.distinct_vendors(session)

    section_title("Invoice Records")

    with st.expander("Search & Filters", expanded=True):
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            search = st.text_input("Search", placeholder="Invoice No, Vendor, PO, GSTIN, Product, SKU…")
        with c2:
            vendor = st.selectbox("Vendor", ["All"] + vendors)
        with c3:
            status = st.selectbox("Status", ["All", "Validated", "Extracted", "Needs Review", "Failed", "Imported"])
        with c4:
            dup_status = st.selectbox("Duplicate", ["All", "None", "Duplicate", "Ignored"])
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            validation = st.selectbox("Validation", ["All", "Matched", "Mismatch", "Needs Review"])
        with c2:
            date_from = st.date_input("Date from", value=None)
        with c3:
            date_to = st.date_input("Date to", value=None)
        with c4:
            po_number = st.text_input("PO Number", placeholder="PO…")
            gstin = st.text_input("GSTIN", placeholder="GSTIN…")
        if st.button("Clear Filters"):
            for key in st.session_state:
                if key.startswith("ia_f_") or key in ("ia_po_number", "ia_gstin"):
                    del st.session_state[key]
            st.rerun()

    filters = _default_filters()
    filters["search"] = search.strip() or None
    filters["vendor"] = None if vendor == "All" else vendor
    filters["status"] = None if status == "All" else status
    filters["validation_status"] = None if validation == "All" else validation
    filters["duplicate_status"] = None if dup_status == "All" else dup_status
    filters["date_from"] = date_from.isoformat() if date_from else None
    filters["date_to"] = date_to.isoformat() if date_to else None
    filters["po_number"] = po_number.strip() or None
    filters["gstin"] = gstin.strip() or None

    page = st.session_state.get("ia_page", 1)
    if "ia_filters" not in st.session_state:
        st.session_state.ia_filters = filters
    st.session_state.ia_filters = filters

    with session_factory() as session:
        total, rows = repository.query_invoices(session, filters, page=page, page_size=PAGE_SIZE)

    st.markdown(f"**{total:,}** invoice(s) found", unsafe_allow_html=True)

    if not rows:
        st.info("No records match the current filters.")
        return

    df = pd.DataFrame(rows)
    display = df[["id", "invoice_number", "invoice_date", "vendor_name", "po_number", "grand_total", "status", "validation_status", "duplicate_status"]].copy()
    display.columns = ["ID", "Invoice No", "Date", "Vendor", "PO", "Total", "Status", "Validation", "Dup"]
    display["Total"] = display["Total"].apply(lambda v: money(v))

    st.dataframe(
        display,
        use_container_width=True,
        hide_index=True,
        column_config={
            "ID": st.column_config.NumberColumn("ID", width="small"),
            "Status": st.column_config.TextColumn("Status", width="small"),
            "Validation": st.column_config.TextColumn("Validation", width="small"),
            "Dup": st.column_config.TextColumn("Dup", width="small"),
        },
    )

    # ------------------------------------------------------------ pagination
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    cols = st.columns([1, 2, 1])
    with cols[0]:
        if st.button("← Previous", disabled=page <= 1, use_container_width=True):
            st.session_state.ia_page = max(1, page - 1)
            st.rerun()
    with cols[2]:
        if st.button("Next →", disabled=page >= total_pages, use_container_width=True):
            st.session_state.ia_page = page + 1
            st.rerun()
    cols[1].markdown(f"<center class='ia-muted'>Page {page} of {total_pages}</center>", unsafe_allow_html=True)

    # ------------------------------------------------------------ open details
    st.markdown("<br>", unsafe_allow_html=True)
    preview_ids = [str(i) for i in display["ID"].tolist()[:25]]
    selected = st.selectbox("Open invoice details for…", ["—"] + preview_ids, key="ia_open_select")
    if selected != "—":
        st.session_state["ia_invoice_id"] = int(selected)
        st.session_state["ia_page_target"] = "Invoice"
        st.rerun()

    # ------------------------------------------------------------ exports
    st.markdown("<br>", unsafe_allow_html=True)
    with st.container(border=True):
        section_title("Export")
        c1, c2, c3, c4 = st.columns(4)
        if c1.button("Export Excel (filtered)", use_container_width=True):
            with session_factory() as session:
                data = excel_bytes(session, filters)
            st.session_state["ia_excel_bytes"] = data
            st.rerun()
        if "ia_excel_bytes" in st.session_state:
            stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            c2.download_button(
                "Download Excel",
                data=st.session_state["ia_excel_bytes"],
                file_name=f"invoiceai_export_{stamp}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
            st.session_state["ia_excel_bytes"] = None
        c3.markdown("&nbsp;")

    with st.container(border=True):
        section_title("Export All Records (current filters → CSV)")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Export Invoices CSV", use_container_width=True):
                with session_factory() as session:
                    from services.export_service import export_csv

                    paths = export_csv(session, filters)
                    st.session_state["ia_csv_inv"] = paths["invoices"]
                    st.session_state["ia_csv_items"] = paths["items"]
        with c2:
            if st.button("Export Line Items CSV", use_container_width=True):
                with session_factory() as session:
                    from services.export_service import export_csv

                    paths = export_csv(session, filters)
                    st.session_state["ia_csv_inv"] = paths["invoices"]
                    st.session_state["ia_csv_items"] = paths["items"]
        if "ia_csv_inv" in st.session_state:
            inv_bytes = b""
            item_bytes = b""
            with open(st.session_state["ia_csv_inv"], "rb") as fh:
                inv_bytes = fh.read()
            with open(st.session_state["ia_csv_items"], "rb") as fh:
                item_bytes = fh.read()
            stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            c3, c4 = st.columns(2)
            c3.download_button("Download Invoices CSV", data=inv_bytes, file_name=f"invoices_{stamp}.csv", mime="text/csv", use_container_width=True)
            c4.download_button("Download Line Items CSV", data=item_bytes, file_name=f"invoice_items_{stamp}.csv", mime="text/csv", use_container_width=True)

    with st.container(border=True):
        section_title("Complete Report (all invoices → Excel)")
        st.caption(
            "One row per line item across every invoice — Invoice Number, Vendor Name, Ship to "
            "Address, SKU, Product Name, Cost Price (per vendor logic), Quantity."
        )
        c1, c2 = st.columns(2)
        if c1.button("Generate Complete Report", use_container_width=True):
            with session_factory() as session:
                data = complete_report_bytes(session)
            st.session_state["ia_complete_bytes"] = data
            st.rerun()
        if "ia_complete_bytes" in st.session_state:
            stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            c2.download_button(
                "Download Complete Report",
                data=st.session_state["ia_complete_bytes"],
                file_name=f"invoiceai_complete_report_{stamp}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
            st.session_state["ia_complete_bytes"] = None

    st.markdown(
        """
        <style>
        div[data-testid="stDataFrame"] [data-testid="stColumnHeader"] { font-weight: 700; }
        </style>
        """,
        unsafe_allow_html=True,
    )