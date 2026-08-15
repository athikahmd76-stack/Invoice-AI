"""Settings page: Ollama, model, OCR, limits, database, export, system checks."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import streamlit as st

from config import AppConfig
from database.database import Database
from services import ai_service, ocr_service
from ui.common import section_title


def _check_row(label: str, ok: bool, detail: str = "") -> str:
    icon = "✓" if ok else "✗"
    color = "#1f8b4c" if ok else "#c62828"
    return f'<div class="ia-check-item"><span style="color:{color};font-weight:700;">{icon}</span> &nbsp;{label} &nbsp;<span class="ia-muted">{detail}</span></div>'


def render(config: AppConfig, database: Database) -> None:
    section_title("Settings")

    tab_model, tab_ocr, tab_files, tab_db, tab_system = st.tabs(
        ["AI / Ollama", "OCR", "File Limits", "Database", "System Checks"]
    )

    # ------------------------------------------------------------------- AI
    with tab_model:
        client = ai_service.OllamaClient(config)
        with st.container(border=True):
            section_title("Ollama Connection")
            base_url = st.text_input("Ollama Base URL", value=config.ollama_base_url)

            models = []
            if st.button("Refresh Model List") or "ia_models" not in st.session_state:
                models = client.list_models()
                st.session_state["ia_models"] = models
            models = st.session_state.get("ia_models", [])
            if not client.ping():
                st.warning("**Ollama is not running.**\n\nPlease start Ollama and make sure the selected model is installed.")
            model_options = [config.ollama_model] + [m for m in models if m != config.ollama_model]
            model = st.selectbox("AI Model", model_options, index=0)

            mode_map = {
                "auto": "Auto (text when the PDF has a text layer, else vision)",
                "vision": "Vision (send page images - slower, for scans)",
                "text": "Text only (OCR text - fastest on CPU)",
            }
            mode = st.selectbox("AI Mode", options=list(mode_map.keys()), index=list(mode_map.keys()).index(config.ai_mode), format_func=lambda k: mode_map[k])
            temperature = st.slider("Temperature", 0.0, 1.0, config.ai_temperature, 0.05)
            timeout = st.number_input("AI Timeout (seconds)", min_value=60, value=config.ai_timeout_seconds, step=30)
            max_tokens = st.number_input("Max output tokens (lower = faster)", min_value=512, max_value=12000, value=int(config.ai_max_tokens), step=512)
            num_ctx = st.number_input("Context window (num_ctx)", min_value=2048, value=int(config.ai_num_ctx), step=2048)

            if st.button("Save AI Settings", type="primary"):
                config.save_runtime(
                    ollama_base_url=base_url.strip(),
                    ollama_model=model,
                    ai_mode=mode,
                    ai_temperature=float(temperature),
                    ai_timeout_seconds=int(timeout),
                    ai_max_tokens=int(max_tokens),
                    ai_num_ctx=int(num_ctx),
                )
                st.session_state.pop("ia_models", None)
                st.rerun()

        with st.container(border=True):
            section_title("Model Installation Helper")
            st.markdown(
                """
                If the model is missing, run in a terminal:
                ```
                ollama pull qwen3:1.7b    # FASTEST - best on CPU-only laptops (~1-2 min)
                ollama pull qwen3:4b      # more accurate, ~2.5x slower
                ollama pull gemma3:4b     # vision - reads scanned PDFs/images directly
                ```
                Tip: text-layer invoices use qwen3 models automatically. Use gemma3:4b for scans.
                """
            )
            if st.button("Run: ollama pull (current model)"):
                try:
                    result = subprocess.run(
                        ["ollama", "pull", config.ollama_model],
                        capture_output=True, text=True, timeout=3600,
                    )
                    if result.returncode == 0:
                        st.success(f"Model '{config.ollama_model}' installed/updated.")
                    else:
                        st.error(f"ollama pull failed: {result.stderr[-500:]}")
                except FileNotFoundError:
                    st.error("Ollama CLI not found. Install Ollama from https://ollama.com first.")

    # ------------------------------------------------------------------- OCR
    with tab_ocr:
        engine_map = {
            "auto": "Auto — PaddleOCR if installed, else PDF text layer",
            "paddle": "PaddleOCR (best for scanned docs; needs paddlepaddle)",
            "text": "PDF text layer only (fast, digital PDFs)",
            "none": "No OCR — AI vision reads page images directly",
        }
        engine = st.selectbox("OCR Engine", options=list(engine_map.keys()), index=list(engine_map.keys()).index(config.ocr_engine if config.ocr_engine in engine_map else "auto"), format_func=lambda k: engine_map[k])
        language = st.text_input("OCR Language", value=config.ocr_language)
        enable_table = st.checkbox("Enable table structure extraction (best effort)", value=config.ocr_enable_table)
        st.markdown("**PaddleOCR status:** " + ("Installed ✓" if ocr_service.paddle_available() else "Not installed — using text layer / vision mode"))
        if not ocr_service.paddle_available():
            st.info("Install PaddleOCR (Python 3.10–3.12 recommended):\n\n`pip install paddlepaddle paddleocr`")
        if st.button("Save OCR Settings", type="primary"):
            config.save_runtime(ocr_engine=engine, ocr_language=language.strip() or "en", ocr_enable_table=bool(enable_table))
            st.rerun()

    # ------------------------------------------------------------------ files
    with tab_files:
        max_mb = st.number_input("Maximum file size (MB)", min_value=1, value=config.max_file_size_mb)
        st.caption("Allowed types: PDF, JPG, JPEG, PNG, WEBP. Multiple files and multi-page PDFs supported.")
        if st.button("Save File Limits", type="primary"):
            config.save_runtime(max_file_size_mb=int(max_mb))
            st.rerun()

    # ---------------------------------------------------------------------- DB
    with tab_db:
        st.markdown(f"**Database file:** `{database.db_path}`")
        st.markdown(f"**Size:** {database.db_size_mb:.2f} MB")
        c1, c2 = st.columns(2)
        if c1.button("Compose Database (recreate empty tables)"):
            with database.session() as session:
                from sqlalchemy import text

                session.execute(text("VACUUM"))
                session.commit()
            st.success("Database vacuumed.")
        if c2.button("Open Exports Folder"):
            config.export_dir.mkdir(parents=True, exist_ok=True)
            subprocess.Popen(["explorer", str(config.export_dir.resolve())])
        with st.container(border=True):
            section_title("Excel Import (invoice master)")
            imported = st.file_uploader("Upload a .xlsx invoice master", type=["xlsx", "xls"], key="ia_import_file")
            if imported:
                from services.export_service import import_invoices_excel

                temp_dir = Path(config.tmp_dir)
                temp_dir.mkdir(parents=True, exist_ok=True)
                path = temp_dir / f"import_{imported.name}"
                path.write_bytes(imported.getvalue())
                with database.session() as session:
                    outcome = import_invoices_excel(session, path)
                st.success(f"Imported: {outcome['imported']}  •  Skipped: {outcome['skipped']}")
                if outcome["errors"]:
                    with st.expander("Import errors"):
                        for err in outcome["errors"][:20]:
                            st.markdown(f"- {err}")

        with st.container(border=True):
            section_title("Clear All Data")
            st.markdown(
                "Permanently deletes **all** records (invoices, line items, vendors, logs, "
                "uploaded-file history) and removes stored files (uploads, previews, temp) to free up "
                "storage. **This cannot be undone** — download any reports you need first."
            )
            confirm = st.checkbox("I understand that all data will be permanently deleted", key="ia_clear_all_confirm")
            if st.button("Clear All Data", type="secondary", disabled=not confirm):
                from database import repository
                from sqlalchemy import text

                size_before = database.db_size_mb
                with database.session() as session:
                    counts = repository.clear_all_data(session)
                    session.commit()
                file_bytes = 0
                for folder in (config.upload_dir, config.preview_dir, config.tmp_dir):
                    if folder.exists():
                        for f in folder.iterdir():
                            if f.is_file():
                                try:
                                    file_bytes += f.stat().st_size
                                    f.unlink()
                                except OSError:
                                    pass
                with database.session() as session:
                    session.execute(text("VACUUM"))
                    session.commit()
                size_after = database.db_size_mb
                freed = (size_before - size_after) + file_bytes / (1024 * 1024)
                st.success(f"All data cleared. Freed {freed:.2f} MB of storage.")
                st.rerun()

    # --------------------------------------------------------------- system
    with tab_system:
        with st.container(border=True):
            section_title("Startup Checks")
            rows = []
            ollama_bin = shutil.which("ollama")
            rows.append(_check_row("Ollama installed", bool(ollama_bin), ollama_bin or "not found on PATH"))
            running = client.ping()
            rows.append(_check_row("Ollama service running", running, config.ollama_base_url))
            models_now = client.list_models()
            rows.append(_check_row(f"Model '{config.ollama_model}' installed", config.ollama_model in models_now, f"{len(models_now)} models available"))
            if config.ollama_model not in models_now and running:
                rows.append(_check_row("Install instructions", False, "ollama pull " + config.ollama_model))
            db_ok = database.db_path.parent.exists()
            rows.append(_check_row("Database connected", db_ok, str(database.db_path)))
            rows.append(
                _check_row(
                    "OCR engine ready",
                    ocr_service.paddle_available() or config.ocr_engine in ("text", "none"),
                    "PaddleOCR available" if ocr_service.paddle_available() else "text layer / vision mode",
                )
            )
            st.markdown("<br>".join(rows), unsafe_allow_html=True)
            if running and config.ollama_model in models_now:
                st.success("**InvoiceAI Ready** — all core services available.")
            else:
                st.error(
                    "**Ollama is not running.**\n\nPlease start Ollama and make sure the selected model is installed."
                )