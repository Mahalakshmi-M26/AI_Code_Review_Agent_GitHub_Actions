# AI Code Review Agent: Demo Questions and Answers

A mentor/evaluator preparation guide for the live P1/P2 demonstration. P1 and P2 are complete; P3 is roadmap only. This is an enterprise-oriented POC, not a production-certified system. Human approval remains required.

## A. Business and Purpose

**What problem does this solve?**
It gives pull requests a consistent, policy-guided first-pass review and returns evidence-based findings in GitHub. It is intended to help reviewers focus attention, not replace their judgment.

**Why automate code review?**
Automation can apply the same first-pass checks on each configured PR event and return feedback in the PR workflow. It does not guarantee defect detection or replace code ownership.

**Does AI replace human reviewers?**
No. The agent posts an advisory COMMENT review and cannot approve or merge. Human approval remains required.

**What is the business value?**
The POC centralizes policy and review implementation and makes an AI-assisted first pass available across repositories. No measured productivity, defect-rate, or cost improvement is claimed.

**Why is this an agent rather than only an API?**
It is an event-driven workflow that gathers PR context, filters and bounds diffs, applies policy, calls a model, validates output and locations, and publishes a review. It orchestrates a task; the model does not independently control GitHub tools.

**What areas does the review assess?**
The enterprise policy guides checks for security, architecture, logical correctness, reliability/error handling, performance, testing, maintainability, coding standards, and dependencies.

## B. Architecture

**Explain the end-to-end architecture.**
A consumer PR event starts its caller workflow, which invokes the central reusable workflow. The Python reviewer reads PR changes through GitHub MCP, sends bounded review context and policy to GEP over HTTP, validates the result, then publishes a summary and eligible inline comments through GitHub MCP.

**Why GitHub Actions?**
The PR lifecycle already exists in GitHub. Actions supplies an event-driven runner and logs without a continuously running webhook service.

**Why remove FastAPI/ngrok?**
They add a separately operated listener and tunnel to a flow that can run from native PR events. They are historical migration context, not part of the current system.

**What does the reusable workflow provide?**
It centralizes checkout, Python setup, dependency installation, permissions, MCP server execution, and reviewer invocation so consumer repositories do not duplicate the agent.

**How does multi-repository support work?**
Each consumer has a small caller workflow referring to the same central reusable workflow. Target repository and PR data come from the caller event or supported inputs.

**Where does the reviewer run?**
The Python code runs on the GitHub-hosted Actions runner in the reusable workflow job.

**What runs in GitHub Actions?**
The workflow checks out the central reviewer and target PR, installs Python dependencies, runs the official MCP server in Docker/stdio, then executes the reviewer module.

**What runs remotely?**
The GitHub MCP server accesses GitHub through its official integration, and the Capgemini Generative Engine processes model requests over HTTP.

**What role does Capgemini Generative Engine play?**
It provides inference for the bounded diff and enterprise policy, returning the requested structured JSON. It does not call GitHub MCP directly.

## C. MCP

**What is MCP?**
The Model Context Protocol is a standard way for a client to discover and call tools exposed by a server. Here the Python MCP SDK talks to the official GitHub MCP Server.

**Why GitHub MCP instead of direct REST calls?**
The application uses the official MCP server as its GitHub integration abstraction for PR reads and review writes. This keeps GitHub tool invocation behind a supported MCP interface; the application has no direct GitHub REST integration.

**Where exactly is MCP used?**
MCP retrieves PR files/diffs with `pull_request_read` and publishes reviews with `pull_request_review_write` and `add_comment_to_pending_review`.

**How can you prove the code uses MCP?**
The provider creates an MCP `ClientSession` over stdio and calls the named GitHub MCP tools. A reviewer-package search contains no `api.github.com` or `github_request()` references.

**Is GPT calling MCP directly?**
No. The Python reviewer calls MCP tools. GEP/GPT receives an HTTP request containing bounded review context and returns structured content; the application performs all GitHub operations.

