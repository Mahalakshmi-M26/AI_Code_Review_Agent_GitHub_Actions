# AI Code Review Agent: Technical Document

**Status:** P1 and P2 are complete and live-validated. P3 remains roadmap. This is an enterprise-oriented proof of concept, not a production-certified system.

## 1. Executive Summary

The AI Code Review Agent is a GitHub Actions-based first-pass Pull Request reviewer. A consumer repository calls a central reusable workflow. The reviewer gets changed-file metadata and patches through the official GitHub MCP Server, bounds review input, applies a central enterprise policy, calls Capgemini Generative Engine (GEP) over HTTP, validates structured output with Pydantic, independently checks finding locations against added lines, and publishes one advisory Pull Request review with a Markdown summary and eligible inline comments through GitHub MCP. Human approval remains required.

## 2. Problem Statement

Reviewers spend engineering time on recurring checks, and review depth may vary across pull requests. The POC adds a repeatable, policy-guided first pass without transferring merge authority to AI.

## 3. Why GitHub Actions

The PR event already exists in GitHub. Actions provides event-triggered execution, a managed runner, workflow logs, and permissions tied to the job. It removes the need for the current flow to maintain an always-running service or public webhook tunnel.

## 4. Migration from FastAPI/Webhook Architecture

FastAPI, Uvicorn, ngrok, and a continuously running webhook listener are not part of the current architecture. They are historical migration context only. GitHub Actions now starts the workflow from a PR lifecycle event; the official GitHub MCP Server is the GitHub integration layer.

## 5. Final Architecture

```text
GitHub PR -> consumer Actions caller -> central reusable workflow -> reviewer
  -> GitHub MCP READ -> bounded diff -> enterprise policy + GEP HTTP
  -> Pydantic -> deterministic location validation -> review summary/comments
  -> GitHub MCP WRITE -> GitHub PR -> human decision
```

GitHub Actions is the trigger/runtime. GitHub MCP is the GitHub PR integration. GEP is model inference. Pydantic validates response shape. Deterministic diff mapping protects inline locations. A human reviewer makes the final merge decision.

## 6. Reusable Multi-Repository Design

The central repository contains the reusable workflow, reviewer package, policy, models, and tests. Consumer repositories contain a caller workflow that references the central reusable workflow and provides required secret/access configuration. `ai-code-review-demo` and Spring-petclinic have been live-validated; `pets-workshop` is also configured as a consumer. Repository and PR context come from the triggering event or supported workflow inputs, not review-code constants.

## 7. Pull Request Trigger Lifecycle

The caller workflow listens to `opened`, `reopened`, and `synchronize`. Opened starts a review for a new PR, reopened runs when a closed PR is reopened, and synchronize starts a new run when commits update the PR branch. Each run evaluates the current PR revision. A fixed issue no longer present in that revision's diff is not raised again merely because it appeared in an earlier review. Old review-thread resolution is separate P3 work.

## 8. GitHub MCP Server Integration

`GitHubMCPProvider` uses the Python MCP SDK and stdio transport to run `ghcr.io/github/github-mcp-server`; live validation used server v2.0.1. The Actions `GITHUB_TOKEN` is passed to the server as `GITHUB_PERSONAL_ACCESS_TOKEN`. The reviewer package has no direct GitHub REST calls or REST fallback. GEP remains HTTP.

## 9. MCP READ Flow

The provider opens a session, initializes it, and calls `pull_request_read` with `method="get_files"`, repository owner/name, PR number, and pagination parameters. It parses the MCP SDK result and preserves authoritative filenames/status metadata. If patches are present, it returns those records. If a successful `get_files` result is empty, it returns an empty list and skips `get_diff`.

## 10. get_files / get_diff Fallback

Some official server responses contain file metadata without patch text. In that case the provider calls `pull_request_read(method="get_diff")`, extracts unified diff text from the typed tool result, splits sections, and joins them to metadata by normalized filename. Count and filename checks fail clearly on inconsistent responses. Sections with no `@@` hunk are preserved with an empty patch and skip reason rather than treated as source changes. No patch or line is fabricated.

