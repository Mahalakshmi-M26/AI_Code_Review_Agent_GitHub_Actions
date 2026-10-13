from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from reviewer.review import (
    MAX_FILE_DIFF_CHARS,
    MAX_FILES,
    MAX_REVIEW_INPUT_CHARS,
    REVIEW_TIMEOUT_SECONDS,
    build_inline_review_comments,
    build_review_prompt,
    build_github_review_payload,
    call_capgemini,
    filter_changed_files,
    load_policy,
    validate_result,
    validate_finding_lines,
)
from reviewer.finding_lifecycle import append_lifecycle_summary, build_finding_records
from reviewer.providers.github_mcp import GitHubMCPProvider
from reviewer.providers.github_mcp_lifecycle import GitHubMCPFindingLifecycle


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def github_token_kind(token: str) -> str:
    if token.startswith("github_pat_"):
        return "fine-grained PAT"
    if token.startswith("ghp_"):
        return "classic PAT"
    if token.startswith("ghs_"):
        return "GitHub App or Actions token"
    return "unrecognized token format"


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


def build_metadata_only_advisory(files: list[dict]) -> str:
    skipped_files = "\n".join(
        f"- {str(item['filename']).replace('`', '\\`').replace(chr(10), ' ')} - {item['review_skip_reason']}"
        for item in files
    )
    return (
        "# AI Code Review\n\n"
        "No reviewable code changes detected.\n\n"
        "This Pull Request contains file metadata changes such as pure renames, but no changed source-code hunks "
        "were available for AI analysis.\n\n"
        "Files skipped:\n\n"
        f"{skipped_files}\n\n"
        "Human approval remains required."
    )


def build_no_change_advisory() -> str:
    return (
        "# AI Code Review\n\n"
        "## No reviewable code changes detected\n\n"
        "The current Pull Request revision contains no effective code changes to analyze.\n\n"
        "The previously introduced change may have been fixed or reverted, leaving no current diff requiring AI "
        "analysis.\n\n"
        "No AI model call was required for this revision.\n\n"
        "Human approval remains required."
    )


def post_review_with_lifecycle(
    github_provider: GitHubMCPProvider,
    lifecycle_provider: GitHubMCPFindingLifecycle,
    owner: str,
    repo: str,
    pr_number: int,
    head_sha: str | None,
    review_payload: dict,
    review_result=None,
    changed_line_map: dict | None = None,
    reviewed_files: list[str] | None = None,
    resolve_unreviewed: bool = False,
) -> dict:
    if review_result is not None:
        print(f"[FINDINGS] stage=before_lifecycle_identity count={len(review_result.findings)}")
        for finding in review_result.findings:
            print(
                f"[FINDINGS] stage=before_lifecycle_identity title={finding.title!r} "
                f"category={finding.category!r} line={finding.line!r}"
            )
    lifecycle_findings = build_finding_records(
        review_result,
        changed_line_map or {},
        os.getenv("TARGET_REPO_PATH"),
    ) if review_result is not None else []
    print(
        f"[FINDINGS] stage=after_lifecycle_identity count={len(lifecycle_findings)} "
        f"unique_ids={len({record['finding_id'] for record in lifecycle_findings})}"
    )
    comparison = asyncio.run(
        lifecycle_provider.reconcile(
            owner=owner,
            repo=repo,
            pull_number=pr_number,
            head_sha=head_sha or "",
            current_findings=lifecycle_findings,
            reviewed_files=reviewed_files or [],
            resolve_unreviewed=resolve_unreviewed,
        )
    )

    new_finding_ids = {item["finding_id"] for item in comparison["new_findings"]}
    if review_result is not None:
        seen_new_ids: set[str] = set()
        new_findings = []
        comment_finding_ids = []
        for finding, record in zip(review_result.findings, lifecycle_findings):
            finding_id = record["finding_id"]
            if finding_id in new_finding_ids and finding_id not in seen_new_ids:
                new_findings.append(finding)
                comment_finding_ids.append(finding_id)
                seen_new_ids.add(finding_id)
            else:
                reason = (
                    "duplicate finding_id already selected for inline publication"
                    if finding_id in seen_new_ids
                    else "finding is existing/reopened, not new; inline duplicate suppressed"
                )
                print(
                    f"[FINDINGS] inline_publication_suppressed title={finding.title!r} "
                    f"category={finding.category!r} reason={reason}"
                )
        print(
            f"[FINDINGS] stage=after_lifecycle_inline_filter "
            f"summary_findings={len(review_result.findings)} "
            f"new_inline_candidates={len(new_findings)}"
        )
        new_result = review_result.model_copy(update={"findings": new_findings})
        review_payload["comments"] = build_inline_review_comments(
            new_result,
            changed_line_map or {},
            comment_finding_ids,
        )
    else:
        review_payload["comments"] = []

    review_payload["body"] = append_lifecycle_summary(review_payload.get("body", ""), comparison)
    asyncio.run(
        github_provider.post_review(
            owner=owner,
            repo=repo,
            pull_number=pr_number,
            review_payload=review_payload,
        )
    )
    asyncio.run(
        lifecycle_provider.finalize(
            owner=owner,
            repo=repo,
            pull_number=pr_number,
            comparison=comparison,
        )
    )
    return comparison


