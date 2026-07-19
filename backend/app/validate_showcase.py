"""CLI entry point for read-only showcase dataset validation."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import NoReturn

from sqlalchemy.exc import SQLAlchemyError

from app.showcase.validator import (
    ShowcaseValidationError,
    render_report,
    validate_database,
)


async def run_cli() -> int:
    """Run validation and return a process-compatible status code."""

    try:
        report = await validate_database()
    except ShowcaseValidationError:
        print(
            json.dumps(
                {"status": "error", "reason": "validation_context_error"},
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2
    except SQLAlchemyError:
        print(
            json.dumps(
                {"status": "error", "reason": "database_validation_error"},
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2

    print(render_report(report))
    return 0 if report.is_valid else 1


def main() -> NoReturn:
    raise SystemExit(asyncio.run(run_cli()))


if __name__ == "__main__":
    main()
