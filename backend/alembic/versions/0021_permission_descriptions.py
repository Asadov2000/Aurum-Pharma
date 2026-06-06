"""Fill permission.description with short, plain-Russian tooltips.

Every permission already has a `name` (short label); `description` was NULL.
This sets a one-line hint per code — "what this lets you do" — for the role
builder's tooltips. Wording is for a pharmacy owner, no jargon. Purely a text
backfill; nothing structural changes. downgrade() sets the same rows back to
NULL.

Covers all 45 permissions (the 43 base + roles.create / roles.update added in
migration 0018).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text as sa_text

revision: str = "0021"
down_revision: str | Sequence[str] | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (code, description) — one short line each.
_DESCRIPTIONS: list[tuple[str, str]] = [
    # users
    ("users.view", "Просмотр списка сотрудников аптеки."),
    ("users.invite", "Приглашение нового сотрудника по email."),
    ("users.update", "Изменение профилей сотрудников: имя, телефон."),
    ("users.block", "Блокировка сотрудника: запрет входа в систему."),
    ("users.delete", "Удаление сотрудника из аптеки."),
    # roles
    ("roles.assign", "Назначение ролей сотрудникам и привязка к точкам."),
    ("roles.create", "Создание новых ролей для сотрудников."),
    ("roles.update", "Изменение ролей и их набора прав."),
    # branches
    ("branches.view", "Просмотр списка точек (филиалов) аптеки."),
    ("branches.create", "Создание новой точки (филиала)."),
    ("branches.update", "Изменение точки: адрес, лицензия, реквизиты."),
    ("branches.delete", "Удаление точки (филиала)."),
    # registers
    ("registers.view", "Просмотр списка касс."),
    ("registers.create", "Создание новой кассы на точке."),
    ("registers.update", "Изменение настроек кассы."),
    ("registers.delete", "Удаление кассы."),
    # catalog
    ("catalog.view", "Просмотр каталога товаров."),
    ("catalog.create", "Добавление нового товара в каталог."),
    ("catalog.update", "Изменение карточки товара: название, цена, штрихкоды."),
    ("catalog.delete", "Удаление товара из каталога."),
    # batches
    ("batches.view", "Просмотр партий на складе: остатки, сроки годности, цены закупки."),
    ("batches.create", "Добавление новой партии товара на склад."),
    ("batches.update", "Изменение данных партии: срок годности, количество, цена."),
    ("batches.write_off", "Списание партии со склада: порча, брак, истёкший срок."),
    # suppliers
    ("suppliers.view", "Просмотр списка поставщиков."),
    ("suppliers.create", "Добавление нового поставщика."),
    ("suppliers.update", "Изменение данных поставщика."),
    # incoming
    ("incoming.view", "Просмотр документов прихода от поставщиков."),
    ("incoming.create", "Оформление прихода товара от поставщика."),
    ("incoming.return", "Оформление возврата товара поставщику."),
    # pos
    ("pos.shift_open", "Открытие кассовой смены."),
    ("pos.shift_close", "Закрытие кассовой смены с подсчётом наличных."),
    ("pos.sell", "Продажа товаров на кассе и оформление чеков."),
    ("pos.refund", "Оформление возврата товара покупателю."),
    ("pos.handle_prescription", "Отпуск рецептурных товаров на кассе."),
    # sales
    ("sales.view.own", "Просмотр только своих чеков."),
    ("sales.view.tenant", "Просмотр всех чеков аптеки, любого кассира."),
    # reports
    ("reports.view", "Просмотр отчётов и сводки по аптеке: выручка, остатки, финансы."),
    ("reports.export", "Выгрузка отчётов в файлы (Excel, PDF)."),
    # audit
    ("audit.view.own", "Просмотр журнала своих собственных действий."),
    ("audit.view.tenant", "Просмотр журнала действий всех сотрудников аптеки."),
    ("audit.view.global", "Просмотр действий по всем аптекам платформы (служебное)."),
    # settings
    ("settings.update", "Изменение настроек аптеки."),
    # tenant
    ("tenant.view", "Просмотр общих данных и профиля аптеки."),
    ("tenant.export.full", "Полная выгрузка всех данных аптеки одним архивом."),
]


def upgrade() -> None:
    conn = op.get_bind()
    for code, description in _DESCRIPTIONS:
        conn.execute(
            sa_text("UPDATE permission SET description = :d WHERE code = :c"),
            {"d": description, "c": code},
        )


def downgrade() -> None:
    conn = op.get_bind()
    for code, _ in _DESCRIPTIONS:
        conn.execute(
            sa_text("UPDATE permission SET description = NULL WHERE code = :c"),
            {"c": code},
        )
