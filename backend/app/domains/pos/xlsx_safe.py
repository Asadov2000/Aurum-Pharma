"""Spreadsheet formula-injection guard for XLSX exports.

A cell whose text starts with =, +, -, @ (or a tab/CR) can be executed as a
formula by Excel / LibreOffice when the file is opened — and our reports carry
user-controlled strings (drug names, INN, batch numbers) straight to an
accountant. Prefixing such values with a single quote neutralises them: the
spreadsheet shows the literal text and never evaluates it. openpyxl also treats
a leading '=' string as a real formula, which this prevents too.

Non-string values (numbers, dates) pass through untouched, so call sites can
wrap every cell value uniformly.
"""

from __future__ import annotations

from typing import TypeVar

_DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

T = TypeVar("T")


def xlsx_safe(value: T) -> T | str:
    if isinstance(value, str) and value[:1] in _DANGEROUS_PREFIXES:
        return "'" + value
    return value
