"""Run the opt-in plugin against tiny suites without application fixtures."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests.sharding import assign_shards, pytest_configure


def _run_pytest(directory: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    environment.pop("PYTEST_ADDOPTS", None)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "tests.sharding",
            "--confcutdir",
            str(directory),
            "-c",
            str(directory / "pytest.ini"),
            "-q",
            *arguments,
        ],
        cwd=directory,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.fixture
def suite(tmp_path: Path) -> Path:
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    for file_name, tests in (("test_a.py", 3), ("test_b.py", 2), ("test_c.py", 1)):
        source = "from pathlib import Path\n"
        for index in range(tests):
            node_id = f"{file_name}::test_{index}"
            source += (
                f"\ndef test_{index}():\n"
                f"    with Path('executed.txt').open('a') as output:\n"
                f"        output.write({node_id!r} + '\\n')\n"
            )
        (tmp_path / file_name).write_text(source, encoding="utf-8")
    return tmp_path


def _shard_arguments(index: int, count: int) -> tuple[str, ...]:
    return (
        f"--shard-index={index}",
        f"--shard-count={count}",
        f"--shard-manifest=shard-{index}.json",
    )


def test_shards_are_balanced_deterministic_and_keep_files_together() -> None:
    nodes = [
        f"test_{file}.py::test_{index}"
        for file, size in (("a", 3), ("b", 2), ("c", 1))
        for index in range(size)
    ]
    shards = assign_shards(nodes, 2)

    assert shards == assign_shards(list(reversed(nodes)), 2)
    assert shards == [frozenset(nodes[:3]), frozenset(nodes[3:])]


def test_two_shards_execute_each_collected_test_exactly_once(suite: Path) -> None:
    (suite / "conftest.py").write_text(
        "def pytest_collection_modifyitems(items):\n    items.reverse()\n", encoding="utf-8"
    )
    selected: list[set[str]] = []
    collected: list[list[str]] = []
    for index in (1, 2):
        result = _run_pytest(suite, *_shard_arguments(index, 2))
        assert result.returncode == 0, result.stdout + result.stderr
        manifest = json.loads((suite / f"shard-{index}.json").read_text(encoding="utf-8"))
        assert manifest["schema_version"] == 1
        assert manifest["shard_index"] == index
        assert manifest["shard_count"] == 2
        assert manifest["selected"] == sorted(manifest["selected"])
        assert manifest["collected"] == sorted(manifest["collected"])
        selected.append(set(manifest["selected"]))
        collected.append(manifest["collected"])

    assert collected[0] == collected[1]
    assert not selected[0] & selected[1]
    assert selected[0] | selected[1] == set(collected[0])
    executed = (suite / "executed.txt").read_text().splitlines()
    # The preceding collection hook reverses execution order. Sharding must not
    # silently sort the selected tests back into manifest order.
    assert executed == [
        "test_a.py::test_2",
        "test_a.py::test_1",
        "test_a.py::test_0",
        "test_c.py::test_0",
        "test_b.py::test_1",
        "test_b.py::test_0",
    ]
    assert len(executed) == len(set(executed)) == 6


def test_equal_file_sizes_and_shard_loads_have_stable_tie_breaking() -> None:
    nodes = [
        f"test_{file}.py::test_{index}"
        for file, size in (("d", 1), ("b", 2), ("c", 1), ("a", 2))
        for index in range(size)
    ]

    assert assign_shards(nodes, 2) == [
        frozenset({"test_a.py::test_0", "test_a.py::test_1", "test_c.py::test_0"}),
        frozenset({"test_b.py::test_0", "test_b.py::test_1", "test_d.py::test_0"}),
    ]


def test_single_shard_executes_full_suite_and_writes_manifest(suite: Path) -> None:
    result = _run_pytest(suite, *_shard_arguments(1, 1))

    assert result.returncode == 0, result.stdout + result.stderr
    manifest = json.loads((suite / "shard-1.json").read_text(encoding="utf-8"))
    assert manifest["collected"] == manifest["selected"]
    assert (suite / "executed.txt").read_text().splitlines() == manifest["collected"]


def test_loaded_plugin_without_arguments_does_not_filter_suite(suite: Path) -> None:
    result = _run_pytest(suite)

    assert result.returncode == 0, result.stdout + result.stderr
    assert len((suite / "executed.txt").read_text().splitlines()) == 6
    assert not list(suite.glob("shard-*.json"))


def test_failed_selected_test_still_fails_pytest(suite: Path) -> None:
    (suite / "test_c.py").write_text("def test_failure():\n    assert False\n", encoding="utf-8")
    result = _run_pytest(suite, *_shard_arguments(2, 2))

    assert result.returncode == int(pytest.ExitCode.TESTS_FAILED), result.stdout + result.stderr
    manifest = json.loads((suite / "shard-2.json").read_text(encoding="utf-8"))
    assert "test_c.py::test_failure" in manifest["selected"]


@pytest.mark.parametrize(
    "options",
    [
        {"shard_index": None},
        {"shard_count": None},
        {"shard_manifest": None},
        {"shard_index": 0},
        {"shard_index": 3},
        {"shard_count": 0},
        {"keyword": "test_0"},
        {"markexpr": "slow"},
        {"lf": True},
        {"deselect": ["test_a.py::test_0"]},
        {"ignore": ["test_a.py"]},
        {"ignore_glob": ["*a.py"]},
        {"stepwise": True},
        {"stepwise_skip": True},
    ],
)
def test_invalid_configuration_is_rejected(options: dict[str, object]) -> None:
    values = {"shard_index": 1, "shard_count": 2, "shard_manifest": "shard.json", **options}
    config = Mock(spec=pytest.Config)
    config.getoption.side_effect = lambda name, default=None: values.get(name, default)

    with pytest.raises(pytest.UsageError):
        pytest_configure(config)


def test_empty_shard_is_rejected() -> None:
    with pytest.raises(pytest.UsageError, match="Empty shard"):
        assign_shards(["test_a.py::test_0"], 2)


@pytest.mark.parametrize(
    "arguments",
    [
        (*_shard_arguments(1, 2), "-k", "test_0"),
        (*_shard_arguments(1, 2), "test_a.py", "test_a.py"),
    ],
)
def test_invalid_sharding_does_not_execute_tests(suite: Path, arguments: tuple[str, ...]) -> None:
    result = _run_pytest(suite, *arguments)

    assert result.returncode == int(pytest.ExitCode.USAGE_ERROR), result.stdout + result.stderr
    assert not (suite / "executed.txt").exists()
    assert not list(suite.glob("shard-*.json"))
