"""Dashboard page: KPI cards, recent invoices, vendor summary, trend."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from database import repository
from ui.common import badge, kpi_card, money, section_title

KPI = [
    ("Invoices", "invoice_count", "invoices processed", "#172b4d"),
    ("Line Items", "item_count", "items extracted", "#1a73e8"),
    ("Vendors", "vendor_count", "distinct vendors", "#0d47a1"),
    ("Total Invoice Value", "total_value", "money", "#1f8b4c"),
    ("Total Tax", "total_tax", "money", "#b8860b"),
    ("Duplicate Invoices", "duplicate_count", "potential duplicates", "#e65100"),
    ("Processing Errors", "error_count", "failed files", "#c62828"),
]


def _render_kpis(stats: dict) -> None:
    cols = st.columns(4)
    for idx, (label, key, kind, color) in enumerate(KPI):
        value = stats[key]
        if kind == "money":
            display = money(value) if value else "₹ 0.00"
            sub = f"₹ {value / 100000:.2f} Lakh" if value else ""
        else:
            display = f"{value:,}" if value else "0"
            sub = kind if kind != "money" else ""
        with cols[idx % 4]:
            kpi_card(label, display, sub, color)


def render(session_factory) -> None:
    with session_factory() as session:
        stats = repository.dashboard_stats(session)
        recent = repository.recent_invoices(session, limit=8)
        vendors = repository.vendor_report(session)
        trend = repository.monthly_trend(session)

    section_title("Overview")
    _render_kpis(stats)

    st.markdown("<br>", unsafe_allow_html=True)
    col_left, col_right = st.columns([3, 2])

    with col_left:
        st.markdown('<div class="ia-card">', unsafe_allow_html=True)
        section_title("Recent Invoices")
        if recent:
            df = pd.DataFrame(recent)
            display = df[["invoice_number", "invoice_date", "vendor_name", "grand_total", "status"]].copy()
            display.columns = ["Invoice No", "Date", "Vendor", "Amount", "Status"]
            display["Amount"] = display["Amount"].apply(lambda v: money(v))
            st.dataframe(display, use_container_width=True, hide_index=True)
        else:
            st.info("No invoices processed yet. Go to **Upload** to add invoices.")
        st.markdown("</div>", unsafe_allow_html=True)

    with col_right:
        st.markdown('<div class="ia-card">', unsafe_allow_html=True)
        section_title("Vendor Summary")
        if vendors:
            vdf = pd.DataFrame(vendors)[["vendor", "invoice_count", "total_value"]].head(8)
            vdf.columns = ["Vendor", "Invoices", "Value"]
            vdf["Value"] = vdf["Value"].apply(lambda v: money(v))
            st.dataframe(vdf, use_container_width=True, hide_index=True)
        else:
            st.info("No vendor data yet.")
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown('<div class="ia-card">', unsafe_allow_html=True)
    section_title("Invoice Value Trend (Monthly)")
    if trend:
        tdf = pd.DataFrame(trend)
        tdf.columns = ["Month", "Invoices", "Value", "Tax"]
        st.line_chart(tdf.set_index("Month")["Value"])
        tdf["Value"] = tdf["Value"].apply(lambda v: money(v))
        st.dataframe(tdf, use_container_width=True, hide_index=True)
    else:
        st.info("No data yet — processing history will appear here.")
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown('<div class="ia-card">', unsafe_allow_html=True)
    section_title("Processing Status")
    cols = st.columns(4)
    cols[0].metric("Total Invoices", f'{stats["invoice_count"]:,}')
    cols[1].metric("Validated (Matched)", f'{stats["validated"]:,}')
    cols[2].metric("Needs Review / Failed", f'{stats["needs_review"]:,}')
    cols[3].metric("Duplicates Flagged", f'{stats["duplicate_count"]:,}')
    st.markdown("</div>", unsafe_allow_html=True)