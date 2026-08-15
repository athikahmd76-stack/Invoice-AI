# InvoiceAI — AI-powered Invoice Extractor

InvoiceAI is a **free, self-hosted, fully local** invoice processing application for Windows (and Docker).
It extracts structured invoice data (headers, line items, GST taxes, totals) from PDF / JPG / JPEG / PNG / WEBP
files using local OCR (**PaddleOCR**) and a local AI (**Ollama**), stores everything in **SQLite**, and lets you
search, review, validate, report and export to Excel/CSV.

> **Privacy guarantee:** invoices are processed entirely on your own machine.
> No Claude API, OpenAI API, Gemini API, Azure, AWS or Google cloud AI is used — ever.
> After installation and model download, the system works **offline**.

---

## 1. Quick Start (Windows)

### Prerequisites
- **Python 3.10 – 3.12** (recommended for PaddleOCR). Visit https://www.python.org/downloads/
- **Ollama** (local AI runtime). Install from https://ollama.com/download

### Option A — One-click start
Double-click **`start_invoiceai.bat`**. It will:
1. create the virtual environment and install dependencies (first run only)
2. verify Ollama is installed and running
3. download the AI model (`qwen3-vl:8b`) if missing
4. create all required folders
5. start Streamlit and open http://localhost:8501

### Option B — Manual
```bat
git clone <repository>
cd invoiceai
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
ollama pull qwen3-vl:8b
streamlit run app.py
```
Open: **http://localhost:8501**

### Ollama model setup
```bat
ollama pull qwen3:1.7b     :: FASTEST on CPU-only laptops (~1-2 min per invoice)
ollama pull qwen3:4b       :: more accurate, ~2-3x slower than 1.7b
ollama pull gemma3:4b      :: vision model - read scanned PDFs / photos directly
ollama pull qwen3-vl:8b    :: most accurate vision model (needs a capable machine)
```
**Model choice matters a lot on CPU-only hardware** (no GPU). `qwen3:1.7b` is the
default because it gives the best speed/accuracy balance for text-layer invoices.
For scanned documents (no text layer) the app automatically falls back to vision —
select `gemma3:4b` in Settings for that. You can switch models in **Settings → AI / Ollama**
or in `.env` (`OLLAMA_MODEL=...`). The app never hard-codes one model and never crashes
when Ollama is missing — it shows a clear message.

---

## 2. Usage

| Page | Purpose |
|---|---|
| **Dashboard** | KPI cards (invoices, line items, vendors, total value, total tax, duplicates, errors), recent invoices, vendor summary, monthly trend |
| **Upload** | Drag & drop or "Choose Files" — batch queue with live progress, retry failed files (including failures from previous sessions) |
| **Records** | Searchable, filterable invoice table (date range, vendor, status, duplicate, PO, GSTIN) with pagination, Excel/CSV export |
| **Vendors** | Vendor master and vendor report (count, value, tax, average) |
| **Reports** | Vendor analysis, GST/tax analysis, monthly report, SKU report |
| **Settings** | Ollama URL/model, AI mode, OCR engine/language, file limits, Excel import, system checks |

### Processing pipeline
```
upload → validation → PDF/image conversion → PaddleOCR (or PDF text layer)
   → Ollama (vision or text) → structured JSON → parsing + tax intelligence
   → validation/reconciliation → duplicate check → SQLite → dashboard → Excel/CSV
```

Vision models are auto-detected (`auto` mode): if the selected model supports images
(gemma3, qwen3-vl, llava …) page images are sent for maximum accuracy on scans.
If the model rejects images it automatically retries in text mode — processing
continues instead of failing.

### Validation & reconciliation
- `Subtotal + Tax − Discount + Round Off = Grand Total` (checked, never silently corrected)
- `Σ line totals vs invoice total`, `Qty × Rate vs line total`
- Discrepancies flagged (`⚠ Mismatch`) with expected/actual/difference; overall status:
  `Extracted` / `Validated` / `Needs Review` / `Failed`
- Confidence scores per field (`High / Medium / Low`) with low-confidence fields highlighted

### Duplicates
Fingerprint = `vendor GSTIN | invoice number`. Duplicates are flagged, never auto-deleted —
choose **Keep / Ignore / Delete** on the invoice detail page.

### Exports
- **Excel**: styled multi-sheet workbook — `Invoices`, `Invoice Items`, `Vendors`, `Tax Summary`, `Processing Log` (freeze panes, filters, currency & date formats)
- **CSV**: Invoices CSV + Line Items CSV
- **Import**: an existing invoice master `.xlsx` can be imported (Settings → Database)

---

## 3. Configuration

Copy `.env.example` to `.env` and edit:

```ini
APP_NAME=InvoiceAI
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3-vl:8b
AI_MODE=auto            # auto | vision | text (auto = fast text mode for text-layer PDFs, vision for scans)
DATABASE_URL=sqlite:///data/invoiceai.db
MAX_FILE_SIZE_MB=25
AI_MAX_TOKENS=2500      # lower = faster (a typical invoice JSON is 500-1500 tokens)
AI_NUM_CTX=8192         # context window sent to Ollama
OCR_ENGINE=auto         # auto | paddle | text | none
OCR_LANGUAGE=en
ENABLE_DUPLICATE_CHECK=true
ENABLE_VALIDATION=true
ENABLE_TAX_INTELLIGENCE=true
```

Settings changed in the UI are stored in `data/settings.json` and override `.env`.

---

## 4. Project Structure

