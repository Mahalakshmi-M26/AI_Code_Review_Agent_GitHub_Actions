# AI Code Review Agent for GitHub Actions

This project implements a minimal GitHub Actions-first AI code review agent for pull requests.

## Problem solved

The goal is to review only the relevant changed files in a pull request, apply an enterprise review policy, and post one overall PR-level AI summary back to GitHub.

## Why this version does not use FastAPI or ngrok

This is intentionally a GitHub Actions-driven solution. The workflow is triggered by the GitHub pull_request event, so there is no continuously running webhook listener, no local FastAPI application, no Uvicorn server, and no ngrok tunnel. The GitHub event itself is the trigger.

## Reusable architecture

The central repository contains:

- the reusable workflow
- the Python reviewer code
- the enterprise review policy
- the Pydantic models
- the tests

Consumer repositories only need a tiny caller workflow that invokes the central reusable workflow.

## Folder structure

- `.github/workflows/ai-code-review-reusable.yml`
- `reviewer/__init__.py`
- `reviewer/main.py`
- `reviewer/models.py`
- `reviewer/review.py`
- `reviewer/enterprise_review.md`
- `tests/test_models.py`
- `tests/test_review.py`
- `requirements.txt`
- `.env.example`
- `.gitignore`

## Required secret

Set the following GitHub repository or organization secret:

- `GEP_API_KEY`

This is the bearer token used for the Capgemini OpenAI-compatible endpoint.

## Required GitHub token

The workflow uses the built-in GitHub token for API calls to create a PR review. It is passed to the runner as `GITHUB_TOKEN`.

## Permissions

The reusable workflow grants only:

- `contents: read`
- `pull-requests: write`

No extra write permissions are requested.

## Optional variables

These optional GitHub Actions variables can be set under repository or organization variables:

- `REVIEW_MODEL` default: `gpt-4o-mini`
- `OPENAI_BASE_URL` default: `https://openai.generative.engine.capgemini.com/v1`
- `MAX_FILES` default: `40`
- `MAX_FILE_DIFF_CHARS` default: `12000`
- `MAX_REVIEW_INPUT_CHARS` default: `100000`
- `REVIEW_TIMEOUT_SECONDS` default: `60`

## Example caller workflow for Repo A

Place this in a consuming repository at `.github/workflows/ai-code-review.yml`:

```yaml
name: Caller AI PR Review

on:
  pull_request:
    types: [opened, reopened, synchronize]

jobs:
  review:
    uses: <central-org>/<central-repo>/.github/workflows/ai-code-review-reusable.yml@main
    secrets:
      GEP_API_KEY: ${{ secrets.GEP_API_KEY }}
      GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

The same pattern can be reused in Repo B, Repo C, and other repositories without copying the reviewer code.

## Behavior

The workflow triggers on:

- `opened`
- `reopened`
- `synchronize`

The reusable workflow checks out the central reviewer repository for the Python code and the caller repository for the PR under review. The Python reviewer reads and bounds the PR diff from GitHub, maps added lines from unified-diff hunk metadata, applies the enterprise policy, calls the Capgemini model, validates the resulting JSON with Pydantic, and submits a GitHub PR review containing the summary and any eligible inline comments.

## Local test command

```bash
pytest
```

## Expected review output

The review always includes the PR-level summary. Findings whose file and line match a deterministic changed-line entry also receive an inline comment in the same GitHub review submission. Invalid, absent, or deletion-only locations remain in the summary without an inline comment.

## Current limitations

This implementation does not yet include:

- resolved threads
- duplicate finding tracking
- advanced production monitoring
- inline placement for deleted-side lines

## Next step after P2

P3 can add duplicate finding handling and resolved-thread lifecycle management. The reusable workflow architecture and `opened`, `reopened`, and `synchronize` triggers remain unchanged.
