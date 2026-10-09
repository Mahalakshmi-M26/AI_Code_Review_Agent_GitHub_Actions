# AI Code Review Agent

An enterprise-oriented proof of concept for automated first-pass Pull Request review. GitHub Actions runs a centrally maintained reviewer, the official GitHub MCP Server handles GitHub Pull Request reads and review publishing, and Capgemini Generative Engine provides model inference. Human approval remains required.

## Problem and objective

Code reviews take engineering time, and reviewers can spend effort on repetitive checks instead of higher-value concerns such as security, architecture, logical correctness, performance, testing, and maintainability. The objective is to automate a policy-guided first pass while leaving merge and approval decisions with people.

## Final architecture

```text
Consumer Pull Request
  -> opened / reopened / synchronize
  -> consumer caller workflow
  -> central reusable GitHub Actions workflow
  -> AI Code Review Agent
       -> official GitHub MCP Server: pull_request_read(get_files)
       -> pull_request_read(get_diff) when patches are unavailable
       -> filter files and bound diff context
       -> build deterministic added-line map
       -> enterprise policy + Capgemini Generative Engine HTTP request
       -> structured JSON + Pydantic validation
       -> deterministic file/line validation
       -> enterprise Markdown summary + eligible inline comments
       -> official GitHub MCP Server: create pending review
       -> add_comment_to_pending_review for validated lines
       -> submit_pending with event COMMENT
  -> GitHub Pull Request
  -> human reviewer makes the final decision
```

Responsibilities are separated: GitHub Actions is the trigger and execution environment; GitHub MCP is the GitHub integration layer; Capgemini Generative Engine is the inference layer; Pydantic checks response shape; deterministic diff mapping protects inline locations; the human reviewer retains final authority. The reviewer package contains no direct GitHub REST integration. GEP remains an HTTP integration.

## Reusable multi-repository workflow

The central repository owns `.github/workflows/ai-code-review-reusable.yml`, Python reviewer code, `reviewer/enterprise_review.md`, models, and tests. A consumer repository adds a small caller workflow and invokes the reusable workflow. The currently validated consumers include `ai-code-review-demo` and Spring-petclinic (`pets-workshop` is also configured as a consumer). The target repository and PR context are taken from the triggering event or supported workflow inputs; repository names are not hardcoded in the review logic.

Example consumer workflow:

```yaml
name: AI PR Review
on:
  pull_request:
    types: [opened, reopened, synchronize]
jobs:
  review:
    uses: <central-org>/<central-repo>/.github/workflows/ai-code-review-reusable.yml@main
    secrets:
      GEP_API_KEY: ${{ secrets.GEP_API_KEY }}
```

The reusable job checks out the central reviewer and the target PR, uses Python 3.12, installs `requirements.txt`, starts the official GitHub MCP Server using Docker/stdio, and runs `python -m reviewer.main`.

## GitHub MCP integration

The Python MCP SDK connects over stdio to `ghcr.io/github/github-mcp-server` (live-validated v2.0.1). Actions provides its short-lived `GITHUB_TOKEN` to the MCP server as `GITHUB_PERSONAL_ACCESS_TOKEN`. The application uses `pull_request_read` for changed-file metadata and patches, and `pull_request_review_write` plus `add_comment_to_pending_review` to publish a Pull Request review. It does not call GitHub REST directly.

### MCP READ

The reviewer requests `pull_request_read(method="get_files")`. If the result contains patches, those are used. If file metadata is returned without usable patches, the reviewer requests `pull_request_read(method="get_diff")`, splits the unified diff, and joins sections to authoritative `get_files` metadata. Metadata-only and rename-only sections are retained without fabricated patches and skipped by the review filter. If `get_files` returns zero files, the read completes with an empty list and `get_diff` is skipped.

### MCP WRITE

The application creates a pending review with `pull_request_review_write(method="create")`, omitting the event and supplying the current PR head `commitID` when available. It sends only existing validated inline comments to `add_comment_to_pending_review` with `side="RIGHT"` and `subjectType="LINE"`. It then submits one review with the enterprise summary and `event="COMMENT"`. If publishing fails after pending-review creation, it attempts `delete_pending` cleanup and reports cleanup failure without hiding the original error. The agent never approves or merges a PR.

## Review and inline-comment safety

`filter_changed_files()` ignores configured generated/build paths, binary extensions, lock files, and files without patches. `MAX_FILES`, `MAX_FILE_DIFF_CHARS`, and `MAX_REVIEW_INPUT_CHARS` bound the model context. Defaults are 40 files, 12,000 characters per patch, and 100,000 characters total. Large patches or total input may be truncated.