```
invoiceai/
├── app.py                      # Streamlit entry point + startup checks
├── config.py                   # .env + runtime settings
├── requirements.txt
├── README.md, .env.example, .gitignore
├── Dockerfile, docker-compose.yml, start_invoiceai.bat
├── data/                       # SQLite DB (invoiceai.db), settings.json, tmp, previews
├── uploads/                    # original invoices (preserved, never modified)
├── exports/                    # generated Excel/CSV files
├── logs/                       # invoiceai.log + startup.log
├── models/                     # local OCR model storage
├── services/
│   ├── ocr_service.py          # PaddleOCR + PDF text layer + page conversion
│   ├── ai_service.py           # Ollama client, prompts, strict-JSON extraction
│   ├── invoice_parser.py       # normalize AI output + GST tax intelligence
│   ├── validation_service.py   # totals reconciliation, line-item checks
│   ├── duplicate_service.py    # fingerprint duplicate detection
│   ├── export_service.py       # styled Excel, CSV, Excel import
│   └── processor.py            # end-to-end processing pipeline + retry
├── database/
│   ├── models.py               # SQLAlchemy schema
│   ├── database.py             # engine / sessions
│   └── repository.py           # all data access + search + reports
├── ui/
│   ├── common.py  dashboard.py  upload.py  records.py
│   ├── invoice_details.py  vendors.py  reports.py  settings.py
└── utils/
    ├── file_utils.py  logging_utils.py  formatting.py
```

---

## 5. Database Schema (SQLite)

`data/invoiceai.db` — see `database/models.py`:

- **vendors** — name, legal_name, gstin (unique), pan, address, phone, email
- **invoices** — invoice number/date/due/PO/GRN/e-way bill, vendor + buyer snapshots,
  subtotal/discount/taxable/CGST/SGST/IGST/UTGST/CESS/round-off/grand total/paid/due,
  bank details, status, validation checks (JSON), confidence (JSON), duplicate status,
  fingerprint, source file link, AI model/mode/OCR engine, raw JSON
- **invoice_items** — line items with SKU/HSN codes, qty, rate, taxes per item, line total (original order preserved)
- **tax_details** — per-tax-component rows (extracted or calculated)
- **processing_logs** — file name, times, duration, OCR/AI/validation status, model, error, retry count
- **uploaded_files** — original file records with SHA-256 fingerprint

Relationship: `vendors 1—n invoices 1—n invoice_items` (+ tax_details).

---

## 6. Docker (optional)

```bat
docker compose up --build
```
Then open http://localhost:8501. Ollama runs on the host (default URL `http://host.docker.internal:11434`).
To run Ollama in a container too, uncomment the `ollama` service in `docker-compose.yml`.
Native Windows installation is equally supported — Docker is entirely optional.

---

## 7. Testing

Sample invoices are included in this folder (`GST-*.pdf`, `EAS-*.pdf`, `IG-*.pdf`):
- Upload several files at once → verify queue, progress, and summary (Processed/Successful/Failed)
- Check **Records** → search by invoice number / vendor / PO / SKU / GSTIN
- Open an invoice → review line items, tax summary, validation status, confidence
- Upload the same file twice → duplicate upload is skipped (`⏭`)
- Upload two invoices with the same `GSTIN + invoice number` → duplicate flagged; test Keep / Ignore / Delete
- Export **Excel** (5 sheets) and **CSV** → open and check formatting
- Restart the app → all records persist
- Test different document kinds: scanned PDF, JPG photo, multi-page PDF, low-quality scan,
  invoice with discounts/round-off, invoice without PO, credit/debit notes

---

## 8. Troubleshooting

| Problem | Fix |
|---|---|
| "Ollama is not running" | Start the Ollama app (system tray) and refresh. Check `Settings → System Checks`. |
| "Model X is not installed" | `ollama pull qwen3-vl:8b` (or pick another model in Settings). |
| PaddleOCR not installed | `pip install paddlepaddle paddleocr` (Python 3.10–3.12; auto-skipped on 3.13+). Until then the app uses the PDF text layer + AI vision automatically. |
| Scanned PDFs give no text | Install PaddleOCR **or** select a vision model (`qwen3-vl`, `gemma3`) — scans are read directly from page images by the vision model. |
| Slow on weak hardware | Use `qwen3:1.7b` (default) - on CPU-only machines it extracts a 1-page e-invoice in ~2 minutes instead of 15+. In `auto` mode the app uses **text mode automatically** when the PDF has a text layer (no image processing, 10-50x faster on CPU) and only falls back to vision for scans. Lower `AI_MAX_TOKENS` (e.g. 1500-2500) and keep `AI_NUM_CTX` at 8192. |
| Batch partially failed | Failed files stay in the queue — fix the issue and press **Retry Failed**. Failures from earlier sessions appear under **Upload → Failed Files — Retry** (powered by `processing_logs`) and can be retried even after a restart. |
| Where are exports stored? | `exports/` folder, or download via the buttons (Records / Settings). |
| Logs | `logs/invoiceai.log` + the **Processing Log** sheet inside Excel exports. |
| Port 8501 busy | Change in `.streamlit/config.toml` (`[server] port = 8502`) or pass `--server.port`. |

---

## 9. Roadmap-Fit Architecture

Modular `services/` layer is ready for: PO/GRN/3-way matching, vendor-master matching,
GST validation, price/quantity variance, purchase analytics, ERP/SAP integration,
email/WhatsApp import — add them without touching the UI core.