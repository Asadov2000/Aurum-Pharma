"""Offline safety tests: no CircleCI credentials, network, services or real log fixtures."""

import copy
import io
import json
import pathlib
import runpy
import unittest
import urllib.error
from http.client import HTTPMessage
from unittest.mock import patch
from urllib.request import Request

MODULE = runpy.run_path(
    str(pathlib.Path(__file__).with_name("read-circleci-feedback.py"))
)
SHA = "a" * 40
WORKFLOW = "11111111-1111-4111-8111-111111111111"
JOB = "22222222-2222-4222-8222-222222222222"
KNOWN_PATHS = frozenset({"tests/test_example.py", "app/service.py"})
ERROR = MODULE["FeedbackError"]
INSPECT = MODULE["inspect"]
CHECK_URL = MODULE["checked_url"]
DIAGNOSTICS = MODULE["diagnostics"]


def fixtures() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    status = {
        "sha": SHA,
        "repository": {"full_name": MODULE["REPOSITORY"]},
        "total_count": 1,
        "statuses": [
            {
                "context": "ci/circleci: backend",
                "state": "success",
                "target_url": f"https://app.circleci.com/workflow/{WORKFLOW}/job/{JOB}",
            }
        ],
    }
    workflow = {
        "items": [
            {
                "id": JOB,
                "name": "backend",
                "job_number": 42,
                "project_slug": MODULE["PROJECT"],
                "status": "success",
            }
        ],
        "next_page_token": None,
    }
    job = {
        "build_num": 42,
        "vcs_revision": SHA,
        "status": "success",
        "parallel": 2,
        "workflows": {"workflow_id": WORKFLOW, "job_id": JOB, "job_name": "backend"},
        "steps": [
            {
                "name": name,
                "actions": [
                    {
                        "index": index,
                        "status": "success",
                        "output_url": f"https://circleci.com/output/{name}/{index}?signed=DO_NOT_PRINT",
                    }
                    for index in (0, 1)
                ],
            }
            for name in ("Ruff", "Black", "Mypy")
        ],
    }
    return status, workflow, job


class FakeFetch:
    def __init__(self) -> None:
        self.status, self.workflow, self.job = fixtures()
        self.output = [{"message": "All checks passed.\n"}]
        self.calls: list[tuple[str, bool]] = []

    def __call__(self, url: str, *, logs: bool = False) -> object:
        self.calls.append((url, logs))
        if logs:
            return copy.deepcopy(self.output)
        if "api.github.com" in url:
            return copy.deepcopy(self.status)
        if "/api/v2/" in url:
            return copy.deepcopy(self.workflow)
        return copy.deepcopy(self.job)