`parse_unified_diff()` maps added lines to exact new-file (`RIGHT`) line numbers. The prompt may ask the model to propose a file and line, but the application independently validates both against the deterministic map generated from the bounded diff actually sent to the model. Invalid, absent, truncated-away, or deletion-only locations become `null` and remain summary-only. Only validated added-line findings become inline comments. One Pull Request review can contain both the enterprise summary and multiple inline comments.

## AI and policy

The default model is `openai.gpt-5` through the configured Capgemini OpenAI-compatible chat completions endpoint. Only PR metadata, bounded changed-file diff text, and the enterprise policy are sent; the whole repository is not sent. The policy covers security, architecture, logical correctness, reliability/error handling, performance, testing, maintainability, coding standards, and dependencies.

The prompt marks PR title, description, filenames, comments, and code as untrusted data and makes the enterprise policy authoritative. It prohibits invented findings and line numbers and requests a JSON object. This is a prompt-level mitigation, not a guarantee against prompt injection. Pydantic rejects malformed/invalid result structures before publishing; it does not establish that a finding is factually correct. Human review remains essential.

## Zero-change and metadata-only PRs

- **Rename-only or metadata-only files:** a diff section with no content hunk has no reviewable patch. It is retained with a skip reason and no inline line is invented.
- **Mixed PR:** files with real hunks continue through normal review; metadata-only files are skipped.
- **Zero effective changes:** an empty successful `get_files` result means there is no current diff. The agent skips `get_diff` and GEP, then posts a concise summary-only MCP COMMENT advisory tied to the current head SHA. No findings or inline comments are fabricated.
- **Fix pushed to an existing PR:** `synchronize` reruns the workflow against the current revision. A reverted/fixed change is not re-reported if it is no longer in the current diff. Automatic detection/resolution of older review threads is not implemented; it is P3 roadmap work.

## Configuration and permissions

Required secret: `GEP_API_KEY` for normal AI reviews. The zero-change code path skips GEP and does not require the key at runtime. GitHub Actions supplies `GITHUB_TOKEN` to MCP. The reusable workflow requests `contents: read` and `pull-requests: write`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `OPENAI_BASE_URL` | `https://openai.generative.engine.capgemini.com/v1` | GEP-compatible endpoint |
| `REVIEW_MODEL` | `openai.gpt-5` | Model identifier |
| `MAX_FILES` | `40` | Maximum selected files |
| `MAX_FILE_DIFF_CHARS` | `12000` | Per-file patch cap |
| `MAX_REVIEW_INPUT_CHARS` | `100000` | Total diff context cap |
| `REVIEW_TIMEOUT_SECONDS` | `60` | Model request timeout |

## Errors and limitations

MCP connection/tool/parse failures stop the workflow clearly; there is no REST fallback. Malformed model JSON, schema failures, GEP HTTP errors, and timeouts stop normal AI publication. Pending MCP reviews are cleaned up best-effort when later publishing stages fail. Empty and metadata-only diffs are handled without GEP.

The generic AI review path has been validated using Python, Java, and JavaScript PR changes. The agent does not automatically run dedicated compilers, linters, static-analysis tools, or target-repository tests. It does not deduplicate findings, track finding identity across commits, or automatically resolve old review threads. Input bounds can truncate context. This is an enterprise-oriented POC, not a production-certified system.

## P1 / P2 / P3 status

- **P1 — COMPLETE:** GitHub Actions trigger/runtime, reusable multi-repository workflow, PR lifecycle events, external configuration, and MCP Pull Request reads.
- **P2 — COMPLETE:** enterprise summary, deterministic added-line mapping, validated inline comments, summary-only findings, pending MCP review, MCP inline comments, MCP COMMENT submission, and human-in-the-loop behavior.
- **P3 — ROADMAP:** stable finding identity, duplicate suppression, previous/current finding comparison, resolved-finding detection, automatic thread resolution, and review lifecycle management.

## Security

Secrets belong in GitHub Actions secrets, not source or logs. `GITHUB_TOKEN` is workflow-provided and scoped by the job permissions. PR content is untrusted; the model receives only bounded diffs and the policy, and model locations are independently checked. Review output is advisory; human approval remains required. Before production use, assess GitHub App authentication, retries/backoff, rate limits, auditing, telemetry, token/cost controls, immutable workflow pinning, and organizational policy requirements.

## Demo

Use the final eight-slide deck at [`docs/ai-code-review-agent-final-demo.pptx`](docs/ai-code-review-agent-final-demo.pptx) and the mentor preparation guide at [`docs/demo-questions-and-answers.md`](docs/demo-questions-and-answers.md). Suggested live sequence: show the PR; push a controlled commit; show Actions start; show MCP retrieval; show GEP, Pydantic, and line validation; show MCP review publishing; refresh the PR and inspect summary/inline comments; fix one issue and push again to demonstrate `synchronize`; explain P3 thread lifecycle as future work.

Local tests:

```bash
pytest
```
