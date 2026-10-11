"""Guard cache boundaries and fail-fast ordering without installing CI tooling."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
CONFIG = (ROOT / ".circleci/config.yml").read_text(encoding="utf-8")


def sections(source: str) -> dict[str, str]:
    """Read the config's top-level command/job blocks, not arbitrary YAML."""
    return dict(re.findall(r"^  ([a-z][a-z0-9-]+):\n(.*?)(?=^  [a-z]|\Z)", source, re.M | re.S))


COMMANDS = sections(CONFIG.split("commands:\n", 1)[1].split("jobs:\n", 1)[0])
JOBS = sections(CONFIG.split("jobs:\n", 1)[1].split("workflows:\n", 1)[0])


class CircleCiCacheContract(unittest.TestCase):
    def test_only_package_downloads_are_cached(self) -> None:
        allowed = {
            "~/.cache/pip",
            "~/.cache/pypoetry/artifacts",
            "~/.cache/pypoetry/cache",
            ".circleci-tmp/pnpm-store",
        }
        paths = []
        for block in re.findall(r"- save_cache:\n(.*?)(?=^  [a-z]|\Z)", CONFIG, re.M | re.S):
            paths.extend(re.findall(r'^            - "([^"]+)"$', block, re.M))
        self.assertEqual(set(paths), allowed)
        self.assertEqual(len(paths), len(allowed))

    def test_exact_cache_keys_bind_locks_architecture_and_runtime(self) -> None:
        for ecosystem, lock in (("python", "backend/poetry.lock"), ("pnpm", "frontend/pnpm-lock.yaml")):
            with self.subTest(ecosystem=ecosystem):
                restore = COMMANDS[f"restore-{ecosystem}-downloads"]
                save = COMMANDS[f"save-{ecosystem}-downloads"]
                keys = re.findall(r"^            - '([^']+)'$", restore, re.M)
                self.assertEqual(len(keys), 1, "Partial fallback cache restores are not allowed")
                self.assertIn(f"key: '{keys[0]}'", save)
                self.assertIn('{{ arch }}', keys[0])
                self.assertIn('{{ checksum "' + lock + '" }}', keys[0])
                self.assertIn('{{ checksum ".circleci-tmp/' + ecosystem + '-runtime" }}', keys[0])
                self.assertLess(restore.index("download-cache-runtime.sh"), restore.index("restore_cache:"))
                self.assertNotIn(".Revision", keys[0])
                self.assertNotIn("epoch", keys[0])
        self.assertIn("poetry2.3.4", COMMANDS["restore-python-downloads"])

    def test_installs_and_audits_always_run(self) -> None:
        for script in ("frontend-03.sh", "dependency-audit-08.sh", "e2e-03.sh"):
            with self.subTest(script=script):
                content = (ROOT / ".circleci/scripts" / script).read_text(encoding="utf-8")
                self.assertIn("pnpm install --frozen-lockfile --store-dir ../.circleci-tmp/pnpm-store", content)
        for script in ("backend-05.sh", "dependency-audit-04.sh"):
            content = (ROOT / ".circleci/scripts" / script).read_text(encoding="utf-8")
            self.assertIn("poetry install --no-root --with dev --sync", content)
        self.assertIn("dependency-audit-05.sh", JOBS["dependency-audit"])
        self.assertIn("dependency-audit-09.sh", JOBS["dependency-audit"])
        self.assertNotIn("save-python-downloads", JOBS["dependency-audit"])
        self.assertNotIn("save-pnpm-downloads", JOBS["dependency-audit"])
        for name in ("backend", "frontend", "dependency-audit", "e2e"):
            self.assertNotIn("condition:", JOBS[name], "Cache hits must never skip validation")

    def test_static_checks_precede_database_provisioning(self) -> None:
        backend = JOBS["backend"]
        install = backend.index("backend-05.sh")
        services = backend.index("backend-services.sh")
        for script in ("backend-12.sh", "backend-13.sh", "backend-14.sh"):
            self.assertGreater(backend.index(script), install)
            self.assertLess(backend.index(script), services)
        self.assertGreater(backend.index("backend-15.sh"), services)

    def test_executor_cache_namespaces_stay_separate(self) -> None:
        frontend = JOBS["frontend"]
        for command in ("restore-pnpm-downloads", "save-pnpm-downloads"):
            self.assertIn(f'- {command}:\n          executor: "docker"', frontend)
        self.assertIn('default: "machine"', COMMANDS["restore-pnpm-downloads"])
        self.assertIn('default: "machine"', COMMANDS["save-pnpm-downloads"])
        self.assertIn('"save-python-downloads"', JOBS["backend"])
        self.assertIn('"save-pnpm-downloads"', JOBS["e2e"])

    def test_full_gate_and_resource_limits_remain_required(self) -> None:
        expected = {"backend", "frontend", "dependency-audit", "e2e", "production-containers", "tooling-linux", "tooling-windows"}
        workflow = CONFIG.split("workflows:\n", 1)[1]
        gate = workflow.split("- ci-gate:\n", 1)[1]
        self.assertEqual(set(re.findall(r'^            - "([^"]+)"$', gate, re.M)), expected)
        self.assertEqual(set(JOBS), expected | {"ci-gate"})
        self.assertNotIn("parallelism:", CONFIG)
        for name in expected - {"tooling-windows"}:
            self.assertIn('resource_class: "medium"', JOBS[name])
        self.assertIn('--pull --load --no-cache-filter runtime', JOBS["production-containers"])


if __name__ == "__main__":
    unittest.main()