class ProvenanceTests(unittest.TestCase):
    def test_verified_success_reads_every_selected_node_without_printing_signed_url(
        self,
    ) -> None:
        fetch = FakeFetch()
        result = INSPECT(SHA, WORKFLOW, "backend", None, fetch, KNOWN_PATHS)
        self.assertTrue(result["verified_complete"])
        self.assertEqual(len(fetch.calls), 9)
        self.assertNotIn("DO_NOT_PRINT", json.dumps(result))

    def test_first_failed_formatter_returns_only_sanitized_relative_path(self) -> None:
        fetch = FakeFetch()
        fetch.status["statuses"][0]["state"] = "failure"
        fetch.workflow["items"][0]["status"] = "failed"
        fetch.job["status"] = "failed"
        for action in fetch.job["steps"][1]["actions"]:
            action["status"] = "failed"
        fetch.output = [
            {
                "message": "\x1b[31mwould reformat /home/circleci/project/backend/tests/test_example.py\x1b[0m\nSQL parameters: PRIVATE_VALUE\n"
            }
        ]
        result = INSPECT(SHA, WORKFLOW, "backend", "Black", fetch, KNOWN_PATHS)
        self.assertEqual(
            result["steps"][0]["nodes"][0]["diagnostics"],
            [{"kind": "black_format", "path": "tests/test_example.py"}],
        )
        self.assertNotIn("PRIVATE_VALUE", json.dumps(result))
        self.assertNotIn("\x1b", json.dumps(result))

    def test_wrong_repository_sha_workflow_job_or_attempt_fails_closed(self) -> None:
        changes = [
            lambda f: f.status.update(sha="b" * 40),
            lambda f: f.status["repository"].update(full_name="other/repo"),
            lambda f: f.status["statuses"][0].update(
                target_url="https://app.circleci.com/workflow/other/job/other"
            ),
            lambda f: f.job.update(vcs_revision="b" * 40),
            lambda f: f.job["workflows"].update(job_id=WORKFLOW),
            lambda f: f.workflow["items"][0].update(project_slug="other/project"),
            lambda f: f.workflow["items"].append(
                {**f.workflow["items"][0], "job_number": 43, "id": WORKFLOW}
            ),
        ]
        for change in changes:
            with self.subTest(change=changes.index(change)):
                fetch = FakeFetch()
                change(fetch)
                with self.assertRaises(ERROR):
                    INSPECT(SHA, WORKFLOW, "backend", None, fetch, KNOWN_PATHS)

    def test_partial_empty_running_or_contradictory_data_never_reports_complete(
        self,
    ) -> None:
        changes = [
            lambda f: f.status.update(total_count=2),
            lambda f: f.workflow.update(next_page_token="more"),
            lambda f: f.workflow["items"][0].update(job_number=True),
            lambda f: f.workflow["items"][0].update(job_number=0),
            lambda f: f.workflow["items"][0].update(status="failed"),
            lambda f: f.job.update(status="running"),
            lambda f: f.job["steps"].pop(),
            lambda f: f.job["steps"][0]["actions"].pop(),
            lambda f: f.job["steps"][0]["actions"][0].pop("output_url"),
            lambda f: f.job["steps"][0]["actions"][0].update(status="failed"),
            lambda f: setattr(f, "output", []),
            lambda f: setattr(f, "output", [{"message": ""}]),
            lambda f: setattr(f, "output", [{"message": "x", "truncated": True}]),
        ]
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                fetch = FakeFetch()
                change(fetch)
                with self.assertRaises(ERROR):
                    INSPECT(SHA, WORKFLOW, "backend", None, fetch, KNOWN_PATHS)


