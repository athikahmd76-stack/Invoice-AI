"""Shared UI helpers: CSS, badges, layout primitives."""

from __future__ import annotations

import streamlit as st

from utils import formatting as fmt

STATUS_COLORS = {
    "Validated": "#1f8b4c",
    "Matched": "#1f8b4c",
    "Extracted": "#1a73e8",
    "Needs Review": "#b8860b",
    "Mismatch": "#c62828",
    "Failed": "#c62828",
    "Imported": "#6a1b9a",
    "Duplicate": "#e65100",
    "Ignored": "#546e7a",
    "None": "#546e7a",
    "Processing": "#1a73e8",
    "Pending": "#90a4ae",
    "Success": "#1f8b4c",
    "Skipped": "#546e7a",
}


def inject_css() -> None:
    st.markdown(
        """
        <style>
        :root {
            --ia-blue: #1a73e8;
            --ia-dark: #172b4d;
            --ia-border: #e3e8ef;
            --ia-bg: #f7f9fc;
        }
        .stApp { background: var(--ia-bg); }
        [data-testid="stHeader"] { background: rgba(247,249,252,0.92); }
        [data-testid="stSidebar"] { background: #ffffff; border-right: 1px solid var(--ia-border); }

        .ia-header { display:flex; align-items:center; gap:14px; padding:10px 0 4px 0; }
        .ia-logo {
            width:44px; height:44px; border-radius:10px;
            background: linear-gradient(135deg, #1a73e8, #0d47a1);
            color:#fff; display:flex; align-items:center; justify-content:center;
            font-weight:800; font-size:20px; letter-spacing:.5px;
        }
        .ia-title { font-size:26px; font-weight:800; color:var(--ia-dark); line-height:1.1; }
        .ia-subtitle { font-size:13px; color:#5f6b7a; margin-top:2px; }

        .ia-card {
            background:#fff; border:1px solid var(--ia-border); border-radius:12px;
            padding:18px 20px; box-shadow:0 1px 3px rgba(23,43,77,.05);
        }
        .ia-kpi-label { font-size:12px; font-weight:600; color:#5f6b7a; text-transform:uppercase; letter-spacing:.4px; }
        .ia-kpi-value { font-size:30px; font-weight:800; color:var(--ia-dark); margin-top:6px; }
        .ia-kpi-sub { font-size:12px; color:#8a94a6; margin-top:2px; }

        .ia-badge {
            display:inline-block; padding:2px 10px; border-radius:999px;
            font-size:12px; font-weight:600; white-space:nowrap;
        }
        .ia-check-item { padding:6px 0; font-size:14px; border-bottom:1px solid #f0f2f5; }
        .ia-check-ok { color:#1f8b4c; font-weight:600; }
        .ia-check-bad { color:#c62828; font-weight:600; }
        .ia-muted { color:#8a94a6; font-size:13px; }

        .ia-section-title { font-size:16px; font-weight:700; color:var(--ia-dark); margin:8px 0 10px 0; }
        div[data-testid="stMetricValue"] { font-size:26px; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def app_header() -> None:
    st.markdown(
        """
        <div class="ia-header">
            <div class="ia-logo">IA</div>
            <div>
                <div class="ia-title">InvoiceAI</div>
                <div class="ia-subtitle">AI-powered invoice extractor &nbsp;•&nbsp; 100% local, private &amp; free</div>
            </div>
        </div>
        <hr style="border:none;border-top:1px solid var(--ia-border);margin:10px 0 18px 0;">
        """,
        unsafe_allow_html=True,
    )


def badge(status: str | None) -> str:
    status = status or ""
    color = STATUS_COLORS.get(status, "#546e7a")
    bg = {"#ffffff": "#fff"}.get(color, color + "1a")
    return f'<span class="ia-badge" style="color:{color};background:{bg};">{status or "—"}</span>'


def kpi_card(label: str, value: str, sub: str = "", accent: str = "#172b4d") -> None:
    st.markdown(
        f"""
        <div class="ia-card">
            <div class="ia-kpi-label">{label}</div>
            <div class="ia-kpi-value" style="color:{accent};">{value}</div>
            <div class="ia-kpi-sub">{sub}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def section_title(text: str) -> None:
    st.markdown(f'<div class="ia-section-title">{text}</div>', unsafe_allow_html=True)


def money(value: float | None) -> str:
    return fmt.inr(value)


def conf_badge(score: int | None) -> str:
    label = fmt.confidence_label(score)
    color = fmt.confidence_color(label)
    return f'<span class="ia-badge" style="color:{color};background:{color}1a;">{label} {score if score is not None else ""}</span>'


def dataframe_with_badges(df, status_cols: tuple[str, ...] = ()) -> None:
    """Minimal wrapper: render a dataframe (badges are handled per-page where needed)."""
    st.dataframe(df, use_container_width=True, hide_index=True)