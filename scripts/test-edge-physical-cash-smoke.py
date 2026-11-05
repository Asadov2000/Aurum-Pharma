"""Early CI proof on a new disposable clone; the original database is read only.

No migration is rerun in the clone, and no application service is started.
The complete proof is also repeated after full pytest on its original CI target.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import runpy
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast
from urllib.parse import urlsplit
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import Mapping


class AdminAPI(Protocol):
    def run(self, sql: str) -> str: ...


_helper = runpy.run_path(str(Path(__file__).with_name("test-edge-retail-cash-replay.py")))
require_ci_context = cast(Callable[[], None], _helper["require_ci_context"])
literal = cast(Callable[[object], str], _helper["literal"])
identifier = cast(Callable[[str], str], _helper["identifier"])


def clone_environment(environment: Mapping[str, str], clone: str) -> dict[str, str]:
    if not re.fullmatch(r"aurum_native_[0-9a-f]{32}_test", clone):
        raise RuntimeError("Invalid disposable native clone identifier")
    result = dict(environment)
    source = urlsplit(environment["DATABASE_URL_APP"])
    for name, value in environment.items():
        if not name.startswith(("DATABASE_URL_", "TEST_DATABASE_URL_")):
            continue
        parsed = urlsplit(value)
        if parsed.path == "/" + clone:
            raise RuntimeError("Native clone cannot replace itself")
        if (
            parsed.scheme != source.scheme
            or parsed.hostname != source.hostname
            or (parsed.port or 5432) != (source.port or 5432)
            or parsed.path != source.path
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError("Every configured database must target the original CI instance")
        result[name] = parsed._replace(path="/" + clone).geturl()
    return result


def fingerprint(admin: AdminAPI) -> str:
    # The transport returns only hashes, never application rows or credentials.
    run = admin.run
    tables = json.loads(
        run(
            "SELECT jsonb_agg(tablename ORDER BY tablename) FROM pg_catalog.pg_tables "
            "WHERE schemaname='public'"
        )
    )
    if not tables or any(not re.fullmatch(r"[a-z][a-z0-9_]*", table) for table in tables):
        raise RuntimeError("Unexpected public table inventory")
    queries = [
        f"SELECT {literal(table)} AS name,md5(coalesce(jsonb_agg(to_jsonb(row) "
        f"ORDER BY to_jsonb(row)::TEXT)::TEXT,'[]')) AS digest "
        f"FROM public.{identifier(table)} row"
        for table in tables
    ]
    digest = run(
        "SELECT md5(jsonb_agg(jsonb_build_array(name,digest) ORDER BY name)::TEXT) "
        "FROM (" + " UNION ALL ".join(queries) + ") AS hashes"
    )
    sequences = run(
        "SELECT md5(coalesce(jsonb_agg(to_jsonb(sequence) ORDER BY sequencename)::TEXT,'[]')) "
        "FROM pg_catalog.pg_sequences sequence WHERE schemaname='public'"
    )
    revision = run("SELECT version_num FROM public.alembic_version ORDER BY version_num")
    return hashlib.sha256((digest + ":" + sequences + ":" + revision).encode()).hexdigest()


def new_native_roles(admin: AdminAPI, before: set[str]) -> list[str]:
    run = admin.run
    roles = json.loads(
        run(
            "SELECT coalesce(jsonb_agg(rolname),'[]') FROM pg_catalog.pg_roles "
            "WHERE rolname ~ '^aurum_edge_node_[0-9a-f]{32}$'"
        )
    )
    result = sorted(set(roles) - before)
    if any(not re.fullmatch(r"aurum_edge_node_[0-9a-f]{32}", role) for role in result):
        raise RuntimeError("Unexpected native role identifier")
    return result


def proof_script(kind: str) -> Path:
    if kind not in {"physical", "mixed"}:
        raise RuntimeError("Unsupported native smoke proof kind")
    return Path(__file__).with_name(f"test-edge-{kind}-cash-execution.py")


def invoke_proof(environment: Mapping[str, str], kind: str = "physical") -> None:
    script = proof_script(kind)
    result = subprocess.run(
        [sys.executable, str(script)],
        env=dict(environment),
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if result.returncode:
        # Keep diagnostic phase/class/SQLSTATE, but never echo arbitrary child
        # logs, traceback arguments, SQL parameters or environment values.
        for line in result.stdout.splitlines():
            try:
                failure = json.loads(line)
            except ValueError:
                continue
            if (
                isinstance(failure, dict)
                and set(failure)
                == {
                    f"native_{kind}_execution_proof",
                    "phase",
                    "error",
                    "site",
                    "sqlstate",
                    "db_table",
                    "db_column",
                    "db_constraint",
                    "db_function",
                    "db_line",
                    "db_reason",
                }
                and all(
                    value is None or re.fullmatch(r"[A-Za-z0-9_]+", str(value))
                    for value in failure.values()
                )
            ):
                print(json.dumps(failure))
        raise RuntimeError(f"Early native {kind} proof failed on its isolated clone")


async def main(kind: str = "physical") -> None:
    proof_script(kind)
    require_ci_context()
    container = os.environ["POSTGRES_SERVICE_ID"]
    source = urlsplit(os.environ["DATABASE_URL_APP"]).path.removeprefix("/")
    if source.startswith("aurum_native_"):
        raise RuntimeError("Nested native smoke clones are forbidden")
    admin_factory = cast(Callable[[str, str], AdminAPI], _helper["Admin"])
    source_admin = admin_factory(container, source)
    control = admin_factory(container, "postgres")
    before = fingerprint(source_admin)
    before_roles = set(new_native_roles(control, set()))
    clone = "aurum_native_" + uuid4().hex + "_test"
    environment = clone_environment(os.environ, clone)
    clone_oid: str | None = None
    try:
        if (
            control.run(
                "SELECT count(*) FROM pg_catalog.pg_stat_activity "
                f"WHERE datname={literal(source)}"
            )
            != "0"
        ):
            raise RuntimeError("Source CI database still has active connections")
        control.run(
            f"CREATE DATABASE {identifier(clone)} TEMPLATE {identifier(source)} OWNER postgres"
        )
        clone_oid = control.run(
            f"SELECT oid FROM pg_catalog.pg_database WHERE datname={literal(clone)}"
        )
        if not re.fullmatch(r"[1-9][0-9]*", clone_oid):
            raise RuntimeError("New disposable clone OID unavailable")
        control.run(
            f"REVOKE ALL ON DATABASE {identifier(clone)} FROM PUBLIC;"
            f"GRANT CONNECT ON DATABASE {identifier(clone)} TO "
            "aurum_app,aurum_support,aurum_migrator,aurum_schema_owner,aurum_edge_cash_executor;"
        )
        invoke_proof(environment, kind)
    finally:
        if clone_oid is not None:
            current_oid = control.run(
                f"SELECT oid FROM pg_catalog.pg_database WHERE datname={literal(clone)}"
            )
            if current_oid != clone_oid or not re.fullmatch(
                r"aurum_native_[0-9a-f]{32}_test", clone
            ):
                raise RuntimeError("Disposable clone identity changed; cleanup refused")
            control.run(f"DROP DATABASE {identifier(clone)}")
            for role in new_native_roles(control, before_roles):
                # No CASCADE or reassignment. Unexpected external dependencies
                # cause a visible failure instead of touching another database.
                control.run(f"DROP ROLE {identifier(role)}")
        if fingerprint(source_admin) != before:
            raise RuntimeError("Original CI database changed during isolated native smoke")
    print(f"Isolated native {kind} smoke passed; original CI data and revision unchanged.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:
        print(json.dumps({"native_physical_smoke": "failed", "error": type(error).__name__}))
        raise SystemExit(1) from None
