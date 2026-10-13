import pytest

from reviewer import review
from reviewer.models import ReviewResult


def test_filter_changed_files_ignores_generated_and_locked_files():
    files = [
        {"filename": "src/app.py", "patch": "@@\n+print('ok')\n"},
        {"filename": "node_modules/pkg/index.js", "patch": "+unwanted"},
        {"filename": "dist/bundle.js", "patch": "+bundle"},
        {"filename": "package-lock.json", "patch": "+lock"},
        {"filename": "image.png", "patch": "binary"},
    ]

    filtered = review.filter_changed_files(files)
    assert filtered["files_reviewed"] == ["src/app.py"]
    assert "node_modules/pkg/index.js" in filtered["files_skipped"]
    assert "package-lock.json" in filtered["files_skipped"]


def test_filter_changed_files_truncates_large_diff():
    big_patch = "@@\n" + "+" + ("x" * 20000) + "\n"
    files = [{"filename": "src/big.py", "patch": big_patch}]

    filtered = review.filter_changed_files(files, max_file_diff_chars=100, max_review_input_chars=200)
    assert len(filtered["diff_text"]) <= 200 + 50
    assert "[truncated]" in filtered["diff_text"]


def test_policy_loading_reads_expected_policy_text():
    policy = review.load_policy()
    assert "UNTRUSTED DATA" in policy
    assert "Security" in policy
    assert "Human approval remains required" in policy


def test_format_review_markdown_contains_expected_sections():
    result = ReviewResult.model_validate(
        {
            "decision": "ADVISORY / CHANGES REQUIRED",
            "risk_level": "MODERATE RISK",
            "findings": [
                {
                    "severity": "MEDIUM",
                    "category": "Coding Standards",
                    "file": "src/app.py",
                    "line": 79,
                    "title": "Keep naming consistent",
                    "issue": "The name is unclear.",
                    "recommendation": "Rename the variable.",
                    "suggested_fix": "value = parse_input(data)",
                }
            ],
            "summary": "One moderate issue was found.",
            "files_reviewed": ["src/app.py"],
            "files_skipped": [],
            "categories_reviewed": ["Coding Standards"],
        }
    )

    markdown = review.format_review_markdown(result)
    assert "# AI Code Review" in markdown
    assert "## Overall Assessment" in markdown
    assert "## Review Summary" in markdown
    assert "## Merge Recommendation" in markdown
    assert "Line 79" in markdown


def test_validate_result_logs_counts_before_and_after_schema_validation(capsys):
    payload = {
        "decision": "ADVISORY",
        "risk_level": "HIGH",
        "findings": [{
            "severity": "HIGH",
            "category": "Security",
            "file": "src/app.py",
            "line": 4,
            "title": "Missing validation",
            "issue": "The input is unchecked.",
            "recommendation": "Validate the input.",
        }],
        "summary": "One finding.",
        "files_reviewed": ["src/app.py"],
        "files_skipped": [],
        "categories_reviewed": ["Security"],
    }

    result = review.validate_result(payload)

    assert len(result.findings) == 1
    output = capsys.readouterr().out
    assert "stage=before_schema_validation count=1" in output
    assert "stage=after_schema_validation count=1" in output
    assert "total_before=1 total_after=1 discarded=0" in output
    assert "title='Missing validation' category='Security'" in output


def test_validate_result_logs_invalid_finding_reason(capsys):
    payload = {
        "decision": "ADVISORY",
        "risk_level": "HIGH",
        "findings": [{
            "severity": "UNKNOWN",
            "category": "Security",
            "file": "src/app.py",
            "line": 4,
            "title": "Bad severity finding",
            "issue": "The input is unchecked.",
            "recommendation": "Validate the input.",
        }],
        "summary": "One finding.",
        "files_reviewed": ["src/app.py"],
        "files_skipped": [],
        "categories_reviewed": ["Security"],
    }

    with pytest.raises(ValueError, match="Pydantic validation failed"):
        review.validate_result(payload)

    output = capsys.readouterr().out
    assert "validation_rejected title='Bad severity finding' category='Security'" in output
    assert "reason=item 0 failed ReviewFinding validation" in output
    assert "schema_validation_failed raw_finding_count=1" in output


