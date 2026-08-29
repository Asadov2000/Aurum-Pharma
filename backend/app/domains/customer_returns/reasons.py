"""Controlled reason codes shared by refund and quarantine workflows."""

from __future__ import annotations

from typing import Literal

type RefundReasonCode = Literal[
    "dispensing_error",
    "duplicate_sale",
    "pricing_error",
    "quality_issue",
    "damaged_package",
    "customer_cancelled",
    "other",
]

REFUND_REASON_CODES: frozenset[str] = frozenset(
    {
        "dispensing_error",
        "duplicate_sale",
        "pricing_error",
        "quality_issue",
        "damaged_package",
        "customer_cancelled",
        "other",
    }
)
