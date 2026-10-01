from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict


class Severity(str, Enum):
    BLOCKER = "BLOCKER"
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


class ReviewFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Severity
    category: str
    file: str
    line: Optional[int] = None
    title: str
    issue: str
    recommendation: str
    suggested_fix: str = ""


class ReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str
    risk_level: str
    findings: list[ReviewFinding]
    summary: str
    files_reviewed: list[str]
    files_skipped: list[str]
    categories_reviewed: list[str]
