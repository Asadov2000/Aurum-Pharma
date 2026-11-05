"""Historical ordinary and physical stock XLSX; generated without a cache."""

from __future__ import annotations

from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.worksheet.worksheet import Worksheet

from app.domains.pos.schemas import StockOnDateData, StockRow
from app.domains.pos.xlsx_safe import xlsx_safe

_HEAD_FONT = Font(bold=True)
_TITLE_FONT = Font(bold=True, size=14)
_UNIT_NAMES = {
    "tablet": "табл.",
    "capsule": "капс.",
    "ampoule": "амп.",
    "sachet": "саше",
    "suppository": "супп.",
}


def _fmt(currency: str) -> str:
    return f'#,##0.00" {currency}"'


def _cost_cell(amount: Decimal | None, status: str) -> float | str:
    if status == "hidden":
        return "Скрыто"
    return float(amount) if amount is not None else "Нет данных"


def _headers(data: StockOnDateData, extended: bool) -> list[str]:
    result = ["Наименование", "МНН"]
    if data.show_branch_column:
        result.append("Филиал")
    result += ["Партия", "Срок годности", "Кол-во", "Закуп. цена", "Стоимость"]
    if extended:
        result += [
            "Учёт",
            "Единица",
            "Закрытых пачек",
            "Единиц во вскрытых пачках",
            "Базовых единиц",
            "Статус стоимости",
        ]
    return result


def _row_values(row: StockRow, show_branch: bool, extended: bool) -> list[object]:
    physical = row.physical_stock
    result: list[object] = [row.name or "—", row.inn or "—"]
    if show_branch:
        result.append(row.branch_name or "—")
    result += [
        row.batch_number or "—",
        row.expires_at.strftime("%d.%m.%Y") if row.expires_at is not None else "—",
        float(row.qty) if physical is None else "—",
        _cost_cell(row.purchase_price, row.cost_status),
        _cost_cell(row.value, row.cost_status),
    ]
    if extended:
        result += [
            "Физический" if physical is not None else "Обычные упаковки",
            _UNIT_NAMES[physical.packaging.unit] if physical is not None else "уп.",
            str(physical.closed_packs) if physical is not None else "—",
            str(physical.opened_units) if physical is not None else "—",
            str(physical.base_units) if physical is not None else "—",
            {"known": "Известна", "unknown": "Нет данных", "hidden": "Скрыто"}[row.cost_status],
        ]
    return result


def _write_rows(ws: Worksheet, data: StockOnDateData, headers: list[str], extended: bool) -> int:
    money_cols = {headers.index(name) + 1 for name in ("Закуп. цена", "Стоимость")}
    row_number = 4
    for row in data.rows:
        values = _row_values(row, data.show_branch_column, extended)
        for column, value in enumerate(values, start=1):
            cell = ws.cell(row=row_number, column=column, value=xlsx_safe(value))
            if column in money_cols and isinstance(value, float):
                cell.number_format = _fmt(data.currency)
        row_number += 1
    return row_number


def _write_totals(
    ws: Worksheet, data: StockOnDateData, headers: list[str], row_number: int
) -> None:
    quantity_column = headers.index("Кол-во") + 1
    value_column = headers.index("Стоимость") + 1
    ws.cell(row=row_number, column=1, value="ИТОГО").font = _HEAD_FONT
    ws.cell(row=row_number, column=quantity_column, value=float(data.total_qty)).font = _HEAD_FONT
    value: float | str
    if data.total_value is not None:
        value = float(data.total_value)
    elif data.cost_status == "hidden":
        value = "Скрыто"
    else:
        value = "Нет полной стоимости"
    cell = ws.cell(row=row_number, column=value_column, value=value)
    cell.font = _HEAD_FONT
    cell.number_format = _fmt(data.currency)
    if data.cost_status == "partial" and data.known_total_value is not None:
        row_number += 1
        ws.cell(row=row_number, column=1, value="ИЗВЕСТНАЯ СТОИМОСТЬ").font = _HEAD_FONT
        cell = ws.cell(row=row_number, column=value_column, value=float(data.known_total_value))
        cell.font = _HEAD_FONT
        cell.number_format = _fmt(data.currency)
    for quantity in data.physical_quantities:
        row_number += 1
        ws.cell(
            row=row_number,
            column=1,
            value=f"ФИЗИЧЕСКИЕ ОСТАТКИ: {_UNIT_NAMES[quantity.unit]}",
        ).font = _HEAD_FONT
        ws.cell(row=row_number, column=quantity_column, value=str(int(quantity.quantity)))


def render_stock_on_date_xlsx(data: StockOnDateData) -> bytes:
    wb = Workbook()
    ws: Worksheet = wb.active
    ws.title = "Остатки"
    title = f"ОСТАТКИ НА {data.on_date:%d.%m.%Y}"
    if data.branch_name:
        title += f" · {data.branch_name}"
    ws.cell(row=1, column=1, value=title).font = _TITLE_FONT
    extended = any(
        row.inventory_kind == "physical" or row.cost_status != "known" for row in data.rows
    )
    headers = _headers(data, extended)
    for column, name in enumerate(headers, start=1):
        ws.cell(row=3, column=column, value=name).font = _HEAD_FONT
        width = 30 if name in {"Наименование", "МНН"} else max(14, min(len(name) + 3, 30))
        ws.column_dimensions[ws.cell(row=3, column=column).column_letter].width = width
    row_number = _write_rows(ws, data, headers, extended)
    _write_totals(ws, data, headers, row_number)
    ws.freeze_panes = "A4"
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
