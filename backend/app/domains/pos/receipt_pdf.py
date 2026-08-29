"""Server-side receipt PDF: render with fpdf2 (pure-Python, no system libs
beyond a Unicode TTF) and cache in MinIO keyed by sale_id.

Format choice: **A4** — this PDF is the archival / download / (later) e-mail
copy, so a standard page is the right fit. The live thermal printing (58/80 mm
ribbon) is done in the browser via window.print(); see the frontend
ReceiptPrintModal. Both consume the same resolved ReceiptData, so totals match.

Cyrillic needs a Unicode font: DejaVuSans, provided by the `fonts-dejavu-core`
apt package installed in backend/Dockerfile.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Protocol

from fpdf import FPDF
from fpdf.enums import Align, XPos, YPos
from minio.error import S3Error

from app.core.storage import ensure_bucket, get_object, put_object
from app.domains.pos.schemas import ReceiptData

_FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
_FONT_REGULAR = _FONT_DIR / "DejaVuSans.ttf"
_FONT_BOLD = _FONT_DIR / "DejaVuSans-Bold.ttf"
_FONT = "DejaVu"


class _LineWriter(Protocol):
    def __call__(
        self,
        txt: str,
        *,
        h: float = 5,
        size: int = 9,
        bold: bool = False,
        align: str = "L",
    ) -> None: ...


def _money(value: Decimal) -> str:
    return f"{value:.2f}"


def _write_receipt_summary(data: ReceiptData, line: _LineWriter) -> None:
    cur = data.currency
    if data.discount_total > 0:
        line(f"Скидка: {_money(data.discount_total)} {cur}", align="R")
    total_label = "ВОЗВРАЩЕНО" if data.is_refund else "ИТОГО"
    line(f"{total_label}: {_money(data.total)} {cur}", h=7, size=13, bold=True, align="R")

    for payment in data.payments:
        line(f"{_method_label(payment.method)}: {_money(payment.amount)} {cur}", align="R")
    if not data.is_refund:
        line(f"Принято: {_money(data.paid_total)} {cur}", align="R")
        line(f"Сдача: {_money(data.change)} {cur}", align="R")

    line("", h=4)
    line(
        "Средства возвращены по исходному чеку." if data.is_refund else "Спасибо за покупку!",
        size=8,
        align="C",
    )


def render_receipt_pdf(data: ReceiptData) -> bytes:
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(15, 15, 15)
    pdf.add_font(_FONT, "", str(_FONT_REGULAR))
    pdf.add_font(_FONT, "B", str(_FONT_BOLD))
    pdf.add_page()
    w = pdf.epw  # effective (content) width

    def line(
        txt: str, *, h: float = 5, size: int = 9, bold: bool = False, align: str = "L"
    ) -> None:
        pdf.set_font(_FONT, "B" if bold else "", size)
        pdf.cell(0, h, txt, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align=Align[align])

    # ---- header ----
    line(data.pharmacy_name or "—", h=8, size=16, bold=True, align="C")
    if data.branch_name:
        line(data.branch_name, align="C")
    if data.branch_address:
        line(data.branch_address, size=8, align="C")
    if data.branch_license:
        line(f"Лицензия: {data.branch_license}", size=8, align="C")
    pdf.ln(2)

    line("ВОЗВРАТ" if data.is_refund else "КАССОВЫЙ ЧЕК", h=6, size=12, bold=True, align="C")
    line(f"Чек № {data.receipt_number or '—'}")
    if data.is_refund:
        line(f"Исходный чек № {data.original_receipt_number or '—'}")
    if data.datetime is not None:
        line(f"Дата: {data.datetime:%d.%m.%Y %H:%M}")
    if data.cashier_name:
        line(f"Кассир: {data.cashier_name}")
    pdf.ln(2)

    # ---- items table ----
    col_name = w * 0.46
    col_qty = w * 0.14
    col_price = w * 0.20
    col_sum = w * 0.20

    def row(
        name: str, qty: str, price: str, total: str, *, bold: bool = False, h: float = 6
    ) -> None:
        pdf.set_font(_FONT, "B" if bold else "", 9)
        pdf.cell(col_name, h, name, border="B")
        pdf.cell(col_qty, h, qty, border="B", align=Align.R)
        pdf.cell(col_price, h, price, border="B", align=Align.R)
        pdf.cell(col_sum, h, total, border="B", align=Align.R, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    row("Наименование", "Кол-во", "Цена", "Сумма", bold=True)
    for it in data.items:
        # Trim long names to keep the row on one line; the browser view shows
        # the full wrapped name — the PDF is the compact archival copy.
        name = it.name if len(it.name) <= 38 else it.name[:37] + "…"
        row(name, _money(it.qty), _money(it.unit_price), _money(it.total_price))
    pdf.ln(2)

    # ---- totals ----
    _write_receipt_summary(data, line)

    out = pdf.output()
    return bytes(out)


def _method_label(method: str) -> str:
    return {
        "cash": "Наличные",
        "card": "Карта",
        "qr": "QR",
        "bank_transfer": "Перевод",
    }.get(method, method)


def get_or_render_receipt_pdf(data: ReceiptData) -> bytes:
    """Return the cached PDF for a completed sale, rendering and storing it on
    first request. Drafts are rendered fresh and never cached. Blocking — call
    from a worker thread."""
    cacheable = data.status == "completed"
    key = f"receipts/{data.sale_id}.pdf"

    if cacheable:
        try:
            bucket = ensure_bucket()
            return get_object(f"{bucket}/{key}")
        except S3Error:
            pass  # not generated yet — fall through and create it

    pdf = render_receipt_pdf(data)
    if cacheable:
        put_object(object_name=key, data=pdf, content_type="application/pdf")
    return pdf