**Which MCP tools are used?**
`pull_request_read`, `pull_request_review_write`, and `add_comment_to_pending_review`.

**What does `pull_request_read` do?**
It retrieves PR metadata/changed-file information using methods including `get_files` and `get_diff`.

**Why `get_files` and `get_diff`?**
`get_files` supplies authoritative changed-file metadata and sometimes patches. When patches are unavailable, `get_diff` supplies a unified PR diff that is normalized per file and matched to metadata.

**What MCP tools publish reviews?**
`pull_request_review_write` creates and submits the pending review; `add_comment_to_pending_review` attaches eligible file-line comments to it.

**Why use a pending review?**
It lets the summary and multiple inline comments be assembled into one PR review before submission.

**Why use `add_comment_to_pending_review`?**
It adds a review comment to the pending review, keeping it part of the Files Changed review rather than a generic conversation comment.

**Why not `add_issue_comment`?**
A generic issue/conversation comment would not provide the current PR review plus inline-comment behavior.

**How is `commitID` used?**
The current PR head SHA is passed on pending review creation when available to associate review publication with that revision.

**Is GitHub MCP internally allowed to use GitHub APIs? Does that mean the application uses REST?**
The official server necessarily integrates with GitHub services internally. The reviewer application itself uses MCP tool calls and does not directly call GitHub REST endpoints.

**How is GitHub MCP authenticated in Actions?**
The workflow passes the built-in `GITHUB_TOKEN` to the MCP server as `GITHUB_PERSONAL_ACCESS_TOKEN`; job permissions include contents read and pull-requests write.

## D. Inline Comments

**How does the model choose an inline line?**
The model may propose a file and line from the supplied bounded diff and deterministic changed-line list. The application then independently validates the proposal; the model does not authorize its own location.

**Can GPT invent a line number?**
It can return an incorrect number, but the application nulls any location not present in the deterministic map. Such a finding stays in the summary and is not sent inline.

**Why `side=RIGHT`?**
`RIGHT` identifies the new-file side of the diff, where added lines are mapped. This matches the supported inline-review contract.

**What happens for deleted lines?**
Deleted-side lines are not eligible for inline placement. A finding without a matching added line remains summary-only.

**What happens if the model returns an invalid line?**
The file/line is checked against added lines from the bounded diff. An invalid location becomes `null`, so no inline comment is created.

**What happens if a finding has `line=null`?**
The finding remains in the enterprise summary and has no inline comment.

**What happens for rename-only files?**
A rename with no content hunk receives no fabricated patch, is skipped for AI analysis, and cannot generate an inline location.

**Why use deterministic line validation?**
It constrains inline comments to actual added lines in the exact diff given to the model, protecting against invented or stale locations.

**Can a single review contain both summary and inline comments?**
Yes. The provider creates a pending review, adds each validated inline comment, and submits the enterprise summary with the comments in one COMMENT review.

## E. AI and Generative Engine

**Which model is used?**
The default configured model is `openai.gpt-5`; the workflow can override it using `REVIEW_MODEL`.

**Why use Capgemini Generative Engine?**
It is the configured enterprise inference endpoint for this POC and exposes an OpenAI-compatible chat-completions interface.

**What code is sent to the model?**
Only selected, bounded PR diff content plus PR metadata and enterprise policy. The entire repository is not sent.

**Does the model receive the entire repository?**
No. It receives only the bounded review context prepared by the application.

**How is prompt injection mitigated?**
The prompt identifies PR metadata and code as untrusted data, keeps enterprise policy authoritative, limits review scope, and validates output locations. Prompt-level defenses reduce risk but cannot guarantee that a model is immune to injection.

**Why require structured JSON?**
A predictable schema lets the application validate fields and safely process findings instead of relying on free-form prose.

**Why use Pydantic?**
Pydantic enforces required fields, types, and allowed severity values before review publishing.

**What happens when the model returns invalid JSON?**
Parsing fails and no AI review is published for that run.

**What happens when Pydantic validation fails?**
The run fails before publishing the invalid response.

