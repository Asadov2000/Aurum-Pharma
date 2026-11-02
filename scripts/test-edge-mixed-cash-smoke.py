"""Run the mixed proof with the same strict disposable clone boundary."""

from __future__ import annotations

import asyncio
import json
import runpy
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

_helper = runpy.run_path(str(Path(__file__).with_name("test-edge-physical-cash-smoke.py")))
main = cast(Callable[[str], Awaitable[None]], _helper["main"])

if __name__ == "__main__":
    try:
        asyncio.run(main("mixed"))
    except Exception as error:
        print(json.dumps({"native_mixed_smoke": "failed", "error": type(error).__name__}))
        raise SystemExit(1) from None