## 11. Diff Filtering and Context Bounding

`filter_changed_files()` is authoritative for review scope. It skips configured generated/build prefixes, listed lock files, binary extensions, and patchless entries; then applies limits. Defaults are 40 files, 12,000 characters per file patch, and 100,000 characters of total review diff. Oversized input is truncated with a marker.

## 12. Deterministic Added-Line Mapping

`parse_unified_diff()` reads unified-diff hunk headers and walks old/new line counters. Added lines are recorded against right/new-file line numbers. Deletions advance only the old-side counter; they do not become right-side inline locations. The map is generated from the bounded prompt diff so it cannot authorize lines omitted from model context.

## 13. Enterprise Review Policy

`reviewer/enterprise_review.md` is loaded from the central repository. It guides conservative, evidence-based review of security, architecture, logical correctness, reliability/error handling, performance, testing, maintainability, coding standards, and dependencies. PR metadata, filenames, comments, and code are treated as untrusted content. Policy text is authoritative over instructions embedded in PR data.

## 14. Capgemini Generative Engine

For non-empty reviewable diffs, the reviewer sends policy and bounded PR review context to the OpenAI-compatible GEP `/chat/completions` endpoint over HTTP. Default model: `openai.gpt-5`; default timeout: 60 seconds. The agent sends the selected bounded PR diff and relevant metadata, not the entire repository. GEP is HTTP because it is the inference service; GitHub MCP is the tool interface for GitHub operations.

## 15. Structured JSON Response

The request asks for a JSON object at temperature zero. The response parser extracts the model message, parses JSON, and rejects malformed or empty output. The expected structure includes decision, risk level, findings, summary, files reviewed/skipped, and categories reviewed.

## 16. Pydantic Validation

`ReviewResult` and finding models validate field structure and types; unknown fields are forbidden and severity is enumerated. Invalid JSON or schema validation fails the run before publication. Pydantic does not prove that a finding is true or supported; it validates structure.

## 17. Inline Location Validation

A model may propose a file and line. The application independently checks that the file and line match the deterministic added-line map. Invalid, missing, truncated-away, and deletion-only locations become `null`. Only locations that pass are eligible for a right-side inline comment. The model does not select its own publishing location.

## 18. Review Summary Generation

The existing formatter produces an enterprise Markdown summary with overall assessment, severity counts, findings, recommendations, suggested fixes, and merge guidance/human-review language. Findings without valid locations remain in the summary. Summary and inline comments are delivered together in one review.

## 19. MCP Review Publishing

`GitHubMCPProvider.post_review()` owns the complete write choreography. It maps the existing application review payload into the official MCP review tools. The main application does not construct raw MCP tool sequences.

## 20. Pending Review Flow

The provider calls `pull_request_review_write(method="create")` without an event, creating a pending review. If available, the current PR head SHA is passed as `commitID`. After creation, eligible inline comments are added and the pending review is submitted. A later write failure triggers best-effort `delete_pending` cleanup; cleanup errors are reported without hiding the original error.

## 21. Inline Comment Flow

Only comments already produced by `build_inline_review_comments()` are sent. Each uses the payload path, line, and body with `side="RIGHT"` and `subjectType="LINE"`. No new location is calculated in the MCP provider. A finding with `line=null` creates no inline comment.

## 22. COMMENT Review Submission

`pull_request_review_write(method="submit_pending")` receives the enterprise summary and `event="COMMENT"`. The agent does not use APPROVE or REQUEST_CHANGES, does not use a generic issue comment as a substitute, and does not merge PRs. Human approval remains required.

## 23. Multi-Language Validation

The generic AI review path has been validated using Python, Java, and JavaScript PR changes. Diffs are handled as text through the shared path. The workflow does not run dedicated compilers, linters, static-analysis tools, or target-repository test suites for those languages.

## 24. Multi-Repository Validation

The reusable central workflow has been exercised by `ai-code-review-demo` and Spring-petclinic, and `pets-workshop` is configured as a consumer. Live runs validate workflow wiring and service integration for those runs; they do not certify every repository or guarantee model accuracy.

## 25. Rename/Metadata-Only Handling

