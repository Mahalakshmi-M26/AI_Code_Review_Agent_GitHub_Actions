# AI Code Review Agent: Presentation Outline and Speaker Notes

Companion to [`ai-code-review-agent-p1.pptx`](ai-code-review-agent-p1.pptx). Speaker notes are also embedded in the presentation file.

To rebuild the editable deck on Windows with Node.js, install the authoring tool outside the repository and run the generator:

```powershell
npm install --prefix "$env:TEMP\ai-review-pptx-tools" pptxgenjs
node docs/generate_presentation.js
```

## Slide 1 — AI Code Review Agent: GitHub Actions Migration

**On-slide content:** Proof of Concept / P1; AI Code Review Agent; GitHub Actions Migration; from webhook-hosted review to a reusable, event-driven PR workflow; P1 completed path; P2 deterministic diff mapping and inline comments.

**Speaker notes:** Open with the scope: this is the P1 proof of concept and migration story. The deck describes the implementation in the central reviewer repository; it does not claim production certification. P2 is future work and is not implemented.

## Slide 2 — Business Need

**On-slide content:** Give pull requests a consistent, policy-guided first-pass review. Central policy; PR-timed execution; actionable feedback. Human approval remains required.

**Speaker notes:** Frame the business need as consistency and workflow fit: review happens when a PR changes, the policy is centralized, and results return to GitHub. Avoid claiming measured productivity, defect reduction, or quality improvement; no such metrics are captured in the repository.

## Slide 3 — Webhook POC vs. GitHub Actions

**On-slide diagram:** Previous: GitHub PR → ngrok → FastAPI → GitHub MCP → AI review. P1: GitHub PR → GitHub Actions → reusable workflow → Generative Engine → GitHub review.

**On-slide caveat:** The webhook diagram is historical context; these services are not part of the current repository.

**Speaker notes:** Compare the two paths. The earlier architecture was provided as context and is not implemented in this repository. The new path uses the GitHub PR event, Actions, a reusable workflow, the central reviewer, the model endpoint, validation, and a PR-level review. GitHub REST is used directly by the Python reviewer; the current P1 has no GitHub MCP dependency.

## Slide 4 — Challenges in the Previous Architecture

**On-slide content:** Always-running service; ngrok dependency; webhook maintenance; operational overhead.

**Speaker notes:** Describe these as architectural challenges motivating the change, not measured incidents or costs. GitHub Actions removes the need for the separately running listener and tunnel in this workflow. It still requires secure secret handling, correct permissions, and workflow maintenance.

## Slide 5 — GitHub Actions Architecture

**On-slide diagram:** GitHub PR event → reusable workflow → central reviewer → Capgemini Generative Engine → Pydantic → GitHub PR review. Runner setup: checkout central and target repositories; Python 3.12; install requirements; run `reviewer.main`. Permissions: `contents: read`, `pull-requests: write`. Secret: `GEP_API_KEY`; GitHub token: built-in `github.token`.

**Speaker notes:** Walk left to right through the actual workflow. It checks out the central repository at main and the target PR head, sets up Python 3.12, installs requirements, and runs reviewer.main. The job grants contents read and pull-requests write. GEP_API_KEY is required; the GitHub API token is github.token passed as GITHUB_TOKEN.

## Slide 6 — Reusable Multi-Repository Design

**On-slide diagram:** Central reviewer repository contains reusable workflow, Python reviewer, enterprise policy, Pydantic models, and tests. Repo A (`ai-code-review-demo`) and Repo B (`pets-workshop`) invoke the central workflow.

**On-slide caveat:** Consumer caller examples are conceptual here; no Repo A or Repo B workflow is checked in.

**Speaker notes:** The central repository holds the reusable workflow, Python reviewer, enterprise policy, models, and tests. Repo A and Repo B are the requested consumer examples; their actual caller workflow files are not present in this workspace. Each consumer needs a minimal caller workflow plus configured secret and cross-repository workflow access. The current reusable YAML checks out this central repo at main.

