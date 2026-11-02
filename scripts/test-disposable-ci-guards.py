"""Prove CI database scripts reject unsafe contexts before reading credentials."""

import asyncio
import builtins
import os
import runpy
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock


class DisposableCiGuards(unittest.TestCase):
    def test_guards(self) -> None:
        root = Path(__file__).resolve().parents[1]
        shell = shutil.which("sh")
        if os.name == "nt":
            shell = str(Path(os.environ["ProgramFiles"]) / "Git/bin/sh.exe")
        self.assertIsNotNone(shell, "A POSIX shell is required")
        if os.name == "nt":
            # Initialize MSYS separately from the unchanged 5-second guard deadline.
            # This bounded empty command never opens database or credential files.
            subprocess.run(
                [str(shell), "-c", ":"],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
        cases = (
            ({}, "explicitly disposable"),
            ({"CI": "true"}, "explicitly disposable"),
            ({"CI": "true", "CIRCLECI": "true"}, "explicitly disposable"),
            (
                {"CIRCLECI": "true", "AURUM_DISPOSABLE_DB_CONFIRM": "destroy-this-database"},
                "explicitly disposable",
            ),
            (
                {
                    "CI": "true",
                    "CIRCLECI": "true",
                    "AURUM_DISPOSABLE_DB_CONFIRM": "destroy-this-database",
                },
                "disposable *_test",
            ),
            (
                {
                    "CI": "true",
                    "GITHUB_ACTIONS": "true",
                    "AURUM_DISPOSABLE_DB_CONFIRM": "destroy-this-database",
                },
                "disposable *_test",
            ),
        )
        for filename in ("test-edge-cash-dispatcher.sh", "test-edge-cash-identity.sh"):
            for overrides, message in cases:
                with self.subTest(script=filename, context=overrides):
                    environment = dict(os.environ)
                    for key in ("CI", "GITHUB_ACTIONS", "CIRCLECI", "AURUM_DISPOSABLE_DB_CONFIRM"):
                        environment.pop(key, None)
                    environment.update(
                        POSTGRES_DB="production",
                        POSTGRES_USER="unused",
                        POSTGRES_PASSWORD_FILE="must-not-be-read",
                        EDGE_IDENTITY_SCRIPT="must-not-be-executed",
                        ROLE_BOOTSTRAP_SCRIPT="must-not-be-executed",
                    )
                    environment.update(overrides)
                    result = subprocess.run(
                        [str(shell), str(root / "scripts" / filename)],
                        env=environment,
                        capture_output=True,
                        text=True,
                        timeout=5,
                        check=False,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(message, result.stderr)
                    self.assertNotIn("must-not-be-read", result.stderr)


class NativeReplayGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.proof = runpy.run_path(
            str(Path(__file__).resolve().parents[1] / "scripts/test-edge-retail-cash-replay.py")
        )

    def environment(self) -> dict[str, str]:
        return {
            "CI": "true",
            "CIRCLECI": "true",
            "AURUM_DISPOSABLE_DB_CONFIRM": "destroy-this-database",
            "POSTGRES_SERVICE_ID": "inert-postgres-test",
            "DATABASE_URL_APP": "postgresql+asyncpg://aurum_app:unused@localhost:5432/aurum_test",
            "DATABASE_URL_SUPPORT": "postgresql+asyncpg://aurum_support:unused@localhost:5432/aurum_test",
            "DATABASE_URL_MIGRATION": "postgresql+asyncpg://aurum_migrator:unused@localhost:5432/aurum_test",
        }

    def assert_early_refusal(self, environment: dict[str, str]) -> None:
        original_import = builtins.__import__

        def safe_import(name: str, *args: object, **kwargs: object) -> object:
            if name.split(".", 1)[0] in {"app", "tests", "sqlalchemy"}:
                raise AssertionError("Runtime dependency imported before rejecting unsafe context")
            return original_import(name, *args, **kwargs)

        with (
            mock.patch.dict(os.environ, environment, clear=True),
            mock.patch("subprocess.run") as docker,
            mock.patch("builtins.__import__", side_effect=safe_import),
        ):
            with self.assertRaises(RuntimeError):
                asyncio.run(self.proof["main"]())
            docker.assert_not_called()

    def test_missing_ci_authorization_is_refused_before_runtime_or_docker(self) -> None:
        for name in (
            "CI",
            "CIRCLECI",
            "AURUM_DISPOSABLE_DB_CONFIRM",
            "POSTGRES_SERVICE_ID",
        ):
            with self.subTest(missing=name):
                environment = self.environment()
                environment.pop(name)
                self.assert_early_refusal(environment)

    def test_unsafe_targets_are_refused_before_runtime_or_docker(self) -> None:
        changes = (
            (
                "DATABASE_URL_APP",
                "postgresql+asyncpg://aurum_app:unused@localhost:5432/production",
            ),
            (
                "DATABASE_URL_APP",
                "postgresql+asyncpg://aurum_app:unused@remote.example:5432/aurum_test",
            ),
            (
                "DATABASE_URL_SUPPORT",
                "postgresql+asyncpg://aurum_support:unused@localhost:5433/aurum_test",
            ),
            (
                "DATABASE_URL_MIGRATION",
                "postgresql+asyncpg://postgres:unused@localhost:5432/aurum_test",
            ),
            (
                "DATABASE_URL_MIGRATION",
                "postgresql+asyncpg://aurum_migrator:unused@localhost:5432/other_test",
            ),
            (
                "DATABASE_URL_MIGRATION",
                "postgresql+asyncpg://aurum_migrator:unused@localhost:0/aurum_test",
            ),
            (
                "DATABASE_URL_APP",
                "postgresql+asyncpg://aurum_app:unused@localhost:bad/aurum_test",
            ),
            (
                "DATABASE_URL_APP",
                "postgresql+asyncpg://aurum_app:unused@localhost/aurum_test?host=remote",
            ),
            ("POSTGRES_SERVICE_ID", "--unexpected-option"),
        )
        for name, value in changes:
            with self.subTest(field=name, value=value):
                environment = self.environment()
                environment[name] = value
                self.assert_early_refusal(environment)

    def test_explicit_valid_context_requires_no_runtime_dependencies(self) -> None:
        with (
            mock.patch.dict(os.environ, self.environment(), clear=True),
            mock.patch("subprocess.run") as docker,
        ):
            self.proof["require_ci_context"]()
            docker.assert_not_called()


class NativePhysicalExecutionGuards(NativeReplayGuards):
    @classmethod
    def setUpClass(cls) -> None:
        cls.proof = runpy.run_path(
            str(
                Path(__file__).resolve().parents[1] / "scripts/test-edge-physical-cash-execution.py"
            )
        )

    def test_database_diagnostic_excludes_sql_parameters_and_invalid_identifiers(self) -> None:
        class DriverError(Exception):
            message = "private_parameter"
            table_name = "sale"
            column_name = "private@example.test"
            constraint_name = "native_scope_guard"
            context = (
                'SQL statement "SELECT private_value"\n'
                "PL/pgSQL function complete_edge_physical_cash_v1(uuid,jsonb) line 14 at PERFORM"
            )

        diagnostic = self.proof["database_diagnostic"](DriverError("private_parameter"))
        self.assertEqual(
            diagnostic,
            {
                "db_table": "sale",
                "db_column": None,
                "db_constraint": "native_scope_guard",
                "db_function": "complete_edge_physical_cash_v1",
                "db_line": "14",
                "db_reason": None,
            },
        )


class NativePhysicalSmokeGuards(NativeReplayGuards):
    @classmethod
    def setUpClass(cls) -> None:
        cls.proof = runpy.run_path(
            str(Path(__file__).resolve().parents[1] / "scripts/test-edge-physical-cash-smoke.py")
        )

    def test_clone_target_changes_only_database(self) -> None:
        clone = "aurum_native_" + "a" * 32 + "_test"
        environment = self.environment()
        result = self.proof["clone_environment"](environment, clone)
        for name in ("DATABASE_URL_APP", "DATABASE_URL_SUPPORT", "DATABASE_URL_MIGRATION"):
            self.assertEqual(result[name], environment[name].removesuffix("aurum_test") + clone)
        self.assertEqual(environment, self.environment())
        for unsafe in ("aurum_test", "production", "aurum_native_short_test", "../aurum_test"):
            with self.subTest(target=unsafe), self.assertRaises(RuntimeError):
                self.proof["clone_environment"](environment, unsafe)

    def test_all_test_and_worker_database_urls_follow_the_clone(self) -> None:
        clone = "aurum_native_" + "b" * 32 + "_test"
        environment = self.environment()
        environment["TEST_DATABASE_URL_APP"] = environment["DATABASE_URL_APP"]
        environment["DATABASE_URL_WORKER"] = (
            "postgresql+asyncpg://aurum_worker:unused@localhost:5432/aurum_test"
        )
        result = self.proof["clone_environment"](environment, clone)
        for name in ("TEST_DATABASE_URL_APP", "DATABASE_URL_WORKER"):
            self.assertTrue(result[name].endswith("/" + clone))
        self.assertTrue(environment["TEST_DATABASE_URL_APP"].endswith("/aurum_test"))

    def test_external_or_different_test_database_is_refused(self) -> None:
        clone = "aurum_native_" + "c" * 32 + "_test"
        for url in (
            "postgresql+asyncpg://aurum_app:unused@localhost:5432/another_test",
            "postgresql+asyncpg://aurum_app:unused@external.test:5432/aurum_test",
        ):
            with self.subTest(url=url):
                environment = self.environment()
                environment["TEST_DATABASE_URL_APP"] = url
                with self.assertRaisesRegex(RuntimeError, "Every configured database"):
                    self.proof["clone_environment"](environment, clone)

    def test_failed_proof_drops_only_its_created_clone(self) -> None:
        statements: list[str] = []

        class Admin:
            def __init__(self, _container: str, _database: str) -> None:
                pass

            def run(self, sql: str) -> str:
                statements.append(sql)
                if "pg_stat_activity" in sql:
                    return "0"
                if "SELECT oid FROM pg_catalog.pg_database" in sql:
                    return "114455"
                if "pg_catalog.pg_roles" in sql:
                    return "[]"
                return ""

        with (
            mock.patch.dict(os.environ, self.environment(), clear=True),
            mock.patch.dict(
                self.proof["main"].__globals__,
                {
                    "_helper": {"Admin": Admin},
                    "fingerprint": mock.Mock(return_value="unchanged"),
                    "invoke_proof": mock.Mock(side_effect=RuntimeError("injected proof failure")),
                },
            ),
            self.assertRaisesRegex(RuntimeError, "injected proof failure"),
        ):
            asyncio.run(self.proof["main"]())
        created = [sql for sql in statements if sql.startswith("CREATE DATABASE")]
        dropped = [sql for sql in statements if sql.startswith("DROP DATABASE")]
        self.assertEqual(len(created), 1)
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0].split()[2], created[0].split()[2])
        self.assertNotEqual(dropped[0], 'DROP DATABASE "aurum_test"')


if __name__ == "__main__":
    unittest.main()