A pure rename with similarity metadata and no hunk is not a content patch. The provider preserves metadata and labels it `rename-only / no content changes`; other hunkless sections receive a metadata-only skip reason. The normal filter skips these empty patches. In a mixed PR, actual hunks continue through review and metadata-only entries do not enter the model context.

## 26. Zero-Effective-Change Handling

If `get_files` succeeds and returns an empty list, the provider returns an empty result and does not request `get_diff`. The main program skips GEP and posts a short summary-only advisory through MCP with the current head SHA, no findings, and no inline comments. This also handles a developer fix/revert that leaves no current diff. It does not resolve previous review threads.

## 27. Developer Fix / Synchronize Flow

A push to the PR branch triggers `synchronize`, and the reviewer reads the new current diff. A removed/fixed issue no longer in that diff is not re-raised by this run. Persisting finding identities and resolving earlier discussion threads are P3 roadmap features, not implemented behavior.

## 28. Failure Handling

MCP initialization, tool, parse, and normalization errors fail clearly; there is no REST fallback. GEP timeouts, HTTP errors, malformed JSON, and Pydantic failures stop normal review publication. MCP pending reviews are cleaned up best-effort if inline addition or submission fails. Empty and metadata-only diffs are valid paths rather than tool failures.

## 29. Configuration

The workflow uses Python 3.12 and installs `requirements.txt`. `GITHUB_TOKEN` is provided by Actions for MCP authentication. `GEP_API_KEY` is required for normal AI analysis and stored as an Actions secret. The zero-change runtime path skips the GEP call and does not read the key. Variables include `OPENAI_BASE_URL`, `REVIEW_MODEL`, `MAX_FILES`, `MAX_FILE_DIFF_CHARS`, `MAX_REVIEW_INPUT_CHARS`, and `REVIEW_TIMEOUT_SECONDS`.

## 30. Context / Cost / Large PR Controls

File count and character limits bound review context and indirectly limit model input. Generated paths, binary files, lock files, and patchless metadata changes are skipped. Truncation can omit relevant evidence; the reviewer must not claim full-repository or unlimited-PR analysis. Token/cost telemetry is not currently implemented.

## 31. Security

Secrets are configured in GitHub Actions secrets and are not printed. The built-in token is scoped by the workflow job (`contents: read`, `pull-requests: write`). PR content is untrusted; input is bounded, prompt policy marks it untrusted, and output locations are checked independently. Human approval remains required. This is an enterprise-oriented POC, not production-certified.

## 32. P1 Status

**P1 — COMPLETE.** GitHub Actions runtime, reusable workflow, multiple repository usage, opened/reopened/synchronize lifecycle, external configuration, and MCP Pull Request reads are implemented and live-validated.

## 33. P2 Status

**P2 — COMPLETE.** Enterprise summaries, deterministic added-line mapping, validated inline comments, summary-only findings, pending MCP reviews, MCP inline comments, MCP submit with COMMENT, and human-in-the-loop behavior are implemented and live-validated.

## 34. P3 Roadmap

**P3 — ROADMAP / NEXT ENHANCEMENT.** Stable finding fingerprints, duplicate suppression, comparison across commits, resolved-finding detection, automatic GitHub review-thread resolution, and review lifecycle management are not implemented.

## 35. Current Limitations

No duplicate suppression, persistent finding state, automatic old-thread resolution, language-specific toolchain execution, production metrics, cost telemetry, or production certification is provided. Findings and model output require human judgment. File and character caps can truncate context.

## 36. Final Demo Flow

Show the PR; push a controlled commit; show Actions start; show MCP retrieving changes; show GEP analysis; show Pydantic and deterministic location validation; show MCP pending review and inline comment publishing; refresh the PR and inspect the summary/comment; fix one finding and push another commit; explain `synchronize` re-review and mention P3 lifecycle work.

## 37. Conclusion

P1/P2 provide a live-validated, reusable GitHub Actions review path with MCP-based GitHub integration, GEP inference, schema validation, deterministic inline safety, and human-controlled outcomes. P3 remains a roadmap for managing findings across revisions and review-thread lifecycle.
