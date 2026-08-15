"""Upload page: drag & drop, queue, progress, retry failed."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from config import AppConfig
from database import repository
from database.database import Database
from services.processor import process_file, retry_failed_log
from ui.common import section_title
from utils.file_utils import is_allowed_file, save_upload

QUEUE_KEY = "invoiceai_queue"
ALLOWED_TYPES = ["pdf", "jpg", "jpeg", "png", "webp"]

ICONS = {"waiting": "○", "processing": "⏳", "done": "✓", "failed": "✗", "skipped": "⏭"}


def _queue() -> list[dict]:
    if QUEUE_KEY not in st.session_state:
        st.session_state[QUEUE_KEY] = []
    return st.session_state[QUEUE_KEY]


def _add_files(files, config: AppConfig) -> None:
    queue = _queue()
    existing_names = {item["name"] for item in queue}
    added = 0
    for uploaded in files:
        if uploaded.name in existing_names:
            continue
        item = {
            "name": uploaded.name,
            "status": "waiting",
            "message": "",
            "invoice_id": None,
        }
        if not is_allowed_file(uploaded.name):
            item["status"] = "failed"
            item["message"] = "Unsupported file type"
        else:
            data = uploaded.getvalue()
            if len(data) > config.max_file_size_mb * 1024 * 1024:
                item["status"] = "failed"
                item["message"] = f"Exceeds {config.max_file_size_mb} MB limit"
        queue.append(item)
        existing_names.add(uploaded.name)
        added += 1
    if added:
        st.session_state.pop("invoiceai_queue_view", None)


def _queue_table() -> None:
    queue = _queue()
    total = len(queue)
    st.markdown(f"**Files Selected: {total}**", unsafe_allow_html=True)
    if not queue:
        return
    rows = []
    for item in queue:
        icon = ICONS.get(item["status"], "○")
        message = item["message"]
        rows.append(f"{icon} &nbsp;{item['name']}&nbsp;&nbsp;<span style='color:#8a94a6;font-size:12px'>{message}</span>")
    st.markdown("<br>".join(rows), unsafe_allow_html=True)


def _render_queue_preview() -> None:
    with st.container(border=True):
        section_title("Processing Queue")
        _queue_table()


def _run_processing(config: AppConfig, database: Database, only_failed: bool = False) -> dict:
    queue = _queue()
    processed = 0
    successful = 0
    failed = 0
    skipped = 0

    targets = []
    for item in queue:
        if only_failed and item["status"] != "failed":
            continue
        if item["status"] in ("waiting", "failed"):
            targets.append(item)

    if not targets:
        st.info("Nothing to process.")
        return {"processed": 0, "successful": 0, "failed": 0, "skipped": 0}

    progress_bar = st.progress(0.0, text="Starting...")
    for idx, item in enumerate(targets):
        progress_bar.progress(idx / len(targets), text=f"Processing {item['name']}...")
        item["status"] = "processing"
        item["message"] = ""
        try:
            with st.status(f"Processing: {item['name']}", expanded=False) as status:
                stored = next((u for u in st.session_state.get("_invoiceai_raw_files", []) if u.get("name") == item["name"]), None)
                if stored is None or stored.get("bytes") is None:
                    raise RuntimeError("Uploaded file data no longer available. Please re-upload.")
                path = save_upload(item["name"], stored["bytes"], config.upload_dir)

                def cb(step: int, total: int, stage: str) -> None:
                    status.update(
                        label=f"{item['name']} — step {step}/{total}: {stage}",
                        state="running",
                    )

                result = process_file(config, database, path, original_name=item["name"], progress_cb=cb)
            if result.ok and result.skipped:
                item["status"] = "skipped"
                item["invoice_id"] = result.invoice_id
                item["message"] = result.message
                skipped += 1
            elif result.ok:
                item["status"] = "done"
                item["invoice_id"] = result.invoice_id
                item["message"] = result.message
                successful += 1
            else:
                item["status"] = "failed"
                item["message"] = result.error or "Unknown error"
                failed += 1
        except Exception as exc:  # noqa: BLE001
            item["status"] = "failed"
            item["message"] = str(exc)
            failed += 1
        processed += 1
    progress_bar.progress(1.0, text="Done")

    summary = {"processed": processed, "successful": successful, "failed": failed, "skipped": skipped}
    st.success(
        f"Processed: {processed} &nbsp;•&nbsp; Successful: {successful} &nbsp;•&nbsp; Failed: {failed} &nbsp;•&nbsp; Skipped (already processed): {skipped}",
    )
    return summary


def render(config: AppConfig, database: Database) -> None:
    queue = _queue()

    if "_invoiceai_raw_files" not in st.session_state:
        st.session_state["_invoiceai_raw_files"] = []

    st.markdown(
        """
        <div class="ia-card" style="border:2px dashed #b9c6d8; text-align:center; padding:42px 24px;">
            <div style="font-size:17px; font-weight:700; color:#172b4d;">Drop invoices here or click to upload</div>
            <div style="color:#8a94a6; font-size:13px; margin-top:6px;">PDF, JPG, JPEG, PNG, WEBP &nbsp;•&nbsp; Multiple invoices supported &nbsp;•&nbsp; Processed 100% locally</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    files = st.file_uploader(
        "Choose Files",
        type=ALLOWED_TYPES,
        accept_multiple_files=True,
        label_visibility="collapsed",
        key="invoiceai_uploader",
    )
    if files:
        added_now = 0
        for uploaded in files:
            if not any(stored.get("name") == uploaded.name for stored in st.session_state["_invoiceai_raw_files"]):
                st.session_state["_invoiceai_raw_files"].append({"name": uploaded.name, "bytes": uploaded.getvalue()})
                added_now += 1
        _add_files(files, config)
        if added_now:
            st.success(f"{added_now} file(s) added to the queue.")

    _render_queue_preview()

    col1, col2, col3 = st.columns([1, 1, 3])
    with col1:
        if st.button("Start Processing", type="primary", use_container_width=True, disabled=not any(q["status"] == "waiting" for q in queue)):
            summary = _run_processing(config, database)
            st.session_state["invoiceai_last_summary"] = summary
            st.rerun()
    with col2:
        failed_items = [q for q in queue if q["status"] == "failed"]
        if st.button("Retry Failed", use_container_width=True, disabled=not failed_items):
            summary = _run_processing(config, database, only_failed=True)
            st.session_state["invoiceai_last_summary"] = summary
            st.rerun()
    with col3:
        if st.button("Clear Queue", use_container_width=False):
            st.session_state[QUEUE_KEY] = []
            st.session_state["_invoiceai_raw_files"] = []
            st.rerun()

    if "invoiceai_last_summary" in st.session_state:
        summary = st.session_state["invoiceai_last_summary"]
        st.markdown("<br>", unsafe_allow_html=True)
        with st.container(border=True):
            section_title("Last Batch Result")
            cols = st.columns(4)
            cols[0].metric("Processed", summary["processed"])
            cols[1].metric("Successful", summary["successful"])
            cols[2].metric("Failed", summary["failed"])
            cols[3].metric("Skipped", summary["skipped"])
        st.markdown(
            "Failed files stay in the queue — correct the problem and press **Retry Failed**. "
            "New results appear in **Records**.",
            unsafe_allow_html=True,
        )

    _render_history_failed(config, database)