def test_location_filter_logs_inline_discard_and_keeps_finding(capsys):
    result = ReviewResult.model_validate({
        "decision": "ADVISORY",
        "risk_level": "HIGH",
        "findings": [{
            "severity": "HIGH",
            "category": "Security",
            "file": "src/app.py",
            "line": 99,
            "title": "Missing validation",
            "issue": "The input is unchecked.",
            "recommendation": "Validate the input.",
        }],
        "summary": "One finding.",
        "files_reviewed": ["src/app.py"],
        "files_skipped": [],
        "categories_reviewed": ["Security"],
    })

    located = review.validate_finding_lines(result, {"src/app.py": [{"line": 4, "content": "changed"}]})
    comments = review.build_inline_review_comments(located, {"src/app.py": [{"line": 4, "content": "changed"}]})

    assert len(located.findings) == 1
    assert located.findings[0].line is None
    assert comments == []
    output = capsys.readouterr().out
    assert "stage=before_location_validation count=1" in output
    assert "stage=after_location_validation count=1" in output
    assert "total_before=1 total_after=1 discarded=0" in output
    assert "inline_location_excluded title='Missing validation' category='Security'" in output
    assert "inline_comment_discarded title='Missing validation'" in output
    assert "reason=line is not an added line in deterministic changed-line map" in output


def test_call_capgemini_rejects_malformed_json(monkeypatch):
    class FakeResponse:
        status_code = 200
        text = "not json"

        def json(self):
            raise ValueError("bad json")

    def fake_post(*args, **kwargs):
        return FakeResponse()

    monkeypatch.setattr(review.httpx, "post", fake_post)

    with pytest.raises(RuntimeError, match="malformed JSON"):
        review.call_capgemini(
            policy_text="policy",
            user_prompt="prompt",
            api_key="abc",
            model="gpt-4o-mini",
            base_url="https://example.com/v1",
        )


def test_call_capgemini_logs_timeout_duration_and_config(monkeypatch, capsys):
    times = iter((100.0, 161.25))
    monkeypatch.setattr(review.time, "perf_counter", lambda: next(times))

    def fake_post(*args, **kwargs):
        assert kwargs["timeout"] == 60
        raise review.httpx.ReadTimeout("upstream read timed out")

    monkeypatch.setattr(review.httpx, "post", fake_post)

    with pytest.raises(RuntimeError, match="request timed out"):
        review.call_capgemini(
            policy_text="policy",
            user_prompt="prompt",
            api_key="abc",
            model="openai.gpt-5",
            base_url="https://example.com/v1",
            timeout_seconds=60,
        )

    output = capsys.readouterr().out
    assert "duration_seconds=61.25" in output
    assert "configured_timeout_seconds=60" in output
    assert "exception=ReadTimeout" in output


def test_call_capgemini_reports_auth_http_status_separately(monkeypatch, capsys):
    class UnauthorizedResponse:
        status_code = 401
        text = "invalid credential"

    monkeypatch.setattr(review.httpx, "post", lambda *args, **kwargs: UnauthorizedResponse())

    with pytest.raises(RuntimeError, match="HTTP 401"):
        review.call_capgemini(
            policy_text="policy",
            user_prompt="prompt",
            api_key="abc",
            model="openai.gpt-5",
            base_url="https://example.com/v1",
        )

    output = capsys.readouterr().out
    assert "http_status=401" in output
    assert "timed out" not in output


def test_no_meaningful_files_returns_empty_diff():
    filtered = review.filter_changed_files([])
    assert filtered["files_reviewed"] == []
    assert filtered["diff_text"] == ""


def test_build_review_prompt_uses_untrusted_data_language():
    prompt = review.build_review_prompt(
        repo_slug="octo/demo",
        pr_title="Improve auth",
        pr_body="Fix login",
        diff_text="FILE: src/app.py\n+enabled = True",
    )

    assert "UNTRUSTED DATA" in prompt
    assert "Repository: octo/demo" in prompt
    assert "FILE: src/app.py" in prompt
