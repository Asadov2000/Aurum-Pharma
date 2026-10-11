"""Exercise parallel CI shell boundaries using an inert Poetry stand-in."""

from __future__ import annotations

import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERIFY = runpy.run_path(str(ROOT / "scripts/verify-backend-shards.py"))["verify"]
SELECT = runpy.run_path(str(ROOT / ".circleci/scripts/verify-backend-evidence.py"))["newest_run"]


class BackendParallelTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = ROOT / ".codex"
        scratch.mkdir(exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="backend-parallel-", dir=scratch))
        self.backend = self.root / "backend"
        self.bin = self.root / "bin"
        self.backend.mkdir()
        self.bin.mkdir()
        self.args = self.root / "poetry-args.txt"
        self.poetry = self.bin / "poetry"
        self.poetry.write_text(
            '#!/usr/bin/env bash\n'
            'printf "%s\\n" "$@" > ../poetry-args.txt\n'
            'exit "${POETRY_EXIT_CODE:-0}"\n',
            encoding="utf-8",
        )
        self.poetry.chmod(0o755)
        shell = shutil.which("bash")
        if os.name == "nt":
            shell = str(Path(os.environ["ProgramFiles"]) / "Git/bin/bash.exe")
        self.assertIsNotNone(shell, "Bash is required for CI script boundary tests")
        self.shell = str(shell)
        self.collection = ["tests/test_a.py::test_a", "tests/test_b.py::test_b"]
        self.run_names = {"run-100", "run-101"}
        self.extra_files: list[Path] = []

    def tearDown(self) -> None:
        # Only exact files created by this fixture are removed; never recurse.
        for path in self.extra_files:
            path.unlink(missing_ok=True)
        directories = [self.backend / "test-results"]
        for run_name in self.run_names:
            directories.extend(
                self.root / "ci-evidence/backend" / run_name / f"shard-{index}"
                for index in (0, 1)
            )
        for directory in directories:
            for filename in ("pytest.xml", "shard.json"):
                (directory / filename).unlink(missing_ok=True)
            if directory.exists():
                directory.rmdir()
        self.args.unlink(missing_ok=True)
        self.poetry.unlink()
        for run_name in self.run_names:
            directory = self.root / "ci-evidence/backend" / run_name
            if directory.exists():
                directory.rmdir()
        for directory in (self.root / "ci-evidence/backend", self.root / "ci-evidence", self.bin, self.backend):
            if directory.exists():
                directory.rmdir()
        self.root.rmdir()

    def invoke(self, script: str, **overrides: str | None) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ)
        environment.pop("BASH_ENV", None)
        environment.update(
            CI="true", CIRCLECI="true", CIRCLE_NODE_TOTAL="2", CIRCLE_NODE_INDEX="0", CIRCLE_BUILD_NUM="100"
        )
        environment["PATH"] = str(self.bin) + os.pathsep + environment["PATH"]
        for key, value in overrides.items():
            if value is None:
                environment.pop(key, None)
            else:
                environment[key] = value
        return subprocess.run(
            [self.shell, (ROOT / ".circleci/scripts" / script).as_posix()],
            cwd=self.backend if script == "backend-15.sh" else self.root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def reports(self, index: int) -> None:
        directory = self.backend / "test-results"
        directory.mkdir(exist_ok=True)
        selected = [self.collection[index]]
        (directory / "shard.json").write_text(json.dumps({
            "schema_version": 1,
            "shard_index": index + 1,
            "shard_count": 2,
            "collected": self.collection,
            "selected": selected,
        }), encoding="utf-8")
        suite = ET.Element("testsuite", tests="1", failures="0", errors="0")
        classname, name = selected[0].split("::", 1)
        ET.SubElement(suite, "testcase", classname=classname, name=name)
        ET.ElementTree(suite).write(directory / "pytest.xml", encoding="utf-8")

    def test_valid_nodes_run_entire_collection_with_distinct_shard_indices(self) -> None:
        for index in (0, 1):
            with self.subTest(index=index):
                result = self.invoke("backend-15.sh", CIRCLE_NODE_INDEX=str(index))
                self.assertEqual(result.returncode, 0, result.stderr)
                arguments = self.args.read_text(encoding="utf-8").splitlines()
                self.assertEqual(arguments[:5], ["run", "pytest", "-p", "tests.sharding", "tests/"])
                self.assertIn(f"--shard-index={index + 1}", arguments)
                self.assertIn("--shard-count=2", arguments)
                self.assertIn("--shard-manifest=test-results/shard.json", arguments)
                self.assertIn("--junitxml=test-results/pytest.xml", arguments)
                self.assertNotIn("-k", arguments)
                self.assertNotIn("-m", arguments)

    def test_pytest_failure_remains_failure(self) -> None:
        result = self.invoke("backend-15.sh", POETRY_EXIT_CODE="37")
        self.assertEqual(result.returncode, 37, result.stderr)
        self.assertTrue(self.args.is_file())

    def test_invalid_ci_context_stops_before_pytest_or_publication(self) -> None:
        invalid = [
            {"CI": None}, {"CI": "false"}, {"CIRCLECI": None}, {"CIRCLECI": "false"},
            {"CIRCLE_NODE_TOTAL": None}, {"CIRCLE_NODE_TOTAL": "1"}, {"CIRCLE_NODE_TOTAL": "3"},
            {"CIRCLE_NODE_INDEX": None}, {"CIRCLE_NODE_INDEX": "-1"}, {"CIRCLE_NODE_INDEX": "2"},
            {"CIRCLE_NODE_INDEX": "00"}, {"CIRCLE_NODE_INDEX": "../outside"},
            {"CIRCLE_NODE_INDEX": "$(touch injected)"},
        ]
        for script in ("backend-15.sh", "backend-evidence.sh"):
            if script == "backend-evidence.sh":
                self.reports(0)
            for environment in invalid:
                with self.subTest(script=script, environment=environment):
                    result = self.invoke(script, **environment)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(self.args.exists())
                    if script == "backend-15.sh":
                        self.assertFalse((self.backend / "test-results").exists())
                    self.assertFalse((self.root / "ci-evidence").exists())

    def test_missing_reports_stop_before_publication(self) -> None:
        for missing in ("pytest.xml", "shard.json"):
            with self.subTest(missing=missing):
                self.reports(0)
                (self.backend / "test-results" / missing).unlink()
                result = self.invoke("backend-evidence.sh")
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / "ci-evidence").exists())

    def test_invalid_build_numbers_stop_before_publication(self) -> None:
        self.reports(0)
        for number in (None, "0", "01", "-1", "../outside", "$(touch injected)"):
            with self.subTest(number=number):
                result = self.invoke("backend-evidence.sh", CIRCLE_BUILD_NUM=number)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / "ci-evidence").exists())

    def test_two_nodes_publish_unique_files_and_aggregate_requires_both(self) -> None:
        for index in (0, 1):
            self.reports(index)
            result = self.invoke("backend-evidence.sh", CIRCLE_NODE_INDEX=str(index))
            self.assertEqual(result.returncode, 0, result.stderr)
            destination = self.root / "ci-evidence/backend/run-100" / f"shard-{index}"
            self.assertTrue((destination / "shard.json").is_file())
            self.assertTrue((destination / "pytest.xml").is_file())
            if index == 0:
                with self.assertRaises(ValueError):
                    VERIFY(SELECT(self.root / "ci-evidence/backend"), 2)
        self.assertEqual(VERIFY(SELECT(self.root / "ci-evidence/backend"), 2), (2, 2))

    def publish(self, number: str, index: int) -> None:
        self.reports(index)
        result = self.invoke("backend-evidence.sh", CIRCLE_BUILD_NUM=number, CIRCLE_NODE_INDEX=str(index))
        self.assertEqual(result.returncode, 0, result.stderr)

    def gate(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(ROOT / ".circleci/scripts/verify-backend-evidence.py")],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def test_new_partial_run_never_falls_back_to_old_complete_run(self) -> None:
        for index in (0, 1):
            self.publish("100", index)
        self.publish("101", 0)
        result = self.gate()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Missing or unexpected shard manifests", result.stderr)

    def test_complete_rerun_supersedes_stale_invalid_reports(self) -> None:
        self.publish("100", 0)
        (self.root / "ci-evidence/backend/run-100/shard-0/shard.json").write_text("invalid", encoding="utf-8")
        for index in (0, 1):
            self.publish("101", index)
        result = self.gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2 shards, 2 tests", result.stdout)

    def test_invalid_run_aliases_or_extra_files_fail_closed(self) -> None:
        for index in (0, 1):
            self.publish("100", index)
        for name in ("run-0100", "run-0", "run-invalid", "unexpected"):
            with self.subTest(name=name):
                directory = self.root / "ci-evidence/backend" / name
                self.run_names.add(name)
                directory.mkdir()
                with self.assertRaises(ValueError):
                    SELECT(self.root / "ci-evidence/backend")
                directory.rmdir()
        for relative in ("extra.txt", "run-100/extra.txt", "run-100/shard-0/extra.txt"):
            with self.subTest(relative=relative):
                path = self.root / "ci-evidence/backend" / relative
                self.extra_files.append(path)
                path.write_text("unexpected", encoding="utf-8")
                with self.assertRaises(ValueError):
                    SELECT(self.root / "ci-evidence/backend")
                path.unlink()


if __name__ == "__main__":
    unittest.main()
