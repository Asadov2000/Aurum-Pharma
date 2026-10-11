"""Verify CI image reuse guards with an inert Docker stand-in, without a drill."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IMAGE_ID = "sha256:" + "a" * 64


class BuildReuseTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = ROOT / ".codex"
        scratch.mkdir(exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="ci-build-reuse-", dir=scratch))
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.docker = self.bin / "docker"
        self.docker.write_text(
            '#!/usr/bin/env sh\n'
            'printf "%s\\n" "$*" >> docker.log\n'
            'test "$#" = 5 && test "$1" = image && test "$2" = inspect || exit 91\n'
            'test "$3" = --format && test "$4" = "{{.Id}}" || exit 92\n'
            'case "$5" in\n'
            '  aurum-backup:ci) printf "%s\\n" "$STUB_SOURCE_ID"; exit "${STUB_SOURCE_EXIT:-0}" ;;\n'
            '  aurum-pharma-backup:ci) printf "%s\\n" "$STUB_RECOVERY_ID"; exit "${STUB_RECOVERY_EXIT:-0}" ;;\n'
            '  *) exit 93 ;;\n'
            'esac\n',
            encoding="utf-8",
        )
        self.docker.chmod(0o755)
        self.mktemp = self.bin / "mktemp"
        self.mktemp.write_text(
            '#!/usr/bin/env sh\nprintf "unexpected resource creation\\n" > mktemp.log\nexit 97\n',
            encoding="utf-8",
        )
        self.mktemp.chmod(0o755)
        shell = shutil.which("sh")
        if os.name == "nt":
            shell = str(Path(os.environ["ProgramFiles"]) / "Git/bin/sh.exe")
        self.assertIsNotNone(shell, "A POSIX shell is required")
        self.shell = str(shell)

    def tearDown(self) -> None:
        for name in ("docker.log", "compose.log", "mktemp.log"):
            (self.root / name).unlink(missing_ok=True)
        self.docker.unlink()
        self.mktemp.unlink()
        self.bin.rmdir()
        self.root.rmdir()

    def invoke(
        self, command: str = "aurum_recovery_prepare_backup", *, full_drill: bool = False,
        **overrides: str | None,
    ) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ)
        environment.pop("BASH_ENV", None)
        environment.pop("ENV", None)
        environment.update(
            CI="true", CIRCLECI="true", AURUM_RECOVERY_USE_PREBUILT="true",
            STUB_SOURCE_ID=IMAGE_ID, STUB_RECOVERY_ID=IMAGE_ID,
        )
        environment["PATH"] = str(self.bin) + os.pathsep + environment["PATH"]
        for key, value in overrides.items():
            if value is None:
                environment.pop(key, None)
            else:
                environment[key] = value
        script = (
            '. "$1"; '
            'compose() { printf "%s\\n" "$*" >> compose.log; return "${STUB_COMPOSE_EXIT:-0}"; }; '
            + command
        )
        arguments = (
            [self.shell, (ROOT / "scripts/test-recovery-tooling.sh").as_posix()]
            if full_drill else
            [self.shell, "-c", script, "reuse-test", (ROOT / "scripts/recovery-build-mode.sh").as_posix()]
        )
        return subprocess.run(
            arguments,
            cwd=self.root, env=environment, capture_output=True, text=True, timeout=30, check=False,
        )

    def test_matching_images_allow_reuse_without_build(self) -> None:
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "docker.log").read_text().splitlines(), [
            "image inspect --format {{.Id}} aurum-backup:ci",
            "image inspect --format {{.Id}} aurum-pharma-backup:ci",
        ])
        self.assertFalse((self.root / "compose.log").exists())

    def test_default_and_explicit_false_keep_local_build(self) -> None:
        for flag in (None, "false"):
            with self.subTest(flag=flag):
                result = self.invoke(AURUM_RECOVERY_USE_PREBUILT=flag, CI=None, CIRCLECI=None)
                self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / "docker.log").exists())
        self.assertEqual((self.root / "compose.log").read_text().splitlines(), [
            "--profile backup build backup", "--profile backup build backup",
        ])
        result = self.invoke(AURUM_RECOVERY_USE_PREBUILT="false", STUB_COMPOSE_EXIT="37")
        self.assertEqual(result.returncode, 37)

    def test_invalid_flag_or_ci_environment_fails_before_docker(self) -> None:
        cases = [
            {"AURUM_RECOVERY_USE_PREBUILT": ""}, {"AURUM_RECOVERY_USE_PREBUILT": "yes"},
            {"AURUM_RECOVERY_USE_PREBUILT": "TRUE"}, {"CI": None}, {"CI": "false"},
            {"CIRCLECI": None}, {"CIRCLECI": "false"},
        ]
        for environment in cases:
            with self.subTest(environment=environment):
                result = self.invoke(**environment)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / "docker.log").exists())
                self.assertFalse((self.root / "compose.log").exists())

    def test_missing_invalid_or_mismatched_image_ids_refuse_reuse(self) -> None:
        cases = [
            {"STUB_SOURCE_EXIT": "42"}, {"STUB_RECOVERY_EXIT": "42"},
            {"STUB_SOURCE_ID": ""}, {"STUB_SOURCE_ID": "sha256:"},
            {"STUB_SOURCE_ID": "sha256:" + "a" * 63},
            {"STUB_SOURCE_ID": "sha256:" + "A" * 64},
            {"STUB_SOURCE_ID": "sha256:" + "g" * 64},
            {"STUB_SOURCE_ID": "sha256:" + "a" * 64 + "\nunexpected"},
            {"STUB_RECOVERY_ID": ""}, {"STUB_RECOVERY_ID": "sha256:" + "b" * 64},
        ]
        for environment in cases:
            with self.subTest(environment=environment):
                result = self.invoke(**environment)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / "compose.log").exists())
                self.assertNotIn(IMAGE_ID, result.stderr)

    def test_prepare_rechecks_image_alias_after_initial_validation(self) -> None:
        result = self.invoke(
            'aurum_recovery_validate_build_mode || exit 90; '
            'export STUB_RECOVERY_ID="sha256:' + "b" * 64 + '"; '
            'aurum_recovery_prepare_backup'
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len((self.root / "docker.log").read_text().splitlines()), 4)
        self.assertFalse((self.root / "compose.log").exists())

    def test_recovery_guard_runs_before_resource_creation(self) -> None:
        recovery = (ROOT / "scripts/test-recovery-tooling.sh").read_text(encoding="utf-8")
        guard = recovery.index("aurum_recovery_validate_build_mode")
        self.assertLess(recovery.index("/recovery-build-mode.sh"), guard)
        self.assertLess(guard, recovery.index("mktemp"))
        self.assertIn("aurum_recovery_prepare_backup\ncompose up -d --wait postgres minio", recovery)
        self.assertNotIn("compose --profile backup build backup", recovery)
        wrapper = (ROOT / ".circleci/scripts/production-containers-12.sh").read_text(encoding="utf-8")
        self.assertLess(wrapper.index("export AURUM_RECOVERY_USE_PREBUILT=true"), wrapper.index("sh ./scripts/test-recovery-tooling.sh"))

    def test_full_recovery_entrypoint_refuses_invalid_reuse_before_mktemp(self) -> None:
        cases = [
            ({"CI": "false"}, "explicit CircleCI environment"),
            ({"CIRCLECI": None}, "explicit CircleCI environment"),
            ({"STUB_SOURCE_EXIT": "42"}, "Missing scanned CI backup image"),
            ({"STUB_RECOVERY_EXIT": "42"}, "Missing recovery CI backup image"),
            ({"STUB_RECOVERY_ID": "sha256:" + "b" * 64}, "image IDs do not match"),
        ]
        for environment, message in cases:
            with self.subTest(environment=environment):
                result = self.invoke(full_drill=True, **environment)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(message, result.stderr)
                self.assertFalse((self.root / "mktemp.log").exists())
                self.assertFalse((self.root / "compose.log").exists())

    def test_go_caches_are_shared_arch_scoped_and_builder_only(self) -> None:
        cache_ids = set()
        total_mounts = 0
        for name, count in (("frontend/Dockerfile.production", 2), ("infra/backup/Dockerfile", 4)):
            with self.subTest(dockerfile=name):
                source = (ROOT / name).read_text(encoding="utf-8")
                builder, runtime = source.split(" AS runtime", 1)
                self.assertNotIn("--mount=type=cache", runtime)
                mounts = re.findall(r"--mount=type=cache,([^\s]+)", builder)
                self.assertEqual(len(mounts), count)
                self.assertLess(builder.index("ARG TARGETARCH"), builder.index("--mount=type=cache"))
                for mount in mounts:
                    settings = dict(part.split("=", 1) for part in mount.split(","))
                    self.assertEqual(settings["sharing"], "locked")
                    self.assertIn("${TARGETARCH}", settings["id"])
                    self.assertIn(settings["target"], {"/go/pkg/mod", "/root/.cache/go-build"})
                    cache_ids.add(settings["id"])
                total_mounts += len(mounts)
        self.assertEqual(total_mounts, 6)
        self.assertEqual(len(cache_ids), 2)


if __name__ == "__main__":
    unittest.main()
