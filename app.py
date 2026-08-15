"""InvoiceAI — Streamlit application entry point.

Run:  streamlit run app.py
"""

from __future__ import annotations

import streamlit as st

from config import get_config
from database.database import Database
from services import ai_service, ocr_service
from utils.logging_utils import setup_logger

st.set_page_config(
    page_title="InvoiceAI — AI-powered invoice extractor",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_resource(show_spinner=False)
def init_app():
    config = get_config()
    config.ensure_dirs()
    logger = setup_logger(config)
    database = Database(config)
    database.init_db()
    return config, database, logger


config, database, logger = init_app()

from ui.common import app_header, inject_css  # noqa: E402

inject_css()

NAV_ITEMS = ["Dashboard", "Upload", "Records", "Vendors", "Reports", "Settings"]


@st.cache_data(ttl=20, show_spinner=False)
def _ollama_status_cached(base_url: str, model: str) -> tuple[bool, list[str]]:
    client = ai_service.OllamaClient(get_config())
    return client.ping(), client.list_models()


def ollama_status() -> tuple[bool, list[str]]:
    return _ollama_status_cached(config.ollama_base_url, config.ollama_model)


def startup_status() -> str:
    running, models = ollama_status()
    model_ok = config.ollama_model in models
    if not running:
        return "Ollama is not running. Please start Ollama and make sure the selected model is installed."
    if not model_ok:
        return f"Ollama is running, but the model '{config.ollama_model}' is not installed. Run: ollama pull {config.ollama_model}"
    return ""


def render_sidebar() -> None:
    with st.sidebar:
        st.markdown(
            """
            <div style="display:flex;align-items:center;gap:10px;margin-bottom:14px;">
                <div style="width:34px;height:34px;border-radius:8px;background:linear-gradient(135deg,#1a73e8,#0d47a1);color:#fff;display:flex;align-items:center;justify-content:center;font-weight:800;">IA</div>
                <div style="font-size:17px;font-weight:800;color:#172b4d;">InvoiceAI</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        running, models_now = ollama_status()
        model_ok = config.ollama_model in models_now
        dot = "#1f8b4c" if (running and model_ok) else "#c62828"
        st.markdown(
            f'<div style="font-size:12px;color:#5f6b7a;margin-bottom:10px;">'
            f'<span style="color:{dot};">●</span> Ollama {"ready" if running else "offline"} &nbsp;•&nbsp; {config.ollama_model}<br>'
            f'<span style="color:#8a94a6;">{config.ollama_base_url}</span></div>',
            unsafe_allow_html=True,
        )

        target = st.session_state.get("ia_page_target", "Dashboard")
        current = "Invoice" if target == "Invoice" else target
        selected = st.radio("Navigation", NAV_ITEMS, index=NAV_ITEMS.index(current) if current in NAV_ITEMS else 0, label_visibility="collapsed")
        st.session_state["ia_nav"] = selected
        st.session_state["ia_page_target"] = selected

        st.markdown(
            """
            <hr style="border:none;border-top:1px solid #e3e8ef;">
            <div style="font-size:11px;color:#8a94a6;line-height:1.6;">
            100% local processing<br>
            PaddleOCR + Ollama + SQLite<br>
            No cloud AI — invoices never leave this computer
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_page() -> None:
    target = st.session_state.get("ia_page_target", "Dashboard")

    if target == "Invoice":
        invoice_id = st.session_state.get("ia_invoice_id")
        if invoice_id:
            from ui.invoice_details import render as render_details

            render_details(int(invoice_id), database.session)
        else:
            st.session_state["ia_page_target"] = "Records"
            st.rerun()
        return

    if target == "Dashboard":
        from ui.dashboard import render as render_dashboard

        render_dashboard(database.session)
    elif target == "Upload":
        from ui.upload import render as render_upload

        render_upload(config, database)
    elif target == "Records":
        from ui.records import render as render_records

        render_records(database.session)
    elif target == "Vendors":
        from ui.vendors import render as render_vendors

        render_vendors(database.session)
    elif target == "Reports":
        from ui.reports import render as render_reports

        render_reports(database.session)
    elif target == "Settings":
        from ui.settings import render as render_settings

        render_settings(config, database)
    else:
        from ui.dashboard import render as render_dashboard

        render_dashboard(database.session)


def main() -> None:
    render_sidebar()
    app_header()

    warning = startup_status()
    if warning:
        st.error(warning)
        st.info("You can still use the app. Uploads will queue; processing starts once Ollama is running. Check **Settings → System Checks** for details and model installation help.")

    with database.session() as session:
        from database import repository

        stats = repository.dashboard_stats(session)

    if stats["invoice_count"] == 0:
        st.info("No invoices yet — start by uploading invoices on the **Upload** page, or import an invoice master on **Settings → Database**.")

    render_page()


if __name__ == "__main__":
    main()