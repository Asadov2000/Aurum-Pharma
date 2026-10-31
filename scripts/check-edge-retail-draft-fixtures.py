"""Check redacted disposable-CI snapshots; expired values never authorize posting."""

from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path


def main() -> int:
    if (
        os.environ.get("CI") != "true"
        or not any(os.environ.get(key) == "true" for key in ("CIRCLECI", "GITHUB_ACTIONS"))
        or os.environ.get("AURUM_DISPOSABLE_DB_CONFIRM") != "destroy-this-database"
    ):
        raise RuntimeError("Native snapshot checks require disposable CI")
    os.environ["JWT_SECRET"] = secrets.token_urlsafe(48)
    from pydantic import ValidationError

    from app.core.errors import BusinessRuleError, ConflictError
    from app.domains.pos.edge_retail_cash_draft import EdgeRetailCashDraftV1
    from app.domains.pos.edge_retail_cash_repricing import check_edge_retail_cash_draft

    path = Path(sys.argv[1])
    if not 0 < path.stat().st_size <= 16 * 1_048_576:
        raise RuntimeError("Native snapshot evidence size is invalid")
    expected_cases = {
        f"{kind}:{scenario}"
        for kind in ("physical_cash", "mixed_cash")
        for scenario in (
            "baseline",
            "quantity",
            "discount",
            "price",
            "rx_missing",
            "rx_blank",
            "rx_global",
            "rx_item",
            "restored",
        )
    }
    expected_cases.add("physical_cash:cancelled")
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        name = row["case"]
        if name not in expected_cases or name in seen:
            raise RuntimeError("Native snapshot evidence has unexpected or duplicate cases")
        seen.add(name)
        expected = row["expect"]
        if expected not in {"ok", "conflict", "business"}:
            raise RuntimeError("Native snapshot expectation is invalid")
        try:
            draft = EdgeRetailCashDraftV1.model_validate(row["draft"])
            checked = check_edge_retail_cash_draft(draft)
        except (ConflictError, BusinessRuleError) as error:
            category = "conflict" if isinstance(error, ConflictError) else "business"
            if expected != category:
                print(
                    f"Native snapshot {name}: unexpected {type(error).__name__}",
                    file=sys.stderr,
                )
                return 1
        except ValidationError as error:
            locations = [list(item["loc"]) for item in error.errors(include_input=False)]
            print(
                f"Native snapshot {name}: invalid contract at {locations}",
                file=sys.stderr,
            )
            return 1
        else:
            if expected != "ok" or checked.quote != draft.inputs.saved_quote:
                print(f"Native snapshot {name}: unexpected acceptance", file=sys.stderr)
                return 1
    if seen != expected_cases:
        raise RuntimeError("Native snapshot evidence is incomplete")
    print(f"Native current-cart/Rx/repricing snapshot checks: {len(seen)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
