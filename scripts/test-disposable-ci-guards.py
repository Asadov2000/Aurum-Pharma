"""Prove CI database scripts reject unsafe contexts before reading credentials."""

import os
from pathlib import Path
import shutil
import subprocess
import unittest


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
                [str(shell), "-c", ":"], capture_output=True, text=True, timeout=30,
                check=True,
            )
        cases = (
            ({}, "explicitly disposable"),
            ({"CI": "true"}, "explicitly disposable"),
            ({"CI": "true", "CIRCLECI": "true"}, "explicitly disposable"),
            ({"CIRCLECI": "true", "AURUM_DISPOSABLE_DB_CONFIRM": "destroy-this-database"}, "explicitly disposable"),
            ({"CI": "true", "CIRCLECI": "true", "AURUM_DISPOSABLE_DB_CONFIRM": "destroy-this-database"}, "disposable *_test"),
            ({"CI": "true", "GITHUB_ACTIONS": "true", "AURUM_DISPOSABLE_DB_CONFIRM": "destroy-this-database"}, "disposable *_test"),
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
                        env=environment, capture_output=True, text=True, timeout=5,
                        check=False,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(message, result.stderr)
                    self.assertNotIn("must-not-be-read", result.stderr)


if __name__ == "__main__":
    unittest.main()