## Slide 7 — Workflow Execution Lifecycle

**On-slide flow:** `opened` — PR created; `reopened` — closed PR reopened; `synchronize` — new commits update the PR branch. All invoke the same reusable review job.

**Speaker notes:** These trigger names are configured in the README caller workflow example. Opened and reopened initiate review; synchronize reruns review after the PR branch receives new commits. All follow the same reusable workflow path.

## Slide 8 — Review Engine Flow

**On-slide flow:** Diff → Policy → Generative Engine → Pydantic → Review. Defaults: 40 files maximum; 12,000 characters per patch; 100,000 characters total; 60-second model timeout.

**Speaker notes:** Describe the implemented pipeline. The reviewer obtains changed-file patches from the GitHub REST API, filters generated/build paths, lockfiles, binary extensions, and patchless files, then enforces the configured caps. It loads enterprise_review.md and sends a bounded prompt to the Capgemini OpenAI-compatible chat completions endpoint. It parses the model JSON, validates the Pydantic model, formats the review, and posts a COMMENT review.

## Slide 9 — P1 Demo Results

**On-slide content:** Reusable execution; bounded review input; structured output; GitHub feedback. Unit-tested behavior includes filtering, truncation, policy loading, JSON errors, schema rules, and rendering.

**Speaker notes:** Present these as demonstrated code-level outcomes, not business metrics. The tests cover file filtering and truncation, policy loading, prompt untrusted-data language, Markdown rendering, malformed JSON handling, schema validation, severity rejection, and a nullable line. They mock the model endpoint and do not prove a live PR run or model accuracy.

## Slide 10 — Multi-Language Validation

**On-slide content:** Python: unit-level diff examples exist, no recorded live PR validation. JavaScript: generic diff transport only, no language-specific test evidence. Java: generic diff transport only, no language-specific test evidence.

**On-slide caveat:** Shared text-diff model path does not equal language-specific parser, compiler, linter, or test suite. Treat the three languages as a proposed demo matrix, not a completed P1 test claim.

**Speaker notes:** The requested presentation calls for Python, JavaScript, and Java. Be precise: the review code treats patches as text and uses the same model prompt for all. Python-style examples occur in unit tests, but there are no dedicated JavaScript or Java fixtures and no language-specific analyzer. Propose a small controlled PR for each language as a follow-up validation demo; do not present this matrix as completed validation.

## Slide 11 — Sample Review Findings

**On-slide content:** Two explicitly illustrative, not captured, examples: MEDIUM / Error handling in `src/client.py` and LOW / Testing in `src/validator.js`. Both show `Line unknown` and a recommendation.

**Speaker notes:** Use this slide to show the shape and tone of a finding. These examples are explicitly illustrative and are not copied from a live model response or PR. Both show Line unknown because P1 allows line to be null and has no deterministic mapping to diff hunks. The actual renderer includes severity, category, file, issue, recommendation, and suggested fix.

## Slide 12 — Benefits Achieved

**On-slide content:** No FastAPI listener; no ngrok tunnel; reusable workflow; structured advisory review. The workflow still depends on secure secrets, permissions, GitHub availability, and model service availability.

**Speaker notes:** State the practical benefits in scope: no FastAPI listener or ngrok tunnel is required in this P1 execution path; a reusable workflow centralizes behavior; and validated structured output is returned to the PR. Also state what remains: secret configuration, GitHub permissions, external service availability, and human review still matter.

## Slide 13 — Current Limitations

**On-slide content:** Line unknown; summary only; no duplicate handling; no language toolchain; no recorded live PR/model run or production monitoring in this repository.

**Speaker notes:** Lead with line mapping: the output may say Line unknown; a numeric model-supplied line is not validated either. The review is a single PR summary, with no inline comments, duplicate handling, or resolved thread lifecycle. Language-specific checks and recorded live end-to-end evidence are absent. These are known P1 boundaries, not surprises.

## Slide 14 — P2 Roadmap

