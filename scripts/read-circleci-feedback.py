"""Read bounded, public CircleCI feedback; never print or execute raw log content."""

import argparse
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from uuid import UUID

REPOSITORY = "Asadov2000/Aurum-Pharma"
PROJECT = "circleci/5TVfbSc2LuEiuYkFyUkVkv/AjapK6m9WDv5hPnBxVuf7T"
API_ORIGINS = {"api.github.com", "circleci.com", "app.circleci.com"}
LOG_ORIGINS = {"circleci.com", "app.circleci.com", "output.circle-artifacts.com"}
STEPS = {
    "Ruff",
    "Black",
    "Mypy",
    "Pytest",
    "Validate dependency lock",
    "Install locked dependencies",
}
TERMINAL = {"success", "failed", "timedout", "canceled"}
MAX_BYTES = 4 * 1024 * 1024
MAX_LINES = 50000
ANSI = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]")
MYPY_RULES = {
    "arg-type",
    "return-value",
    "assignment",
    "attr-defined",
    "name-defined",
    "call-arg",
    "union-attr",
    "index",
    "operator",
    "override",
    "type-var",
    "valid-type",
    "no-untyped-def",
    "no-untyped-call",
    "import-not-found",
    "import-untyped",
    "misc",
}
PATH = r"(?:app|tests|scripts)/[A-Za-z0-9_./-]+\.py"


class FeedbackError(Exception):
    """Only fixed, non-sensitive error codes cross the command boundary."""


def checked_url(url: str, *, logs: bool = False) -> str:
    if any(ord(char) < 33 or ord(char) > 126 for char in url):
        raise FeedbackError("unsafe_url")
    try:
        parsed = urllib.parse.urlsplit(url)
        allowed = LOG_ORIGINS if logs else API_ORIGINS
        if (
            parsed.scheme != "https"
            or parsed.hostname not in allowed
            or parsed.port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or "\\" in url
        ):
            raise FeedbackError("unsafe_url")
    except ValueError as exc:
        raise FeedbackError("unsafe_url") from exc
    return url


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, logs: bool) -> None:
        self.logs = logs
        self.count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.count += 1
        if self.count > 3:
            raise FeedbackError("redirect_limit")
        checked_url(newurl, logs=self.logs)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class PublicJson:
    """Default platform proxy/TLS; no auth, cookies, retries or alternate transport."""

    def __init__(self) -> None:
        self.deadline = time.monotonic() + 60
        self.remaining = 16 * 1024 * 1024

    def __call__(self, url: str, *, logs: bool = False) -> object:
        checked_url(url, logs=logs)
        remaining_time = self.deadline - time.monotonic()
        if remaining_time <= 0:
            raise FeedbackError("time_limit")
        request = urllib.request.Request(
            url, headers={"Accept": "application/json"}, method="GET"
        )
        opener = urllib.request.build_opener(SafeRedirect(logs))
        try:
            with opener.open(request, timeout=min(10, remaining_time)) as response:
                checked_url(response.url, logs=logs)
                if response.status != 200 or response.headers.get("Content-Encoding"):
                    raise FeedbackError("unexpected_response")
                if 'rel="next"' in response.headers.get("Link", ""):
                    raise FeedbackError("incomplete_pagination")
                raw = bytearray()
                while chunk := response.read(65536):
                    raw.extend(chunk)
                    if len(raw) > MAX_BYTES or len(raw) > self.remaining:
                        raise FeedbackError("byte_limit")
                    if time.monotonic() > self.deadline:
                        raise FeedbackError("time_limit")
                self.remaining -= len(raw)
                length = response.headers.get("Content-Length")
                if length is not None and int(length) != len(raw):
                    raise FeedbackError("incomplete_response")
                return json.loads(raw)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise FeedbackError("transport_or_json_error") from exc


def mapping(value: object) -> dict:
    if not isinstance(value, dict):
        raise FeedbackError("invalid_schema")
    return value


def sequence(value: object) -> list:
    if not isinstance(value, list):
        raise FeedbackError("invalid_schema")
    return value


def identifiers(sha: str, workflow: str) -> None:
    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise FeedbackError("invalid_sha")
    try:
        if str(UUID(workflow)) != workflow:
            raise ValueError()
    except ValueError as exc:
        raise FeedbackError("invalid_workflow") from exc


def diagnostics(payload: object) -> list[dict]:
    """Allowlisted categories/paths only: no source, SQL params, arbitrary messages or URLs."""
    entries = sequence(payload)
    found = []
    lines_count = 0
    nonempty = False
    for entry in entries:
        row = mapping(entry)
        message = row.get("message")
        if (
            not isinstance(message, str)
            or row.get("truncated")
            or row.get("output_truncated")
        ):
            raise FeedbackError("incomplete_log")
        if len(message) > 131072 or any(
            len(line) > 4096 for line in message.splitlines()
        ):
            raise FeedbackError("message_or_line_limit")
        clean = ANSI.sub("", message)
        clean = "".join(
            char for char in clean if char in "\n\t" or 32 <= ord(char) < 127
        )
        nonempty = nonempty or bool(clean.strip())
        if "output has been truncated" in clean.lower():
            raise FeedbackError("incomplete_log")
        for line in clean.splitlines():
            lines_count += 1
            if lines_count > MAX_LINES:
                raise FeedbackError("line_limit")
            if line.startswith("would reformat "):
                parts = line.removeprefix("would reformat ").strip().split("/")
                for index, part in enumerate(parts):
                    candidate = "/".join(parts[index:])
                    if part in {"app", "tests", "scripts"} and re.fullmatch(
                        PATH, candidate
                    ):
                        found.append({"kind": "black_format", "path": candidate})
                        break
            match = re.match(
                rf"({PATH}):(\d+)(?::\d+)?:.*?\b([A-Z]{{1,4}}\d{{3,4}})\b", line
            )
            if match:
                found.append(
                    {
                        "kind": "ruff",
                        "path": match[1],
                        "line": int(match[2]),
                        "rule": match[3],
                    }
                )
            match = re.match(rf"({PATH}):(\d+):.*?error:.*?\[([a-z-]+)\]", line)
            if match:
                found.append(
                    {
                        "kind": "mypy",
                        "path": match[1],
                        "line": int(match[2]),
                        "rule": match[3] if match[3] in MYPY_RULES else "mypy_error",
                    }
                )
            match = re.match(rf"FAILED ({PATH})::([A-Za-z0-9_]+)", line)
            if match:
                found.append({"kind": "pytest", "path": match[1], "test": match[2]})
    if not nonempty:
        raise FeedbackError("empty_log")
    safe = [row for row in found if ".." not in row["path"].split("/")]
    unique = [
        dict(items) for items in dict.fromkeys(tuple(row.items()) for row in safe)
    ]
    if len(unique) > 50:
        raise FeedbackError("diagnostic_limit")
    return unique


def inspect(
    sha: str, workflow: str, job_name: str, step: str | None, fetch: Callable
) -> dict:
    identifiers(sha, workflow)
    if job_name not in {
        "backend",
        "frontend",
        "e2e",
        "tooling-linux",
        "tooling-windows",
        "dependency-audit",
        "production-containers",
        "ci-gate",
    }:
        raise FeedbackError("unsupported_job")
    status = mapping(
        fetch(
            f"https://api.github.com/repos/{REPOSITORY}/commits/{sha}/status?per_page=100"
        )
    )
    if (
        status.get("sha") != sha
        or mapping(status.get("repository")).get("full_name") != REPOSITORY
    ):
        raise FeedbackError("repository_or_sha_mismatch")
    statuses = sequence(status.get("statuses"))
    if status.get("total_count") != len(statuses):
        raise FeedbackError("incomplete_statuses")
    contexts = [
        mapping(row)
        for row in statuses
        if mapping(row).get("context") == f"ci/circleci: {job_name}"
    ]
    if len(contexts) != 1:
        raise FeedbackError("missing_current_context")
    target = contexts[0].get("target_url")
    if not isinstance(target, str):
        raise FeedbackError("missing_current_target")
    parsed = urllib.parse.urlsplit(checked_url(target))
    match = re.fullmatch(rf"/workflow/{workflow}/job/([a-f0-9-]{{36}})", parsed.path)
    if parsed.hostname != "app.circleci.com" or parsed.query or not match:
        raise FeedbackError("workflow_or_attempt_mismatch")
    jobs = mapping(fetch(f"https://circleci.com/api/v2/workflow/{workflow}/job"))
    if jobs.get("next_page_token"):
        raise FeedbackError("incomplete_pagination")
    candidates = [
        mapping(row)
        for row in sequence(jobs.get("items"))
        if mapping(row).get("name") == job_name
    ]
    if not candidates or any(
        type(row.get("job_number")) is not int or row["job_number"] <= 0
        for row in candidates
    ):
        raise FeedbackError("missing_job")
    latest = max(candidates, key=lambda row: row["job_number"])
    if latest.get("id") != match[1] or latest.get("project_slug") != PROJECT:
        raise FeedbackError("project_or_attempt_mismatch")
    number = latest["job_number"]
    job = mapping(
        fetch(f"https://app.circleci.com/api/v1.1/project/{PROJECT}/{number}")
    )
    scope = mapping(job.get("workflows"))
    if (
        job.get("build_num") != number
        or job.get("vcs_revision") != sha
        or scope.get("workflow_id") != workflow
        or scope.get("job_id") != latest["id"]
        or scope.get("job_name") != job_name
    ):
        raise FeedbackError("job_provenance_mismatch")
    if job.get("status") not in TERMINAL:
        raise FeedbackError("job_not_terminal")
    if latest.get("status") != job["status"] or contexts[0].get("state") not in {
        "success",
        "failure",
        "pending",
        "error",
    }:
        raise FeedbackError("inconsistent_status")
    if (
        contexts[0]["state"] == "success"
        and job["status"] != "success"
        or contexts[0]["state"] in {"failure", "error"}
        and job["status"] == "success"
    ):
        raise FeedbackError("inconsistent_status")
    parallel = job.get("parallel")
    if type(parallel) is not int or not 1 <= parallel <= 16:
        raise FeedbackError("invalid_parallelism")
    selected = []
    for raw_step in sequence(job.get("steps")):
        current = mapping(raw_step)
        name = current.get("name")
        actions = sequence(current.get("actions"))
        wanted = (
            name == step
            if step
            else (
                name in {"Ruff", "Black", "Mypy"}
                if job["status"] == "success"
                else any(mapping(a).get("status") == "failed" for a in actions)
            )
        )
        if wanted:
            if name not in STEPS or not actions or len(actions) > 16:
                raise FeedbackError("unsupported_or_incomplete_step")
            indexes = [mapping(action).get("index") for action in actions]
            if any(type(index) is not int for index in indexes) or sorted(
                indexes
            ) != list(range(parallel)):
                raise FeedbackError("incomplete_nodes")
            result = {"step": name, "nodes": []}
            for raw_action in actions:
                action = mapping(raw_action)
                output = action.get("output_url")
                if action.get("status") not in TERMINAL or not isinstance(output, str):
                    raise FeedbackError("incomplete_action")
                if job["status"] == "success" and action["status"] != "success":
                    raise FeedbackError("inconsistent_action_status")
                data = fetch(checked_url(output, logs=True), logs=True)
                extracted = diagnostics(data)
                result["nodes"].append(
                    {
                        "index": action.get("index"),
                        "status": action["status"],
                        "diagnostics": extracted,
                        "diagnostics_unavailable": action["status"] == "failed"
                        and not extracted,
                    }
                )
            selected.append(result)
    if not selected:
        raise FeedbackError("missing_requested_step")
    if (
        not step
        and job["status"] == "success"
        and {row["step"] for row in selected} != {"Ruff", "Black", "Mypy"}
    ):
        raise FeedbackError("missing_required_steps")
    return {
        "repository": REPOSITORY,
        "sha": sha,
        "workflow": workflow,
        "job": job_name,
        "build": number,
        "job_status": job["status"],
        "context_state": contexts[0].get("state"),
        "verified_complete": True,
        "steps": selected,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--job", default="backend")
    parser.add_argument("--step", choices=sorted(STEPS))
    args = parser.parse_args()
    try:
        result = inspect(args.sha, args.workflow, args.job, args.step, PublicJson())
        print(json.dumps(result))
        return (
            0
            if result["job_status"] == "success"
            and result["context_state"] == "success"
            else 1
        )
    except FeedbackError as exc:
        print(json.dumps({"verified_complete": False, "error": str(exc)}))
    except Exception:
        print(
            json.dumps(
                {"verified_complete": False, "error": "unexpected_schema_or_transport"}
            )
        )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
