from __future__ import annotations

import re
from typing import Any

HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
FILE_HEADER = re.compile(r"(?m)^FILE: (.+)\n")


def parse_unified_diff(patch: str) -> list[dict[str, int | str]]:
    """Return added lines with their exact line numbers in the new file.

    Deleted lines advance only the old-file counter. This keeps subsequent
    additions correctly numbered and intentionally leaves deletion-only
    findings without an inline-eligible right-side line.
    """
    changed_lines: list[dict[str, int | str]] = []
    old_line: int | None = None
    new_line: int | None = None

    for diff_line in patch.splitlines():
        hunk = HUNK_HEADER.match(diff_line)
        if hunk:
            old_line = int(hunk.group(1))
            new_line = int(hunk.group(2))
            continue

        if old_line is None or new_line is None or diff_line.startswith("\\"):
            continue

        if diff_line.startswith("+++") or diff_line.startswith("---"):
            continue
        if diff_line.startswith("+"):
            changed_lines.append({"line": new_line, "content": diff_line[1:]})
            new_line += 1
        elif diff_line.startswith("-"):
            old_line += 1
        elif diff_line.startswith(" "):
            old_line += 1
            new_line += 1

    return changed_lines


def build_changed_line_map(changed_files: list[dict[str, Any]]) -> dict[str, list[dict[str, int | str]]]:
    """Build a per-file map of inline-eligible added lines from GitHub patches."""
    line_map: dict[str, list[dict[str, int | str]]] = {}
    for entry in changed_files:
        file_name = (entry.get("filename") or entry.get("file") or "").strip()
        if not file_name:
            continue
        line_map[file_name] = parse_unified_diff(entry.get("patch") or "")
    return line_map


def build_changed_line_map_from_prompt_diff(diff_text: str) -> dict[str, list[dict[str, int | str]]]:
    """Build the map from the bounded diff text actually sent to the model."""
    headers = list(FILE_HEADER.finditer(diff_text))
    line_map: dict[str, list[dict[str, int | str]]] = {}
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(diff_text)
        file_name = header.group(1).strip()
        patch = diff_text[header.end():end]
        line_map[file_name] = parse_unified_diff(patch)
    return line_map