**On-slide content:** Deterministic diff mapping; inline comments only for valid changed locations; test added/deleted lines, multiple hunks, renames, invalid locations, and fallback summary behavior. No P2 code is included in this P1 deliverable.

**Speaker notes:** P2 is roadmap only. First map model findings deterministically to the actual changed diff, validate GitHub review-comment positions, and test edge cases such as deleted lines, multiple hunks, renames, and invalid locations. Only then create inline comments for findings with valid mappings, preserving a PR-level summary fallback. This deck does not implement P2.

## Slide 15 — Conclusion

**On-slide content:** A smaller operational footprint. A clearer next step. PR event → reusable workflow → central policy → validated advisory review. P1 establishes the path; P2 makes locations deterministic. Human approval is required.

**Speaker notes:** Close by summarizing the implemented P1 path and its key boundary. GitHub Actions replaces the separate listener and tunnel in this flow; reusable code and policy return a Pydantic-validated advisory review. The next engineering step is deterministic finding-to-diff mapping, which unlocks safe inline placement. Invite questions about permissions, trust boundaries, language evidence, and line mapping.

## Demo Talking Points

1. Explain the migration motivation without suggesting every operational concern disappears.
2. Show the caller/reusable-workflow boundary; Repo A and Repo B are examples, while this repository contains the central implementation.
3. Trace the run from event payload and PR files API through filters, policy, model, Pydantic, formatting, and GitHub `COMMENT` review.
4. Point out the advisory statement, severity counts, evidence fields, and `Line unknown` behavior in the output.
5. Show the local test suite and distinguish mocked/unit-level tests from a live end-to-end PR demo.
6. State that Python, JavaScript, and Java diffs use a shared text path; language-specific validation is not demonstrated.
7. Close with P2: validate finding locations against actual diff positions before creating inline comments.

## Expected Mentor Questions and Answers

**Why move away from FastAPI and ngrok?**  
The PR event already exists in GitHub. Actions supplies the event-driven execution path and removes the separately managed listener and tunnel from this POC. This reduces those operational dependencies; it does not prove production readiness or remove the need to manage secrets, permissions, and workflow reliability.

**Where is the reusable workflow and what is centralized?**  
`.github/workflows/ai-code-review-reusable.yml` calls the central Python package and checks out the policy and Pydantic models alongside it. Consumer repositories need a caller workflow and appropriate secret/access configuration.

**How are Python, JavaScript, and Java validated?**  
P1 submits text diffs to the same model-based review path without a language-specific toolchain. The existing tests do not verify all three languages end to end; a controlled PR matrix is a demo follow-up, not an accomplished test result.

**Can it block a merge?**  
The posted GitHub review event is `COMMENT`, and the policy explicitly says human approval remains required. This implementation does not approve or merge PRs.

**Why can a review say “Line unknown”?**  
The Pydantic finding schema allows `line: null`, and the renderer handles that case. P1 does not deterministically map model locations to GitHub diff positions, so inline placement is deferred to P2.

**What prevents prompt injection?**  
The policy and prompt explicitly treat PR-provided text and code as untrusted and keep the enterprise policy authoritative. This is a prompt-level mitigation; it does not make model behavior infallible.

**What happens if the model returns invalid JSON or an unsupported severity?**  
JSON parsing or Pydantic validation fails, and the workflow exits without posting a review. Tests cover malformed JSON, invalid severity, and nullable lines.

**How is review input bounded?**  
Defaults cap selection at 40 files, each patch at 12,000 characters, and combined diff text at 100,000 characters. Generated/build paths, several lockfiles, binary extensions, and patchless entries are skipped. Large patches may be truncated, which can omit evidence.

**Is the central reusable workflow version pinned?**  
No. The current implementation checks out the central repository's `main` branch. An immutable commit SHA or maintained release tag is a hardening item for broader adoption.

**What is the P2 deliverable?**  
Map each finding deterministically to a changed line and valid GitHub diff position, test that mapping across diff edge cases, then add inline comments for only validated positions.