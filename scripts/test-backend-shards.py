"""Stdlib regression tests for complete, nonoverlapping backend shard evidence."""

from __future__ import annotations

import json
import runpy
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

validator = runpy.run_path(str(Path(__file__).with_name("verify-backend-shards.py")))
verify = validator["verify"]
collection_mismatch = validator["_collection_mismatch"]


class ShardEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = Path(__file__).resolve().parents[1] / ".codex"
        scratch.mkdir(exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="shard-validator-", dir=scratch))
        self.collection = ["tests/test_a.py::test_a", "tests/test_b.py::test_b"]
        self.files: list[Path] = []
        self.directories: list[Path] = []

    def tearDown(self) -> None:
        for path in reversed(self.files):
            path.unlink(missing_ok=True)
        for path in reversed(self.directories):
            path.rmdir()
        self.root.rmdir()

    def shard(self, index: int, *, count: int = 2, selected: list[str] | None = None) -> Path:
        folder = self.root / f"artifact-{len(self.directories)}"
        folder.mkdir()
        self.directories.append(folder)
        selected = selected if selected is not None else [self.collection[index - 1]]
        manifest = {
            "schema_version": 1,
            "shard_index": index,
            "shard_count": count,
            "collected": self.collection,
            "selected": selected,
        }
        path = folder / "shard.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        report = folder / "pytest.xml"
        suite = ET.Element("testsuite", tests=str(len(selected)), failures="0", errors="0")
        for nodeid in selected:
            classname, name = nodeid.split("::", 1)
            ET.SubElement(suite, "testcase", classname=classname, name=name)
        ET.ElementTree(suite).write(report, encoding="utf-8")
        self.files.extend((path, report))
        return path

    def change(self, path: Path, **updates: object) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update(updates)
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_complete_two_shards_and_explicit_skip(self) -> None:
        path = self.shard(1)
        self.shard(2)
        report = path.with_name("pytest.xml")
        tree = ET.parse(report)
        ET.SubElement(next(tree.getroot().iter("testcase")), "skipped")
        tree.write(report)
        self.assertEqual(verify(self.root, 2), (2, 2))

    def test_single_shard_baseline(self) -> None:
        self.shard(1, count=1, selected=self.collection)
        self.assertEqual(verify(self.root, 1), (1, 2))

    def test_missing_shard(self) -> None:
        self.shard(1)
        with self.assertRaises(ValueError):
            verify(self.root, 2)

    def test_invalid_manifests_fail_closed(self) -> None:
        path = self.shard(1)
        self.shard(2)
        original = path.read_text()
        mutations = [
            {"schema_version": True}, {"schema_version": 2}, {"shard_index": True},
            {"shard_index": 2}, {"shard_index": 0}, {"shard_count": "2"},
            {"shard_count": 1}, {"collected": list(reversed(self.collection))},
            {"collected": [self.collection[0]]}, {"collected": [None]},
            {"selected": []}, {"selected": "not-a-list"},
            {"selected": [self.collection[1]]}, {"selected": ["unknown"]},
            {"selected": [self.collection[0], self.collection[0]]},
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                path.write_text(original)
                self.change(path, **mutation)
                with self.assertRaises(ValueError):
                    verify(self.root, 2)
        for malformed in ("{", "[]", "null"):
            with self.subTest(malformed=malformed):
                path.write_text(malformed)
                with self.assertRaises(ValueError):
                    verify(self.root, 2)

    def test_omitted_test_is_rejected(self) -> None:
        self.collection.append("tests/test_c.py::test_c")
        self.shard(1)
        self.shard(2)
        with self.assertRaises(ValueError):
            verify(self.root, 2)

    def test_collection_mismatch_reports_file_counts_without_parameter_values(self) -> None:
        private_id = "private-customer-value"
        self.collection = [
            f"tests/test_a.py::test_internal_name[{private_id}-first]",
            "tests/test_b.py::test_b",
        ]
        self.shard(1)
        second = self.shard(2)
        self.change(second, collected=[
            f"tests/test_a.py::test_internal_name[{private_id}-second]",
            self.collection[1],
        ])
        with self.assertRaises(ValueError) as raised:
            verify(self.root, 2)
        message = str(raised.exception)
        self.assertIn("baseline-count=2, shard-count=2", message)
        self.assertIn("tests/test_a.py (baseline-only=1, shard-only=1)", message)
        self.assertNotIn("tests/test_b.py", message)
        self.assertNotIn(private_id, message)
        self.assertNotIn("test_internal_name", message)
        self.assertNotIn("::", message)

    def test_collection_mismatch_distinguishes_missing_and_added_file_counts(self) -> None:
        message = collection_mismatch(
            ["tests/isolation/test_a.py::test_one", "tests/isolation/test_a.py::test_two"],
            ["tests/test_b.py::test_three"],
        )
        self.assertIn("baseline-count=2, shard-count=1", message)
        self.assertIn("tests/isolation/test_a.py (baseline-only=2, shard-only=0)", message)
        self.assertIn("tests/test_b.py (baseline-only=0, shard-only=1)", message)

    def test_collection_mismatch_redacts_invalid_paths_and_bounds_file_output(self) -> None:
        invalid = [
            "tests/test_private[value].py::test_case",
            "tests/test_private\nvalue.py::test_case",
            "https://private-value.example/test.py::test_case",
            "tests/../private-value.py::test_case",
            "tests/test_" + "a" * 300 + ".py::test_case",
        ]
        message = collection_mismatch(invalid, [])
        self.assertIn("<invalid test path> (baseline-only=5, shard-only=0)", message)
        self.assertNotIn("private", message)
        self.assertNotIn("\n", message)
        bounded = collection_mismatch([f"tests/test_{index:02}.py::test_case" for index in range(23)], [])
        self.assertEqual(bounded.count("tests/test_"), 20)
        self.assertIn("3 additional file groups omitted", bounded)
        self.assertNotIn("tests/test_20.py", bounded)
        self.assertNotIn("test_case", bounded)

    def test_missing_and_invalid_junit_are_rejected(self) -> None:
        path = self.shard(1)
        self.shard(2)
        report = path.with_name("pytest.xml")
        for xml in (
            "<testsuite", "<unknown/>", '<testsuite tests="0"/>',
            '<testsuite tests="bad"><testcase name="test"/></testsuite>',
            '<testsuite tests="1"><testcase name="test"><failure/></testcase></testsuite>',
            '<testsuite tests="1"><testcase name="test"><error/></testcase></testsuite>',
            '<testsuite tests="1" failures="1"><testcase name="test"/></testsuite>',
            '<testsuite tests="1" errors="1"><testcase name="test"/></testsuite>',
        ):
            with self.subTest(xml=xml):
                report.write_text(xml)
                with self.assertRaises(ValueError):
                    verify(self.root, 2)
        report.unlink()
        with self.assertRaises(ValueError):
            verify(self.root, 2)

    def test_duplicate_junit_test_identity_is_rejected(self) -> None:
        path = self.shard(1, count=1, selected=self.collection)
        path.with_name("pytest.xml").write_text(
            '<testsuite tests="2"><testcase classname="same" name="test"/>'
            '<testcase classname="same" name="test"/></testsuite>'
        )
        with self.assertRaises(ValueError):
            verify(self.root, 1)


if __name__ == "__main__":
    unittest.main()
