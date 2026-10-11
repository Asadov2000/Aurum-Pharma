"""Verify the newest backend job attempt in an additive CircleCI workspace."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


def newest_run(directory: Path) -> Path:
    """Never fall back to old successful reports when a newer attempt is incomplete."""
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Missing backend evidence directory")
    runs: dict[int, Path] = {}
    for run in directory.iterdir():
        match = re.fullmatch(r"run-([1-9][0-9]*)", run.name)
        if match is None or run.is_symlink() or not run.is_dir():
            raise ValueError("Unexpected backend run path")
        number = int(match.group(1))
        if number in runs:
            raise ValueError("Duplicate backend run number")
        runs[number] = run
        for shard in run.iterdir():
            if shard.name not in {"shard-0", "shard-1"} or shard.is_symlink() or not shard.is_dir():
                raise ValueError("Unexpected backend shard path")
            for report in shard.iterdir():
                if report.name not in {"pytest.xml", "shard.json"} or report.is_symlink() or not report.is_file():
                    raise ValueError("Unexpected backend report path")
    if not runs:
        raise ValueError("No backend job attempts were published")
    return runs[max(runs)]


def main() -> int:
    try:
        run = newest_run(Path("ci-evidence/backend"))
    except (OSError, ValueError) as exc:
        print(f"Backend evidence selection failed: {exc}", file=sys.stderr)
        return 1
    validator = Path(__file__).resolve().parents[2] / "scripts/verify-backend-shards.py"
    # Reuse the existing fail-closed coverage/JUnit validator and its exit code.
    return subprocess.run(
        [sys.executable, str(validator), str(run), "--expected-shards", "2"],
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