**What happens when Generative Engine times out?**
The request raises a clear timeout error and the normal review is not published. Bounded retries are future hardening.

**How is model context bounded?**
By file count, per-file patch length, and total review-input characters, followed by truncation markers when required.

**How are large PRs controlled?**
The same file and character limits cap the submitted diff. Files may be skipped or truncated, so findings cover only the selected bounded input.

**Why is GEP HTTP while GitHub uses MCP?**
GEP is the inference service and is called through its HTTP API. GitHub PR reads and writes are tools exposed by the official GitHub MCP Server.

## F. Multi-Repository and Multi-Language

**Can one reviewer support multiple repositories?**
Yes. Consumer caller workflows invoke the central reusable workflow; the reviewer uses runtime event context rather than a hardcoded repository.

**Which repositories were validated?**
Live validation includes `ai-code-review-demo` and Spring-petclinic; `pets-workshop` is also configured as a consumer.

**How does the reviewer know which repository triggered it?**
The workflow passes the triggering repository and event context to the reviewer, which derives owner, repository, PR number, and head SHA.

**Is repository information hardcoded?**
No, the target repository is supplied by the workflow context or supported reusable-workflow input.

**Can the reviewer analyze Java?**
The generic AI review path has been validated using Java PR changes.

**Can the reviewer analyze Python?**
The generic AI review path has been validated using Python PR changes.

**Can the reviewer analyze JavaScript?**
The generic AI review path has been validated using JavaScript PR changes.

**Does multi-language support mean compilers and linters run?**
No. The agent reviews diff text and does not automatically run dedicated language-specific compilers, linters, or test suites.

**Can repository-specific policies be introduced later?**
Yes, policy selection can be added as a future feature, with explicit configuration and governance. The current policy is central.

## G. Edge Cases

**What happens when a PR only renames files?**
A pure rename with no hunk is treated as non-reviewable metadata. The agent does not invent a patch or inline line.

**What happens for metadata-only changes?**
Hunkless mode or metadata changes are skipped with an explicit reason. In a mixed PR, content patches are still reviewed.

**What happens when the PR has zero effective changes?**
A successful empty `get_files` response returns zero files; `get_diff` and GEP are skipped, and the MCP publisher posts a summary-only no-change advisory.

**Why is GEP skipped when there is nothing to review?**
There is no code evidence for model analysis. Skipping avoids an unnecessary request and prevents fabricated findings.

**What happens when a developer fixes an issue and pushes again?**
The push triggers synchronize and the current revision is reviewed. A fix no longer present in the diff is not re-reported by that run; older thread resolution is not automatic.

**What does the synchronize event mean?**
It means the PR branch has received new commits and causes the review workflow to run again.

**What happens if `get_files` has no usable patches?**
The provider calls MCP `get_diff`, splits the unified diff, and joins sections to metadata. No REST fallback exists.

**Why use `get_diff` fallback?**
Some MCP `get_files` results provide filenames/status but omit patch data; `get_diff` supplies the unified PR diff in that case.

**What happens for a deleted line?**
Deleted lines do not yield right-side added-line locations. Related findings remain summary-only unless they also apply to a valid added line.

**What happens for binary files?**
Configured binary extensions are skipped by the existing filter.

**What happens for generated files?**
Configured generated/build paths are excluded from review input.

**What happens for lock files?**
The listed common lock files are skipped.

**What happens for a very large PR?**
File count and character caps bound the input; patches or total diff may be truncated. Review results apply only to supplied content.

## H. Security

**Where are secrets stored?**
In GitHub Actions secrets. `GEP_API_KEY` is used for model inference; `GITHUB_TOKEN` is provided by GitHub Actions for MCP authentication.

**Is `GITHUB_TOKEN` permanent?**
It is a workflow-provided token scoped to the job/run, not a long-lived personal token.

**Why least-privilege workflow permissions?**
To limit the consequences of token misuse or workflow compromise.

**Which permissions are required?**
The checked-in reusable workflow grants `contents: read` and `pull-requests: write`.

