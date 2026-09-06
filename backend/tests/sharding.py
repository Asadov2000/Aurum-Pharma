"""Opt-in pytest file sharding for runners with independent test infrastructure."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest


@dataclass(frozen=True)
class _ShardConfig:
    index: int
    count: int
    manifest: Path


_SHARD_CONFIG = pytest.StashKey[_ShardConfig]()
_FILTER_OPTIONS = (
    ("keyword", "-k"),
    ("markexpr", "-m"),
    ("lf", "--lf"),
    ("deselect", "--deselect"),
    ("ignore", "--ignore"),
    ("ignore_glob", "--ignore-glob"),
    ("stepwise", "--stepwise"),
    ("stepwise_skip", "--stepwise-skip"),
)


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("test sharding")
    group.addoption("--shard-index", type=int, help="One-based shard index")
    group.addoption("--shard-count", type=int, help="Total independent test shards")
    group.addoption("--shard-manifest", help="Output JSON collection manifest")


def pytest_configure(config: pytest.Config) -> None:
    index = cast(int | None, config.getoption("shard_index"))
    count = cast(int | None, config.getoption("shard_count"))
    manifest = cast(str | None, config.getoption("shard_manifest"))
    if index is None and count is None and manifest is None:
        return
    if index is None or count is None or not manifest:
        raise pytest.UsageError(
            "Sharding requires --shard-index, --shard-count and --shard-manifest"
        )
    if count < 1 or not 1 <= index <= count:
        raise pytest.UsageError(
            "Sharding requires shard-count >= 1 and 1 <= shard-index <= shard-count"
        )
    for option, spelling in _FILTER_OPTIONS:
        if config.getoption(option, default=None):
            raise pytest.UsageError(
                f"Sharding cannot be combined with collection filter {spelling}"
            )
    config.stash[_SHARD_CONFIG] = _ShardConfig(index, count, Path(manifest))


def assign_shards(node_ids: Sequence[str], shard_count: int) -> list[frozenset[str]]:
    """Balance whole files by collected test count, with stable tie breaking."""
    if shard_count < 1:
        raise pytest.UsageError("shard-count must be positive")
    if len(node_ids) != len(set(node_ids)):
        raise pytest.UsageError("Duplicate collected node IDs prevent safe sharding")
    files: dict[str, list[str]] = defaultdict(list)
    for node_id in node_ids:
        files[node_id.split("::", 1)[0]].append(node_id)
    shards: list[set[str]] = [set() for _ in range(shard_count)]
    for file_name in sorted(files, key=lambda name: (-len(files[name]), name)):
        index = min(range(shard_count), key=lambda candidate: (len(shards[candidate]), candidate))
        shards[index].update(files[file_name])
    if any(not shard for shard in shards):
        raise pytest.UsageError(
            "Empty shard: shard-count exceeds the number of collected test files"
        )
    return [frozenset(shard) for shard in shards]


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    shard = config.stash.get(_SHARD_CONFIG, None)
    if shard is None:
        return
    collected = [item.nodeid for item in items]
    selected = assign_shards(collected, shard.count)[shard.index - 1]
    deselected = [item for item in items if item.nodeid not in selected]
    items[:] = [item for item in items if item.nodeid in selected]
    config.hook.pytest_deselected(items=deselected)
    manifest = {
        "schema_version": 1,
        "shard_index": shard.index,
        "shard_count": shard.count,
        "collected": sorted(collected),
        "selected": sorted(selected),
    }
    try:
        shard.manifest.parent.mkdir(parents=True, exist_ok=True)
        shard.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    except OSError:
        raise pytest.UsageError("Cannot write the shard manifest") from None