class TransportAndRedactionTests(unittest.TestCase):
    def test_long_control_or_repeated_prefix_input_is_rejected_before_regex(
        self,
    ) -> None:
        for text in ("x" * 131073, "\x1b]" * 10000, "would reformat " * 1000):
            with self.subTest(length=len(text)), patch.dict(
                DIAGNOSTICS.__globals__, ANSI=None
            ):
                with self.assertRaisesRegex(ERROR, "message_or_line_limit"):
                    DIAGNOSTICS([{"message": text}], KNOWN_PATHS)

    def test_unknown_mypy_code_cannot_echo_a_lowercase_secret(self) -> None:
        result = DIAGNOSTICS(
            [{"message": "app/service.py:13: error: ignored [private-secret]\n"}],
            KNOWN_PATHS,
        )
        self.assertEqual(result[0]["rule"], "mypy_error")
        self.assertNotIn("private-secret", json.dumps(result))

    def test_untracked_paths_and_arbitrary_test_names_never_escape(self) -> None:
        payload = [
            {
                "message": "would reformat app/PRIVATE_VALUE.py\n"
                "FAILED tests/test_example.py::PRIVATE_VALUE\n"
                "app/PRIVATE_VALUE.py:13: error: ignored [arg-type]\n"
            }
        ]
        result = DIAGNOSTICS(payload, KNOWN_PATHS)
        self.assertEqual(result, [{"kind": "pytest", "path": "tests/test_example.py"}])
        self.assertNotIn("PRIVATE_VALUE", json.dumps(result))

    def test_http_errors_preserve_only_fixed_status_or_rate_limit(self) -> None:
        for status, remaining, expected in (
            (429, "1", "http_rate_limited"),
            (403, "0", "http_rate_limited"),
            (503, "1", "http_status_503"),
        ):
            headers = HTTPMessage()
            headers["X-RateLimit-Remaining"] = remaining
            error = urllib.error.HTTPError(
                "https://circleci.com/PRIVATE_VALUE",
                status,
                "PRIVATE_VALUE",
                headers,
                None,
            )
            with patch("urllib.request.build_opener") as build:
                build.return_value.open.side_effect = error
                with self.assertRaisesRegex(ERROR, "^" + expected + "$"):
                    MODULE["PublicJson"]()("https://circleci.com/api/example")

    def test_malicious_urls_are_rejected_before_transport(self) -> None:
        for url in (
            "http://circleci.com/x",
            "https://circleci.com.evil/x",
            "https://u:secret@circleci.com/x",
            "https://127.0.0.1/x",
            "https://circleci.com:444/x",
            "https://circleci.com/x#fragment",
            "https://circleci.com/\nheader",
            "https://circleci.com\\@evil/x",
        ):
            with self.subTest(url=url), self.assertRaises(ERROR):
                CHECK_URL(url, logs=True)

    def test_redirects_are_checked_per_hop_and_limited(self) -> None:
        redirect = MODULE["SafeRedirect"](True)
        request = Request(
            "https://circleci.com/start", headers={"Accept": "application/json"}
        )
        for _ in range(3):
            next_request = redirect.redirect_request(
                request,
                io.BytesIO(),
                302,
                "",
                HTTPMessage(),
                "https://circleci.com/next?signature=HIDDEN",
            )
            self.assertEqual(next_request.get_method(), "GET")
            self.assertIsNone(next_request.get_header("Authorization"))
        with self.assertRaises(ERROR):
            redirect.redirect_request(
                request,
                io.BytesIO(),
                302,
                "",
                HTTPMessage(),
                "https://circleci.com/next",
            )
        with self.assertRaises(ERROR):
            MODULE["SafeRedirect"](True).redirect_request(
                request, io.BytesIO(), 302, "", HTTPMessage(), "https://evil.invalid/"
            )

    def test_untrusted_messages_do_not_echo_sql_params_tokens_commands_or_test_parameters(
        self,
    ) -> None:
        log = [
            {
                "message": "FAILED tests/test_example.py::test_access[PRIVATE_VALUE] - SQL PRIVATE_VALUE\n"
                "Authorization: PRIVATE_VALUE\nCookie: PRIVATE_VALUE\nrm -rf /anything\n"
                "app/service.py:12:4: E501 PRIVATE_VALUE\n"
                "app/service.py:13: error: PRIVATE_VALUE [arg-type]\n"
            }
        ]
        text = json.dumps(DIAGNOSTICS(log, KNOWN_PATHS))
        self.assertNotIn("PRIVATE_VALUE", text)
        self.assertNotIn("rm -rf", text)
        self.assertIn("E501", text)
        self.assertIn("arg-type", text)

    def test_limits_and_truncated_output_are_errors(self) -> None:
        for payload in (
            [{"message": "output has been truncated"}],
            [{"message": "x\n" * (MODULE["MAX_LINES"] + 1)}],
            [{"message": "\x1b[31m\x00\x1b[0m"}],
        ):
            with self.assertRaises(ERROR):
                DIAGNOSTICS(payload, KNOWN_PATHS)

    def test_http_get_only_preserves_timeout_and_rejects_short_or_oversized_body(
        self,
    ) -> None:
        class Response(io.BytesIO):
            status = 200
            url = "https://circleci.com/api/example"
            headers = {"Content-Length": "2"}

        for raw, length in (
            (b"{}", "2"),
            (b"{}", "3"),
            (b"x" * (MODULE["MAX_BYTES"] + 1), None),
        ):
            response = Response(raw)
            response.headers = {} if length is None else {"Content-Length": length}
            with patch("urllib.request.build_opener") as build:
                build.return_value.open.return_value = response
                if length == "2":
                    self.assertEqual(MODULE["PublicJson"]()(response.url), {})
                else:
                    with self.assertRaises(ERROR):
                        MODULE["PublicJson"]()(response.url)
                args, kwargs = build.return_value.open.call_args
                self.assertEqual(args[0].get_method(), "GET")
                self.assertLessEqual(kwargs["timeout"], 10)
                self.assertIsNone(args[0].get_header("Authorization"))
                self.assertIsNone(args[0].get_header("Cookie"))


if __name__ == "__main__":
    unittest.main()
