"""SQLAlchemy ORM models for InvoiceAI (SQLite default)."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Vendor(Base):
    __tablename__ = "vendors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str | None] = mapped_column(String(255), index=True)
    legal_name: Mapped[str | None] = mapped_column(String(255))
    gstin: Mapped[str | None] = mapped_column(String(32), index=True)
    pan: Mapped[str | None] = mapped_column(String(32))
    address: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(String(64))
    email: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[str] = mapped_column(String(32), default=lambda: dt.datetime.now().isoformat(timespec="seconds"))

    __table_args__ = (UniqueConstraint("gstin", name="uq_vendor_gstin"),)

    invoices: Mapped[list["Invoice"]] = relationship(back_populates="vendor")


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id"), index=True)

    # vendor snapshot
    vendor_name: Mapped[str | None] = mapped_column(String(255), index=True)
    vendor_gstin: Mapped[str | None] = mapped_column(String(32), index=True)
    vendor_pan: Mapped[str | None] = mapped_column(String(32))
    vendor_address: Mapped[str | None] = mapped_column(Text)
    vendor_phone: Mapped[str | None] = mapped_column(String(64))
    vendor_email: Mapped[str | None] = mapped_column(String(128))

    # invoice identity
    invoice_number: Mapped[str | None] = mapped_column(String(64), index=True)
    invoice_date: Mapped[str | None] = mapped_column(String(16), index=True)
    due_date: Mapped[str | None] = mapped_column(String(16))
    po_number: Mapped[str | None] = mapped_column(String(64), index=True)
    po_date: Mapped[str | None] = mapped_column(String(16))
    grn_number: Mapped[str | None] = mapped_column(String(64))
    delivery_note: Mapped[str | None] = mapped_column(String(64))
    eway_bill: Mapped[str | None] = mapped_column(String(64))
    currency: Mapped[str | None] = mapped_column(String(8))
    payment_terms: Mapped[str | None] = mapped_column(String(160))
    place_of_supply: Mapped[str | None] = mapped_column(String(80))

    # buyer
    buyer_name: Mapped[str | None] = mapped_column(String(255))
    buyer_address: Mapped[str | None] = mapped_column(Text)
    buyer_gstin: Mapped[str | None] = mapped_column(String(32), index=True)
    shipping_address: Mapped[str | None] = mapped_column(Text)
    billing_address: Mapped[str | None] = mapped_column(Text)

    # financials
    subtotal: Mapped[float | None] = mapped_column(Float)
    discount: Mapped[float | None] = mapped_column(Float)
    taxable_value: Mapped[float | None] = mapped_column(Float)
    cgst: Mapped[float | None] = mapped_column(Float)
    sgst: Mapped[float | None] = mapped_column(Float)
    igst: Mapped[float | None] = mapped_column(Float)
    utgst: Mapped[float | None] = mapped_column(Float)
    cess: Mapped[float | None] = mapped_column(Float)
    round_off: Mapped[float | None] = mapped_column(Float)
    grand_total: Mapped[float | None] = mapped_column(Float)
    amount_paid: Mapped[float | None] = mapped_column(Float)
    balance_due: Mapped[float | None] = mapped_column(Float)

    # payment
    bank_name: Mapped[str | None] = mapped_column(String(128))
    bank_account: Mapped[str | None] = mapped_column(String(64))
    ifsc: Mapped[str | None] = mapped_column(String(32))
    upi: Mapped[str | None] = mapped_column(String(128))

    # processing / status
    status: Mapped[str] = mapped_column(String(32), default="Extracted", index=True)  # Extracted|Validated|Needs Review|Failed|Imported
    validation_status: Mapped[str | None] = mapped_column(String(32))  # Matched|Mismatch|Needs Review
    validation_checks: Mapped[str | None] = mapped_column(Text)  # JSON
    confidence_json: Mapped[str | None] = mapped_column(Text)
    duplicate_status: Mapped[str] = mapped_column(String(32), default="None", index=True)  # None|Duplicate|Ignored
    duplicate_of_id: Mapped[int | None] = mapped_column(Integer, index=True)
    fingerprint: Mapped[str | None] = mapped_column(String(96), index=True)
    file_hash: Mapped[str | None] = mapped_column(String(64), index=True)

    # source document
    original_filename: Mapped[str | None] = mapped_column(String(255))
    file_path: Mapped[str | None] = mapped_column(String(512))
    page_count: Mapped[int | None] = mapped_column(Integer)
    preview_path: Mapped[str | None] = mapped_column(String(512))

    # engine metadata
    ai_model: Mapped[str | None] = mapped_column(String(64))
    ai_mode: Mapped[str | None] = mapped_column(String(16))
    ocr_engine: Mapped[str | None] = mapped_column(String(32))
    raw_json: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[str] = mapped_column(String(32), default=lambda: dt.datetime.now().isoformat(timespec="seconds"))
    processed_at: Mapped[str | None] = mapped_column(String(32))

    vendor: Mapped["Vendor | None"] = relationship(back_populates="invoices")
    items: Mapped[list["InvoiceItem"]] = relationship(back_populates="invoice", cascade="all, delete-orphan", order_by="InvoiceItem.line_no")
    tax_details: Mapped[list["TaxDetail"]] = relationship(back_populates="invoice", cascade="all, delete-orphan")


class InvoiceItem(Base):
    __tablename__ = "invoice_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)

    line_no: Mapped[int | None] = mapped_column(Integer)
    product_name: Mapped[str | None] = mapped_column(String(255), index=True)
    product_description: Mapped[str | None] = mapped_column(Text)
    sku: Mapped[str | None] = mapped_column(String(96), index=True)
    product_code: Mapped[str | None] = mapped_column(String(96))
    item_code: Mapped[str | None] = mapped_column(String(96))
    ean: Mapped[str | None] = mapped_column(String(32))
    upc: Mapped[str | None] = mapped_column(String(32))
    barcode: Mapped[str | None] = mapped_column(String(64))
    hsn: Mapped[str | None] = mapped_column(String(32), index=True)

    quantity: Mapped[float | None] = mapped_column(Float)
    free_quantity: Mapped[float | None] = mapped_column(Float)
    uom: Mapped[str | None] = mapped_column(String(16))
    unit_price: Mapped[float | None] = mapped_column(Float)
    mrp: Mapped[float | None] = mapped_column(Float)
    discount_pct: Mapped[float | None] = mapped_column(Float)
    discount_amount: Mapped[float | None] = mapped_column(Float)
    taxable_value: Mapped[float | None] = mapped_column(Float)

    gst_pct: Mapped[float | None] = mapped_column(Float)
    cgst_pct: Mapped[float | None] = mapped_column(Float)
    sgst_pct: Mapped[float | None] = mapped_column(Float)
    igst_pct: Mapped[float | None] = mapped_column(Float)
    cgst_amount: Mapped[float | None] = mapped_column(Float)
    sgst_amount: Mapped[float | None] = mapped_column(Float)
    igst_amount: Mapped[float | None] = mapped_column(Float)
    cess_amount: Mapped[float | None] = mapped_column(Float)

    line_total: Mapped[float | None] = mapped_column(Float)

    invoice: Mapped["Invoice"] = relationship(back_populates="items")


class TaxDetail(Base):
    __tablename__ = "tax_details"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    tax_type: Mapped[str] = mapped_column(String(16))  # CGST|SGST|IGST|UTGST|CESS
    taxable_value: Mapped[float | None] = mapped_column(Float)
    rate: Mapped[float | None] = mapped_column(Float)
    amount: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(16), default="extracted")  # extracted|calculated

    invoice: Mapped["Invoice"] = relationship(back_populates="tax_details")


class ProcessingLog(Base):
    __tablename__ = "processing_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_name: Mapped[str] = mapped_column(String(255), index=True)
    file_path: Mapped[str | None] = mapped_column(String(512))
    file_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    invoice_id: Mapped[int | None] = mapped_column(Integer, index=True)
    upload_date: Mapped[str] = mapped_column(String(32), default=lambda: dt.datetime.now().isoformat(timespec="seconds"))
    start_time: Mapped[str | None] = mapped_column(String(32))
    end_time: Mapped[str | None] = mapped_column(String(32))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    ocr_status: Mapped[str | None] = mapped_column(String(16))
    ai_status: Mapped[str | None] = mapped_column(String(16))
    validation_status: Mapped[str | None] = mapped_column(String(32))
    model_used: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="Pending", index=True)  # Pending|Processing|Success|Failed
    error_message: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)


class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    original_name: Mapped[str] = mapped_column(String(255))
    stored_name: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(String(512))
    size_bytes: Mapped[int] = mapped_column(Integer)
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    extension: Mapped[str | None] = mapped_column(String(8))
    upload_date: Mapped[str] = mapped_column(String(32), default=lambda: dt.datetime.now().isoformat(timespec="seconds"))
    invoice_id: Mapped[int | None] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(16), default="Pending")


def init_models():
    return Base.metadata


def touch_all(T):
    pass