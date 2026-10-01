from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

from reviewer.models import ReviewResult
from reviewer.review import (
    MAX_FILE_DIFF_CHARS,
    MAX_FILES,
    MAX_REVIEW_INPUT_CHARS,
    REVIEW_TIMEOUT_SECONDS,
    build_review_prompt,
    call_capgemini,
    filter_changed_files,
    format_review_markdown,
    load_policy,
    validate_result,
)


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def load_event() -> dict:
    event_path = os.getenv("GITHUB_EVENT_PATH")
    if not event_path:
        raise RuntimeError("Missing GITHUB_EVENT_PATH. This workflow must run from GitHub Actions.")
    path = Path(event_path)
    if not path.exists():
        raise RuntimeError(f"GitHub event payload file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("GitHub event payload is not valid JSON.") from exc
    if "pull_request" not in data:
        raise RuntimeError("Malformed or missing pull_request event context.")
    return data


def github_request(path: str, token: str, method: str = "GET", body: dict | None = None) -> dict | list:
    url = f"https://api.github.com{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        response = httpx.request(method, url, headers=headers, json=body, timeout=60)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"GitHub API request failed for {path}: {exc}") from exc

    if response.status_code >= 400:
        message = response.text[:300].strip()
        raise RuntimeError(f"GitHub API error {response.status_code} for {path}: {message}")

    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(f"GitHub API returned no JSON for {path}.") from exc


def ensure_pr_context(event: dict) -> dict:
    pr = event.get("pull_request") or {}
    if not pr:
        raise RuntimeError("Missing pull_request context in the GitHub event payload.")
    repo_slug = os.getenv("GITHUB_REPOSITORY") or event.get("repository", {}).get("full_name")
    if not repo_slug or "/" not in repo_slug:
        raise RuntimeError("Unable to determine repository owner and name from GitHub context.")
    pr_number = pr.get("number") or os.getenv("PR_NUMBER")
    if not pr_number:
        raise RuntimeError("Unable to determine the pull request number.")
    return {
        "repo_slug": repo_slug,
        "pr_number": int(pr_number),
        "head_sha": pr.get("head", {}).get("sha") or os.getenv("PR_HEAD_SHA"),
        "base_sha": pr.get("base", {}).get("sha") or os.getenv("PR_BASE_SHA"),
        "title": pr.get("title") or "Pull Request",
        "body": pr.get("body") or "",
    }


def main() -> None:
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("Missing GITHUB_TOKEN. Ensure the workflow exposes the GitHub token to the runner.")

    api_key = os.getenv("GEP_API_KEY")
    if not api_key:
        raise RuntimeError("Missing GEP_API_KEY. Configure the secret in GitHub before running reviews.")

    event = load_event()
    pr_context = ensure_pr_context(event)
    repo_owner, repo_name = pr_context["repo_slug"].split("/", 1)

    files_payload = github_request(
        f"/repos/{repo_owner}/{repo_name}/pulls/{pr_context['pr_number']}/files",
        token=token,
    )
    if not isinstance(files_payload, list):
        raise RuntimeError("GitHub returned an unexpected payload for changed files.")

    filtered = filter_changed_files(
        files_payload,
        max_files=int(os.getenv("MAX_FILES", str(MAX_FILES))),
        max_file_diff_chars=int(os.getenv("MAX_FILE_DIFF_CHARS", str(MAX_FILE_DIFF_CHARS))),
        max_review_input_chars=int(os.getenv("MAX_REVIEW_INPUT_CHARS", str(MAX_REVIEW_INPUT_CHARS))),
    )

    if not filtered["files_reviewed"] or not filtered["diff_text"].strip():
        raise RuntimeError("No meaningful changed files were available for review after filtering.")

    policy_text = load_policy()
    prompt = build_review_prompt(
        repo_slug=pr_context["repo_slug"],
        pr_title=pr_context["title"],
        pr_body=pr_context["body"],
        diff_text=filtered["diff_text"],
    )

    model_name = os.getenv("REVIEW_MODEL", "gpt-4o-mini")
    base_url = os.getenv("OPENAI_BASE_URL", "https://openai.generative.engine.capgemini.com/v1")
    timeout_seconds = int(os.getenv("REVIEW_TIMEOUT_SECONDS", str(REVIEW_TIMEOUT_SECONDS)))

    raw_response = call_capgemini(
        policy_text=policy_text,
        user_prompt=prompt,
        api_key=api_key,
        model=model_name,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
    )

    review_result = validate_result(raw_response)
    formatted_review = format_review_markdown(review_result)

    github_request(
        f"/repos/{repo_owner}/{repo_name}/pulls/{pr_context['pr_number']}/reviews",
        token=token,
        method="POST",
        body={
            "event": "COMMENT",
            "body": formatted_review,
            "commit_id": pr_context["head_sha"],
        },
    )

    print("AI PR review posted successfully.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover - runtime guard
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
