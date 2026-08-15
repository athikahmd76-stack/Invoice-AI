"""Reports page: vendor, monthly, SKU, GST analysis."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from database import repository
from ui.common import money, section_title


def render(session_factory) -> None:
    section_title("Reports")

    with session_factory() as session:
        vendors = repository.vendor_report(session)
        monthly = repository.monthly_trend(session)
        skus = repository.sku_report(session)
        tax = repository.tax_summary(session)

    t1, t2 = st.tabs(["Vendor Analysis", "GST / Tax Analysis"])

    with t1:
        col_l, col_r = st.columns([3, 2])
        with col_l:
            with st.container(border=True):
                section_title("Vendor Report")
                if vendors:
                    vdf = pd.DataFrame(vendors)
                    vdf.columns = ["Vendor", "Invoices", "Purchase Value", "Tax", "Avg Invoice Value"]
                    vdf["Purchase Value"] = vdf["Purchase Value"].apply(lambda v: money(v))
                    vdf["Tax"] = vdf["Tax"].apply(lambda v: money(v))
                    vdf["Avg Invoice Value"] = vdf["Avg Invoice Value"].apply(lambda v: money(v))
                    st.dataframe(vdf, use_container_width=True, hide_index=True)
                else:
                    st.info("No vendor data yet.")
        with col_r:
            with st.container(border=True):
                section_title("Invoice Value by Vendor")
                if vendors:
                    pv = pd.DataFrame(vendors).set_index("vendor")["total_value"].dropna()
                    st.bar_chart(pv)
                else:
                    st.info("No data.")

    with t2:
        col_l, col_r = st.columns([3, 2])
        with col_l:
            with st.container(border=True):
                section_title("GST Report")
                gdf = pd.DataFrame([
                    {"Component": "CGST", "Amount": tax["cgst"]},
                    {"Component": "SGST", "Amount": tax["sgst"]},
                    {"Component": "IGST", "Amount": tax["igst"]},
                    {"Component": "UTGST", "Amount": tax["utgst"]},
                    {"Component": "CESS", "Amount": tax["cess"]},
                    {"Component": "Total GST", "Amount": tax["total"]},
                ])
                gdf["Amount"] = gdf["Amount"].apply(lambda v: money(v))
                st.dataframe(gdf, use_container_width=True, hide_index=True)
        with col_r:
            with st.container(border=True):
                section_title("Tax Mix")
                if tax["total"]:
                    pie = pd.DataFrame(
                        {"Component": ["CGST", "SGST", "IGST", "UTGST", "CESS"], "Amount": [tax["cgst"], tax["sgst"], tax["igst"], tax["utgst"], tax["cess"]]}
                    )
                    st.bar_chart(pie.set_index("Component"))
                else:
                    st.info("No tax data.")

    st.markdown("<br>", unsafe_allow_html=True)
    with st.container(border=True):
        section_title("Monthly Report")
        if monthly:
            mdf = pd.DataFrame(monthly)
            mdf.columns = ["Month", "Invoices", "Purchase Value", "Tax"]
            chart = mdf.copy()
            st.bar_chart(chart.set_index("Month")[["Purchase Value", "Tax"]])
            mdf["Purchase Value"] = mdf["Purchase Value"].apply(lambda v: money(v))
            mdf["Tax"] = mdf["Tax"].apply(lambda v: money(v))
            st.dataframe(mdf, use_container_width=True, hide_index=True)
        else:
            st.info("No monthly data yet.")

    st.markdown("<br>", unsafe_allow_html=True)
    with st.container(border=True):
        section_title("SKU Report")
        if skus:
            sdf = pd.DataFrame(skus)
            sdf.columns = ["SKU", "Product Name", "Invoices", "Quantity", "Purchase Value"]
            sdf["Purchase Value"] = sdf["Purchase Value"].apply(lambda v: money(v))
            st.dataframe(sdf, use_container_width=True, hide_index=True)
        else:
            st.info("No SKU data yet — line items with SKU codes will appear here.")

    st.markdown("<br>", unsafe_allow_html=True)
    with st.container(border=True):
        section_title("Complete Report (all invoices → Excel)")
        st.caption(
            "One row per line item across every invoice — Invoice Number, Date, Vendor, PO, Bill To, "
            "SKU / Product Code / HSN, Cost Price, Quantity, Unit Price, Taxable Value, Tax Rate, Line Total."
        )
        with session_factory() as session:
            from services.export_service import complete_report_bytes

            st.download_button(
                "Download Complete Report",
                data=complete_report_bytes(session),
                file_name=f"invoiceai_complete_report_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )