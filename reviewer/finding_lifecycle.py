from __future__ import annotations

import hashlib
import json
import posixpath
import re
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

from .models import ReviewFinding
from .review import build_inline_review_comments

STATE_MARKER = "<!-- ai-review-finding-lifecycle:v1\n"
STATE_END_MARKER = "\n-->"


def normalize_file_path(value: str) -> str:
    candidate = value.replace("\\", "/").strip()
    while candidate.startswith("./"):
        candidate = candidate[2:]
    normalized = posixpath.normpath(candidate)
    return "" if normalized == "." else PurePosixPath(normalized).as_posix()


def normalize_text(value: str) -> str:
    return " ".join(value.casefold().split())


def normalize_code_context(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def normalize_issue_type(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def finding_fingerprint(
    finding: ReviewFinding | dict[str, Any],
    code_context: str,
) -> str:
    get_value = finding.get if isinstance(finding, dict) else lambda key: getattr(finding, key)
    severity = get_value("severity")
    if hasattr(severity, "value"):
        severity = severity.value
    file_path = normalize_file_path(str(get_value("file")))
    issue_type_value = finding.get("issue_type", finding.get("category")) if isinstance(finding, dict) else finding.category
    issue_type = normalize_issue_type(str(issue_type_value))
    normalized_severity = normalize_text(str(severity))
    line = get_value("line")
    normalized_context = normalize_code_context(code_context)
    context_digest = hashlib.sha256(normalized_context.encode("utf-8")).hexdigest()
    location = f"line:{line}" if isinstance(line, int) and line > 0 else f"context_sha256:{context_digest}"
    identity = "\x1f".join((file_path, issue_type, normalized_severity, location))
    print(
        f"[P3] Finding identity input file={file_path!r} issue_type={issue_type!r} "
        f"severity={normalized_severity!r} line={line!r} "
        f"location={location!r} title={str(get_value('title'))!r}"
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _finding_location_score(previous: dict[str, Any], current: dict[str, Any]) -> int | None:
    previous_category = normalize_issue_type(str(previous.get("issue_type") or previous.get("category") or ""))
    current_category = normalize_issue_type(str(current.get("issue_type") or current.get("category") or ""))
    previous_severity = normalize_text(str(previous.get("severity") or ""))
    current_severity = normalize_text(str(current.get("severity") or ""))
    if (
        normalize_file_path(str(previous.get("file") or ""))
        != normalize_file_path(str(current.get("file") or ""))
        or not previous_category
        or previous_category != current_category
        or not previous_severity
        or previous_severity != current_severity
    ):
        return None

    previous_line = previous.get("line")
    current_line = current.get("line")
    if isinstance(previous_line, int) and previous_line > 0 and previous_line == current_line:
        return 0

    previous_context = previous.get("code_context")
    current_context = current.get("code_context")
    if (
        isinstance(previous_context, str)
        and bool(normalize_code_context(previous_context))
        and isinstance(current_context, str)
        and normalize_code_context(previous_context) == normalize_code_context(current_context)
    ):
        return 1

    if (
        isinstance(previous_line, int)
        and previous_line > 0
        and isinstance(current_line, int)
        and current_line > 0
        and abs(previous_line - current_line) <= 5
    ):
        return 10 + abs(previous_line - current_line)
    return None


def _source_code_context(
    finding: ReviewFinding,
    changed_line_map: dict[str, list[dict[str, int | str]]],
    target_repo_path: str | None,
) -> str:
    path = normalize_file_path(finding.file)
    if target_repo_path:
        root = Path(target_repo_path).resolve()
        candidate = (root / Path(*PurePosixPath(path).parts)).resolve()
        if candidate.is_relative_to(root) and candidate.is_file() and finding.line is not None:
            try:
                lines = candidate.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                lines = []
            if 1 <= finding.line <= len(lines):
                start = max(0, finding.line - 2)
                end = min(len(lines), finding.line + 1)
                context = "\n".join(lines[start:end])
                if context.strip():
                    return context

    changed_lines = changed_line_map.get(path, changed_line_map.get(finding.file, []))
    if finding.line is not None:
        matching = [item for item in changed_lines if item.get("line") == finding.line]
        if matching:
            return str(matching[0].get("content", ""))
    return "\n".join(str(item.get("content", "")) for item in changed_lines[:5])


def build_finding_records(
    result: Any,
    changed_line_map: dict[str, list[dict[str, int | str]]],
    target_repo_path: str | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for finding in result.findings:
        code_context = _source_code_context(finding, changed_line_map, target_repo_path)
        finding_id = finding_fingerprint(finding, code_context)
        single_finding_result = result.model_copy(update={"findings": [finding]})
        comments = build_inline_review_comments(single_finding_result, changed_line_map, [finding_id])
        records.append({
            "finding_id": finding_id,
            "thread_id": None,
            "status": "open",
            "file": normalize_file_path(finding.file),
            "line": finding.line,
            "category": finding.category,
            "issue_type": normalize_issue_type(finding.category),
            "severity": finding.severity.value,
            "title": finding.title,
            "issue": finding.issue,
            "recommendation": finding.recommendation,
            "suggested_fix": finding.suggested_fix,
            "code_context": code_context,
            "inline_body": comments[0]["body"] if comments else None,
        })
    return records


def compare_findings(
    previous_records: list[dict[str, Any]],
    current_findings: list[dict[str, Any]],
    repository: str,
    pr_number: int,
    head_sha: str,
    reviewed_files: list[str],
    resolve_unreviewed: bool = False,
) -> dict[str, Any]:
    prior_by_id = {
        str(record.get("finding_id")): record
        for record in previous_records
        if record.get("repository") == repository
        and record.get("pr_number") == pr_number
        and record.get("finding_id")
    }
    current_by_id = {
        str(record["finding_id"]): record
        for record in current_findings
        if record.get("finding_id")
    }
    reviewed = {normalize_file_path(path) for path in reviewed_files}
    records: dict[str, dict[str, Any]] = {}
    new_findings: list[dict[str, Any]] = []
    existing_findings: list[dict[str, Any]] = []
    reopened_findings: list[dict[str, Any]] = []
    resolved_findings: list[dict[str, Any]] = []
    matched_previous_ids: set[str] = set()

    for finding_id, current in current_by_id.items():
        previous = prior_by_id.get(finding_id)
        match_reason = "finding_id" if previous is not None else "none"
        if previous is None:
            candidates = [
                (score, previous_id, candidate)
                for previous_id, candidate in prior_by_id.items()
                if previous_id not in matched_previous_ids
                if (score := _finding_location_score(candidate, current)) is not None
            ]
            if candidates:
                best_score = min(score for score, _, _ in candidates)
                best_candidates = [item for item in candidates if item[0] == best_score]
            else:
                best_candidates = []
            if len(best_candidates) == 1:
                _, previous_id, previous = best_candidates[0]
                matched_previous_ids.add(previous_id)
                match_reason = "stable_location"
            elif best_candidates:
                print(
                    f"[P3] Finding identity ambiguous finding_id={finding_id} "
                    f"title={current.get('title')!r} "
                    f"candidates={[item[1] for item in best_candidates]}"
                )
        elif finding_id in prior_by_id:
            matched_previous_ids.add(finding_id)

        old_title = repr(previous.get("title")) if previous is not None else "None"
        old_finding_id = repr(previous.get("finding_id")) if previous is not None else "None"
        print(
            f"[P3] Finding identity match result={match_reason} "
            f"old_title={old_title} "
            f"new_title={current.get('title')!r} "
            f"old_finding_id={old_finding_id} "
            f"new_finding_id={finding_id!r}"
        )
        record = {
            **(previous or {}),
            **current,
            "finding_id": finding_id,
            "repository": repository,
            "pr_number": pr_number,
            "head_sha": head_sha,
            "status": "open",
        }
        if previous is None:
            new_findings.append(record)
        else:
            existing_findings.append(record)
            if previous.get("status") == "resolved":
                reopened_findings.append(record)
        records[finding_id] = record

    for finding_id, previous in prior_by_id.items():
        if finding_id in current_by_id or finding_id in matched_previous_ids:
            continue
        record = dict(previous)
        record["head_sha"] = head_sha
        file_path = normalize_file_path(str(record.get("file", "")))
        if record.get("status") == "open" and (resolve_unreviewed or file_path in reviewed):
            record["status"] = "resolved"
            resolved_findings.append(record)
        records[finding_id] = record

    return {
        "records": list(records.values()),
        "new_findings": new_findings,
        "existing_findings": existing_findings,
        "resolved_findings": resolved_findings,
        "reopened_findings": reopened_findings,
    }


def encode_lifecycle_state(repository: str, pr_number: int, records: list[dict[str, Any]]) -> str:
    payload = {
        "version": 1,
        "repository": repository,
        "pr_number": pr_number,
        "findings": records,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"{STATE_MARKER}{encoded}{STATE_END_MARKER}"


def decode_lifecycle_state(
    body: str,
    repository: str,
    pr_number: int,
) -> list[dict[str, Any]] | None:
    if not body.startswith(STATE_MARKER) or not body.endswith(STATE_END_MARKER):
        return None
    encoded = body[len(STATE_MARKER):-len(STATE_END_MARKER)]
    try:
        payload = json.loads(encoded)
    except json.JSONDecodeError:
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("version") != 1
        or payload.get("repository") != repository
        or payload.get("pr_number") != pr_number
        or not isinstance(payload.get("findings"), list)
    ):
        return None
    return [
        item for item in payload["findings"]
        if isinstance(item, dict) and item.get("finding_id")
    ]


def append_lifecycle_summary(body: str, lifecycle: dict[str, Any]) -> str:
    sections = (
        ("Resolved Since Last Review", lifecycle.get("resolved_findings") or [], "✅"),
        ("New Findings", lifecycle.get("new_findings") or [], "🆕"),
        ("Existing Findings", lifecycle.get("existing_findings") or [], "⚠"),
    )
    lines = ["## Finding Lifecycle"]
    for heading, findings, icon in sections:
        lines.extend(("", f"### {heading}"))
        if not findings:
            lines.extend(("", "None."))
            continue
        for finding in findings:
            title = str(finding.get("title") or "Untitled finding").replace("\n", " ")
            file_path = str(finding.get("file") or "unknown file").replace("\n", " ")
            lines.extend(("", f"{icon} **{title}** (`{file_path}`)"))
    return f"{body.rstrip()}\n\n" + "\n".join(lines) + "\n"