**Are PR contents trusted?**
No. Titles, descriptions, filenames, comments, and code are treated as untrusted input.

**How is prompt injection mitigated?**
Policy hierarchy, untrusted-data instructions, bounded input, structured output, and deterministic location validation are used. This is defense-in-depth, not a guarantee.

**Are credentials logged?**
No. Tokens and API keys should not be printed; logs report operational stages and safe metadata only.

**Why does human approval remain required?**
Model output can be incomplete or wrong. The workflow posts COMMENT and does not approve or merge.

**Is this production-ready?**
No. It is an enterprise-oriented POC, not a production-certified system.

**What security hardening is needed for production?**
Review authentication, immutable workflow pinning, audit controls, retries, rate limits, secret handling, threat modeling, retention, monitoring, and organizational governance.

## I. Testing and Validation

**Which scenarios were tested?**
Live runs covered normal source changes, multiple files, MCP read/write, summaries, inline comments, exact right-side locations, get_diff fallback, rename-only and metadata-only changes, zero effective changes, and a fix followed by another push.

**Was multi-repository behavior validated?**
Yes, the central reusable workflow has live runs across more than one consumer repository, including ai-code-review-demo and Spring-petclinic.

**Was multi-language behavior validated?**
The generic review path has been validated with Python, Java, and JavaScript PR changes; no language toolchain is run.

**Were inline comments tested?**
Yes, both unit tests and live Actions runs verified eligible inline comment publishing.

**Were exact changed-line comments tested?**
Yes. Deterministic line-map validation is covered by tests and live placement was verified against changed lines.

**Was get_diff fallback tested?**
Yes, unit tests and Spring-petclinic live validation exercised MCP get_diff fallback and per-file normalization.

**Were rename-only and metadata-only PRs tested?**
Yes. Rename-only and mode/metadata-only sections are skipped without fabricated patches, while mixed source changes remain reviewable.

**Was zero-effective-change tested?**
Yes. Empty successful get_files results skip get_diff and GEP and produce a summary-only MCP advisory.

**Was a developer fix followed by another push tested?**
Yes. A fix/revert followed by a synchronize run produced the current zero-effective-change case and exercised the no-GEP path.

**How are unit tests different from live PR validation?**
Unit tests isolate parsing, validation, and orchestration with mocks. Live Actions runs prove integration with the runner, Docker server, token, external model service, GitHub review publishing, and the tested PR.

**What does a successful live Actions run prove?**
It proves those integrations worked for that run and configuration. It does not prove model correctness, universal reliability, or production certification.

## J. P1 / P2 / P3

**What is P1?**
P1 established the GitHub Actions event/runtime, reusable multi-repository workflow, configuration, and MCP PR reads. P1 is COMPLETE.

**Which P1 requirements are complete?**
Actions trigger/runtime, central reusable workflow, multiple consumers, opened/reopened/synchronize lifecycle, external configuration, and MCP reads are complete and live-validated.

**What is P2?**
P2 completed enterprise summary generation, deterministic added-line validation, inline comments, and MCP pending-review publishing. P2 is COMPLETE.

**Which P2 requirements are complete?**
Summary-only findings, validated RIGHT-side inline comments, pending MCP review create, comment addition, COMMENT submission, and human-in-the-loop behavior are complete.

**What is P3?**
P3 is the roadmap for stable finding identity, duplicate suppression, cross-commit comparisons, resolved-finding detection, and review-thread lifecycle management.

**Why is duplicate handling P3?**
It requires stable finding identity, persistence, and careful comparison across evolving diffs; it is separate from safe first-pass review publication.

**Is duplicate suppression implemented?**
No. Repeated findings may appear on later runs.

**Are old review threads automatically resolved?**
No. A fixed issue is not re-raised if absent from the current diff, but the old GitHub thread is not automatically resolved.

**How could automatic resolution work through GitHub MCP?**
A future implementation could persist finding/thread identity, compare current and prior findings, and use supported GitHub MCP thread-resolution tools only after safe matching and explicit policy.

