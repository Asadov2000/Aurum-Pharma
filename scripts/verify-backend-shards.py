"""Verify that successful backend shard reports cover one complete pytest collection."""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def _nodeids(value: object) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise ValueError("Manifest node IDs must be nonempty lists of strings")
    if value != sorted(set(value)):
        raise ValueError("Manifest node IDs must be sorted and unique")
    return value


def _verify_junit(path: Path, expected_tests: int) -> None:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ValueError("Missing or invalid pytest.xml report") from exc
    if root.tag not in {"testsuite", "testsuites"}:
        raise ValueError("Unsupported JUnit report root")
    cases = list(root.iter("testcase"))
    suites = list(root.iter("testsuite"))
    if not suites or len(cases) != expected_tests:
        raise ValueError("JUnit test count does not match the selected collection")
    identities = [(case.get("classname"), case.get("name")) for case in cases]
    if any(not name for _classname, name in identities) or len(set(identities)) != len(cases):
        raise ValueError("JUnit contains missing or duplicate test identities")
    if list(root.iter("failure")) or list(root.iter("error")):
        raise ValueError("JUnit reports failing tests or errors")
    for suite in suites:
        try:
            declared = int(suite.attrib["tests"])
            failures = int(suite.get("failures", "0"))
            errors = int(suite.get("errors", "0"))
        except (KeyError, ValueError) as exc:
            raise ValueError("Invalid JUnit summary counters") from exc
        if declared != len(list(suite.iter("testcase"))) or failures != 0 or errors != 0:
            raise ValueError("JUnit summary reports missing tests, failures or errors")


def verify(directory: Path, expected_shards: int) -> tuple[int, int]:
    if type(expected_shards) is not int or expected_shards < 1:
        raise ValueError("Expected shard count must be a positive integer")
    manifests = sorted(directory.rglob("shard.json"))
    if len(manifests) != expected_shards:
        raise ValueError("Missing or unexpected shard manifests")
    indices: set[int] = set()
    all_collected: list[str] | None = None
    executed: set[str] = set()
    for path in manifests:
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("Missing or invalid shard manifest") from exc
        if not isinstance(manifest, dict):
            raise ValueError("Shard manifest must be an object")
        version = manifest.get("schema_version")
        index = manifest.get("shard_index")
        count = manifest.get("shard_count")
        if type(version) is not int or version != 1:
            raise ValueError("Unsupported shard manifest schema")
        if type(count) is not int or count != expected_shards:
            raise ValueError("Inconsistent shard count")
        if type(index) is not int or index not in range(1, expected_shards + 1) or index in indices:
            raise ValueError("Missing, duplicate or invalid shard index")
        indices.add(index)
        collected = _nodeids(manifest.get("collected"))
        selected = _nodeids(manifest.get("selected"))
        if all_collected is None:
            all_collected = collected
        elif collected != all_collected:
            raise ValueError("Shards did not collect the same complete test suite")
        if not set(selected).issubset(collected) or executed.intersection(selected):
            raise ValueError("Shard selections overlap or contain unknown tests")
        _verify_junit(path.with_name("pytest.xml"), len(selected))
        executed.update(selected)
    if executed != set(all_collected or []):
        raise ValueError("Shard selections omit collected tests")
    return len(indices), len(executed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--expected-shards", type=int, required=True)
    args = parser.parse_args()
    try:
        shards, tests = verify(args.directory, args.expected_shards)
    except ValueError as exc:
        print(f"Backend shard verification failed: {exc}", file=sys.stderr)
        return 1
    print(f"Backend shard coverage verified: {shards} shards, {tests} tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