def _render_history_failed(config: AppConfig, database: Database) -> None:
    """Failed files from all sessions (persisted in processing_logs) with retry buttons."""
    with database.session() as session:
        failed = repository.failed_logs(session, limit=50)

    if not failed:
        return

    st.markdown("<br>", unsafe_allow_html=True)
    with st.container(border=True):
        section_title(f"Failed Files — Retry (persisted history, {len(failed)})")
        st.markdown(
            "These files failed in previous sessions and are still available on disk. "
            "Fix the cause (Ollama running, model installed, OCR available) and retry.",
            unsafe_allow_html=True,
        )
        col_a, _ = st.columns([1, 3])
        if col_a.button("Retry All Failed", type="primary", use_container_width=True):
            retried = 0
            with st.spinner("Retrying failed files…"):
                for entry in failed:
                    invoice_id = retry_failed_log(database, entry["id"], config)
                    retried += 1 if invoice_id else 0
            st.success(f"Retry round finished: {retried} invoice(s) processed successfully.")
            st.rerun()

        st.markdown("<br>", unsafe_allow_html=True)
        for entry in failed[:10]:
            c1, c2, c3 = st.columns([4, 2, 1])
            c1.markdown(f"**{entry['file_name']}**<br><span class='ia-muted'>{entry['error_message'] or ''}</span>", unsafe_allow_html=True)
            c2.markdown(f"<span class='ia-muted'>{entry['start_time'] or ''}</span>", unsafe_allow_html=True)
            if c3.button("Retry", key=f"retry_log_{entry['id']}", use_container_width=True):
                with st.spinner(f"Retrying {entry['file_name']}…"):
                    invoice_id = retry_failed_log(database, entry["id"], config)
                if invoice_id:
                    st.success(f"{entry['file_name']} → invoice #{invoice_id}")
                else:
                    st.error(f"Retry failed for {entry['file_name']} — see processing log.")
                st.rerun()