from reviewer.diff_parser import build_changed_line_map, parse_unified_diff
from reviewer.models import ReviewResult
from reviewer.review import (
    build_github_review_payload,
    build_review_prompt,
    filter_changed_files,
    validate_finding_lines,
)


def make_result(findings: list[dict]) -> ReviewResult:
    return ReviewResult.model_validate(
        {
            "decision": "ADVISORY",
            "risk_level": "MEDIUM",
            "findings": findings,
            "summary": "Review summary",
            "files_reviewed": ["demo.py"],
            "files_skipped": [],
            "categories_reviewed": ["Security"],
        }
    )


def finding(file: str = "demo.py", line: int | None = 8) -> dict:
    return {
        "severity": "HIGH",
        "category": "Security",
        "file": file,
        "line": line,
        "title": "Validate input",
        "issue": "The new value is used without validation.",
        "recommendation": "Validate the value before use.",
        "suggested_fix": "value = validate(value)",
    }


def test_single_file_diff_maps_added_lines_to_new_file_numbers():
    patch = "@@ -5,3 +5,5 @@\n context\n+names.append(pet[\"name\"])\n context\n+print(\"processing\")\n+return names\n"

    assert parse_unified_diff(patch) == [
        {"line": 6, "content": 'names.append(pet["name"])'},
        {"line": 8, "content": 'print("processing")'},
        {"line": 9, "content": "return names"},
    ]


def test_multi_file_diff_builds_separate_file_maps():
    files = [
        {"filename": "demo.py", "patch": "@@ -1 +1,2 @@\n one\n+two"},
        {"filename": "src/app.js", "patch": "@@ -3,0 +4 @@\n+ready()"},
    ]

    assert build_changed_line_map(files) == {
        "demo.py": [{"line": 2, "content": "two"}],
        "src/app.js": [{"line": 4, "content": "ready()"}],
    }


def test_added_line_is_extracted():
    assert parse_unified_diff("@@ -0,0 +1 @@\n+first") == [
        {"line": 1, "content": "first"}
    ]


def test_modified_line_maps_to_added_side():
    patch = "@@ -5 +5 @@\n-old value\n+new value"

    assert parse_unified_diff(patch) == [
        {"line": 5, "content": "new value"}
    ]


def test_deleted_line_advances_old_counter_without_becoming_inline_line():
    patch = "@@ -5,3 +5,2 @@\n context before\n-removed value\n context after\n+new value\n"

    assert parse_unified_diff(patch) == [
        {"line": 7, "content": "new value"}
    ]


def test_changed_line_extraction_handles_multiple_hunks():
    patch = "@@ -1,2 +1,3 @@\n one\n+inserted near top\n two\n@@ -20,2 +21,3 @@\n twenty one\n+inserted near bottom\n twenty two\n"

    assert parse_unified_diff(patch) == [
        {"line": 2, "content": "inserted near top"},
        {"line": 22, "content": "inserted near bottom"},
    ]


def test_invalid_model_line_is_rejected_to_null():
    result = make_result([finding(line=999), finding(file="other.py", line=8)])
    line_map = {"demo.py": [{"line": 8, "content": "safe = True"}]}

    validated = validate_finding_lines(result, line_map)

    assert [item.line for item in validated.findings] == [None, None]


def test_null_line_remains_null():
    result = make_result([finding(line=None)])
    line_map = {"demo.py": [{"line": 8, "content": "safe = True"}]}

    validated = validate_finding_lines(result, line_map)

    assert validated.findings[0].line is None


def test_prompt_lists_only_supplied_changed_lines_and_prohibits_guessing():
    prompt = build_review_prompt(
        repo_slug="org/repo",
        pr_title="Update names",
        pr_body="",
        diff_text="FILE: demo.py\n@@ -5 +5 @@\n+names.append(name)",
        changed_line_map={"demo.py": [{"line": 8, "content": "names.append(name)"}]},
    )

    assert "8: names.append(name)" in prompt
    assert "Only use supplied changed-line numbers" in prompt
    assert "line = null" in prompt


def test_changed_line_map_excludes_lines_outside_bounded_prompt_diff():
    filtered = filter_changed_files(
        [{"filename": "demo.py", "patch": "@@ -0,0 +1,2 @@\n+first\n+second"}],
        max_review_input_chars=37,
    )

    assert filtered["changed_line_map"] == {
        "demo.py": [{"line": 1, "content": "first"}]
    }


def test_summary_and_inline_comments_share_one_github_review_payload():
    result = make_result([finding(line=8), finding(line=9)])
    line_map = {"demo.py": [{"line": 8, "content": "safe = True"}]}

    payload = build_github_review_payload(result, line_map, "abc123")

    assert payload["event"] == "COMMENT"
    assert payload["commit_id"] == "abc123"
    assert "# AI Code Review" in payload["body"]
    assert "Line 8" in payload["body"]
    assert len(payload["comments"]) == 1
    assert payload["comments"][0]["path"] == "demo.py"
    assert payload["comments"][0]["line"] == 8
    assert payload["comments"][0]["side"] == "RIGHT"
    assert "Issue:" in payload["comments"][0]["body"]


def test_unlocated_finding_stays_in_summary_without_inline_payload():
    result = make_result([finding(line=None)])
    payload = build_github_review_payload(result, {"demo.py": []}, "abc123")

    assert "Line unknown" in payload["body"]
    assert "comments" not in payload