**What would stable finding identity mean?**
A reproducible fingerprint derived from normalized file, issue/category, and relevant code evidence, designed to remain stable across harmless line shifts while distinguishing different findings.

**How would findings be compared across commits?**
Persist prior findings and revision identifiers, calculate new fingerprints, compare with the current result, and classify continuing, changed, fixed, and new findings. That lifecycle is not implemented.

## K. Production Roadmap

**What needs to change before production?**
Threat modeling, security review, authentication and workflow pinning decisions, retries/backoff, rate-limit handling, observability, audit trail, cost controls, retention policy, large-PR strategy, and operational ownership.

**Should GitHub App authentication be considered?**
Yes. Evaluate GitHub App installation tokens for centralized, narrowly scoped, auditable access across repositories.

**How should transient failures be retried?**
Use bounded retries with exponential backoff and jitter for clearly transient network/service failures; avoid retrying validation errors or non-idempotent writes blindly.

**How should 429 and 5xx responses be handled?**
Respect rate-limit and retry headers, use bounded backoff, record safe telemetry, and fail clearly after the retry budget. The current POC does not implement a generalized retry policy.

**How should very large PRs be processed?**
Use explicit deterministic file prioritization or chunking with reconciliation, preserve file/line provenance, and report coverage. Current behavior applies caps and truncation.

**How could token/cost telemetry be implemented?**
Record model, request size, token usage when provided, latency, and outcome with repository/run identifiers while excluding source secrets and sensitive payloads.

**How could per-repository policies work?**
Add centrally governed policy selection keyed by repository or an approved config file, validate allowed paths, and record which policy version ran.

**How would Azure DevOps MCP fit?**
A separate provider could implement the same application-level read/publish contracts with Azure DevOps MCP tools, preserving the review engine. It is not implemented.

**How should audit logs and operational metrics work?**
Capture run ID, repository/PR identifiers, revision SHA, policy/model versions, stage outcomes, duration, retries, and publication result while avoiding tokens and unnecessary source retention.

**How could finding lifecycle state be persisted?**
Use a governed store keyed by repository, PR, and stable finding fingerprint, with revision and thread IDs, retention rules, and explicit reconciliation semantics.

## 15 Quick Answers for the Live Demo

**1. Why GitHub Actions?** It runs reviews from native PR events on a managed runner. There is no always-on webhook service in this architecture.

**2. Why MCP?** The official GitHub MCP Server is the application's GitHub tool integration for PR reads and review publishing.

**3. Where exactly is MCP used?** `pull_request_read` retrieves files/diffs; pending-review and inline-comment MCP tools publish results.

**4. Is GPT itself calling MCP?** No. Python calls MCP; GEP/GPT is called over HTTP and returns structured output.

**5. Why no FastAPI/ngrok?** Native GitHub PR events trigger Actions, removing the separate listener and tunnel from this POC.

**6. How are inline lines validated?** Proposed file/line pairs must match added lines in the deterministic map from the bounded diff.

**7. Can it review multiple repositories?** Yes. Consumer caller workflows reuse the central workflow and reviewer.

**8. Which languages were tested?** The generic review path was validated using Python, Java, and JavaScript PR changes; no language toolchain runs.

**9. What happens when an issue is fixed?** A synchronize run reviews the current diff; an issue absent from that diff is not re-raised by that run.

**10. What happens when there are no changes?** MCP returns zero files, get_diff and GEP are skipped, and a summary-only COMMENT advisory is posted.

**11. Why Pydantic?** It rejects malformed output and enforces the expected finding schema before publication.

**12. How are large PRs controlled?** File count, per-file diff, and total review-input caps bound context; truncation may omit evidence.

**13. Does AI merge or approve code?** No. It posts an advisory COMMENT review; human approval remains required.

**14. What remains in P3?** Finding identity, duplicate suppression, cross-commit comparison, fixed-finding detection, and review-thread lifecycle.

**15. What is needed before production?** Security and operations hardening: auth review, retries/rate limits, immutable workflow pinning, audit/metrics, cost controls, and lifecycle-state design.
