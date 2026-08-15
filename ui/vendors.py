"""Vendors page: vendor list with aggregates + detail."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from database import repository
from ui.common import section_title


def render(session_factory) -> None:
    section_title("Vendors")

    search = st.text_input("Search vendors (name, GSTIN, PAN)", placeholder="Search…")
    with session_factory() as session:
        vendors = repository.list_vendors(session, search or None)
        report = repository.vendor_report(session)
        stats = repository.dashboard_stats(session)

    cols = st.columns(4)
    cols[0].metric("Vendors", f'{stats["vendor_count"]:,}')
    cols[1].metric("Invoices", f'{stats["invoice_count"]:,}')
    cols[2].metric("Total Value", f"₹ {stats['total_value']:,.0f}")
    cols[3].metric("Total Tax", f"₹ {stats['total_tax']:,.0f}")

    st.markdown("<br>", unsafe_allow_html=True)
    with st.container(border=True):
        section_title("Vendor Report")
        if report:
            df = pd.DataFrame(report)
            df.columns = ["Vendor", "Invoices", "Purchase Value", "Tax", "Avg Invoice Value"]
            df["Purchase Value"] = df["Purchase Value"].apply(lambda v: f"₹ {v:,.2f}" if v is not None else "—")
            df["Tax"] = df["Tax"].apply(lambda v: f"₹ {v:,.2f}" if v is not None else "—")
            df["Avg Invoice Value"] = df["Avg Invoice Value"].apply(lambda v: f"₹ {v:,.2f}" if v is not None else "—")
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("No vendor data yet.")

    st.markdown("<br>", unsafe_allow_html=True)
    with st.container(border=True):
        section_title("Vendor Master")
        if vendors:
            rows = [
                {
                    "Name": v.name or "—",
                    "Legal Name": v.legal_name or "—",
                    "GSTIN": v.gstin or "—",
                    "PAN": v.pan or "—",
                    "Phone": v.phone or "—",
                    "Email": v.email or "—",
                }
                for v in vendors
            ]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.info("No vendors found.")