def main() -> None:
    token = os.getenv("GITHUB_TOKEN")
    print("[AUTH DEBUG]")
    print(f"token_length={len(token) if token else 0}")
    print(f"token_kind={github_token_kind(token) if token else 'missing'}")
    if not token:
        raise RuntimeError("Missing GITHUB_TOKEN. Ensure the workflow exposes the GitHub token to the runner.")

    event = load_event()
    pr_context = ensure_pr_context(event)
    repo_owner, repo_name = pr_context["repo_slug"].split("/", 1)
    event_name = os.getenv("GITHUB_EVENT_NAME", "unknown")

    head_repo = (
        event.get("pull_request", {})
        .get("head", {})
        .get("repo", {})
        .get("full_name")
    )

    base_repo = (
        event.get("pull_request", {})
        .get("base", {})
        .get("repo", {})
        .get("full_name")
    )

    is_fork = head_repo != base_repo

    print("[AUTH DEBUG]")
    print(f"event={event_name}")
    print(f"repository={pr_context['repo_slug']}")
    print(f"base_repo={base_repo}")
    print(f"head_repo={head_repo}")
    print(f"is_fork={is_fork}")

    token = os.getenv("GITHUB_TOKEN")
    print(f"token_present={bool(token)}")
    print(f"token_length={len(token) if token else 0}")
    print(f"token_kind={github_token_kind(token) if token else 'missing'}")

    print("[CONTEXT] Repository:")
    print(pr_context["repo_slug"])
    print("[CONTEXT] Pull Request:")
    print(f"#{pr_context['pr_number']}")
    github_provider = GitHubMCPProvider(token=token)
    lifecycle_provider = GitHubMCPFindingLifecycle(github_provider)
    files_payload = asyncio.run(
        github_provider.get_pull_request_files(
            owner=repo_owner,
            repo=repo_name,
            pull_number=pr_context["pr_number"],
        )
    )

    filtered = filter_changed_files(
        files_payload,
        max_files=int(os.getenv("MAX_FILES", str(MAX_FILES))),
        max_file_diff_chars=int(os.getenv("MAX_FILE_DIFF_CHARS", str(MAX_FILE_DIFF_CHARS))),
        max_review_input_chars=int(os.getenv("MAX_REVIEW_INPUT_CHARS", str(MAX_REVIEW_INPUT_CHARS))),
    )
    max_files = int(os.getenv("MAX_FILES", str(MAX_FILES)))
    max_file_diff_chars = int(os.getenv("MAX_FILE_DIFF_CHARS", str(MAX_FILE_DIFF_CHARS)))
    max_review_input_chars = int(os.getenv("MAX_REVIEW_INPUT_CHARS", str(MAX_REVIEW_INPUT_CHARS)))
    accounted_files = len(filtered["files_reviewed"]) + len(filtered["files_skipped"])
    not_considered_count = max(0, len(files_payload) - accounted_files)
    print(
        f"[DIFF] source_files={len(files_payload)} selected={len(filtered['files_reviewed'])} "
        f"explicitly_skipped={len(filtered['files_skipped'])} "
        f"not_considered={not_considered_count} max_files={max_files} "
        f"max_file_diff_chars={max_file_diff_chars} "
        f"max_review_input_chars={max_review_input_chars} "
        f"bounded_diff_chars={len(filtered['diff_text'])} "
        f"truncated={'[truncated]' in filtered['diff_text']}"
    )
    if not_considered_count:
        print(
            f"[DIFF] {not_considered_count} source file(s) were not considered "
            "because the selected-file limit was reached"
        )
    print("[FILTER] Reviewable files:")
    print(len(filtered["files_reviewed"]))
    print("[DIFF] Bounded diff generated")
    print("[DIFF] Added-line mapping generated")

    if not files_payload:
        print("[AI] Generative Engine call skipped")
        print("[REVIEW] Preparing no-change advisory")
        post_review_with_lifecycle(
            github_provider,
            lifecycle_provider,
            repo_owner,
            repo_name,
            pr_context["pr_number"],
            pr_context["head_sha"],
            {
                "body": build_no_change_advisory(),
                "commit_id": pr_context["head_sha"],
                "comments": [],
            },
            resolve_unreviewed=True,
        )
        print("AI PR REVIEW POSTED SUCCESSFULLY")
        return

    if not filtered["files_reviewed"] or not filtered["diff_text"].strip():
        metadata_only_files = [
            item for item in files_payload
            if not (item.get("patch") or "").strip() and item.get("review_skip_reason")
        ]
        if files_payload and len(metadata_only_files) == len(files_payload):
            advisory_payload = {
                "body": build_metadata_only_advisory(metadata_only_files),
                "commit_id": pr_context["head_sha"],
                "comments": [],
            }
            post_review_with_lifecycle(
                github_provider,
                lifecycle_provider,
                repo_owner,
                repo_name,
                pr_context["pr_number"],
                pr_context["head_sha"],
                advisory_payload,
            )
            print("AI PR REVIEW POSTED SUCCESSFULLY")
            return
        raise RuntimeError("No meaningful changed files were available for review after filtering.")

    api_key = os.getenv("GEP_API_KEY")
    if not api_key:
        raise RuntimeError("Missing GEP_API_KEY. Configure the secret in GitHub before running reviews.")

    policy_text = load_policy()
    prompt = build_review_prompt(
        repo_slug=pr_context["repo_slug"],
        pr_title=pr_context["title"],
        pr_body=pr_context["body"],
        diff_text=filtered["diff_text"],
        changed_line_map=filtered["changed_line_map"],
    )

    model_name = os.getenv("REVIEW_MODEL", "openai.gpt-5")
    base_url = os.getenv("OPENAI_BASE_URL", "https://openai.generative.engine.capgemini.com/v1")
    timeout_seconds = int(os.getenv("REVIEW_TIMEOUT_SECONDS", str(REVIEW_TIMEOUT_SECONDS)))

    print(
        f"[AI] GEP request settings model={model_name!r} "
        f"timeout_seconds={timeout_seconds} "
        f"prompt_chars={len(prompt)} prompt_bytes={len(prompt.encode('utf-8'))} "
        f"policy_chars={len(policy_text)} "
        f"diff_chars={len(filtered['diff_text'])} "
        f"diff_bytes={len(filtered['diff_text'].encode('utf-8'))}"
    )
    print("[AI] Calling Capgemini Generative Engine")
    raw_response = call_capgemini(
        policy_text=policy_text,
        user_prompt=prompt,
        api_key=api_key,
        model=model_name,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
    )
    print("[AI] Structured response received")
    raw_findings = raw_response.get("findings") if isinstance(raw_response, dict) else None
    raw_finding_count = len(raw_findings) if isinstance(raw_findings, list) else 0
    print(f"[FINDINGS] stage=ai_response_extracted count={raw_finding_count}")
    if isinstance(raw_findings, list):
        for index, finding in enumerate(raw_findings):
            if isinstance(finding, dict):
                print(
                    f"[FINDINGS] stage=ai_response_extracted index={index} "
                    f"title={finding.get('title')!r} category={finding.get('category')!r}"
                )
            else:
                print(
                    f"[FINDINGS] stage=ai_response_extracted index={index} "
                    f"title='<unavailable>' category='<unavailable>' "
                    f"reason=expected object, got {type(finding).__name__}"
                )

    validated_result = validate_result(raw_response)
    print("[PYDANTIC] Response validated")
    review_result = validate_finding_lines(validated_result, filtered["changed_line_map"])
    print("[LOCATION] Finding locations validated")
    review_payload = build_github_review_payload(
        review_result,
        filtered["changed_line_map"],
        pr_context["head_sha"],
    )
    print("[FORMAT] Enterprise summary generated")
    complete_diff = "[truncated]" not in filtered["diff_text"]
    complete_file_selection = len(filtered["files_reviewed"]) + len(filtered["files_skipped"]) == len(files_payload)
    lifecycle_reviewed_files = filtered["files_reviewed"] if complete_diff and complete_file_selection else []
    post_review_with_lifecycle(
        github_provider,
        lifecycle_provider,
        repo_owner,
        repo_name,
        pr_context["pr_number"],
        pr_context["head_sha"],
        review_payload,
        review_result=review_result,
        changed_line_map=filtered["changed_line_map"],
        reviewed_files=lifecycle_reviewed_files,
    )

    print("AI PR REVIEW POSTED SUCCESSFULLY")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover - runtime guard
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
