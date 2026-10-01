import pytest
from pydantic import ValidationError

from reviewer.models import ReviewFinding, ReviewResult, Severity


def test_valid_review_result_model():
    payload = {
        "decision": "ADVISORY / CHANGES REQUIRED",
        "risk_level": "MODERATE RISK",
        "findings": [
            {
                "severity": "MEDIUM",
                "category": "Coding Standards",
                "file": "src/app.py",
                "line": 79,
                "title": "Use a clearer variable name",
                "issue": "The variable name is unclear.",
                "recommendation": "Rename the variable to improve readability.",
                "suggested_fix": "value = parse_input(data)",
            }
        ],
        "summary": "The patch is mostly safe but needs a small readability improvement.",
        "files_reviewed": ["src/app.py"],
        "files_skipped": [],
        "categories_reviewed": ["Coding Standards"],
    }

    result = ReviewResult.model_validate(payload)
    assert result.findings[0].severity is Severity.MEDIUM
    assert result.findings[0].line == 79


def test_invalid_severity_is_rejected():
    payload = {
        "decision": "ADVISORY / CHANGES REQUIRED",
        "risk_level": "MODERATE RISK",
        "findings": [
            {
                "severity": "DANGEROUS",
                "category": "Security",
                "file": "src/app.py",
                "line": None,
                "title": "Bad severity",
                "issue": "Bad input",
                "recommendation": "Fix it",
                "suggested_fix": "x = 1",
            }
        ],
        "summary": "bad",
        "files_reviewed": ["src/app.py"],
        "files_skipped": [],
        "categories_reviewed": ["Security"],
    }

    with pytest.raises(ValidationError):
        ReviewResult.model_validate(payload)


def test_line_can_be_nullable():
    finding = ReviewFinding(
        severity=Severity.INFO,
        category="Logging",
        file="src/logger.py",
        line=None,
        title="Add context to logs",
        issue="The patch lacks relevant context.",
        recommendation="Include more context in logs.",
        suggested_fix="logger.info(\"processing %s\", request_id)",
    )

    assert finding.line is None
