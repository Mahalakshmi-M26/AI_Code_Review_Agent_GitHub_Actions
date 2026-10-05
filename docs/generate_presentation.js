const path = require('path');

let PptxGenJS;
try {
  PptxGenJS = require('pptxgenjs');
} catch (error) {
  if (error.code !== 'MODULE_NOT_FOUND') throw error;
  PptxGenJS = require(path.join(
    process.env.TEMP,
    'ai-review-pptx-tools',
    'node_modules',
    'pptxgenjs'
  ));
}

const pptx = new PptxGenJS();
pptx.layout = 'LAYOUT_WIDE';
pptx.author = 'AI Code Review Agent POC';
pptx.subject = 'P1 GitHub Actions migration for AI-assisted pull request review';
pptx.title = 'AI Code Review Agent: GitHub Actions Migration';
pptx.company = 'Capgemini';
pptx.lang = 'en-US';
pptx.theme = {
  headFontFace: 'Aptos Display',
  bodyFontFace: 'Aptos',
  lang: 'en-US',
};
pptx.defineLayout({ name: 'POC_WIDE', width: 13.333, height: 7.5 });
pptx.layout = 'POC_WIDE';
pptx.margin = 0;
pptx.background = { color: 'F4F6F3' };

const S = pptx.ShapeType;
const C = {
  ink: '142421',
  deep: '183D35',
  green: '28715F',
  lime: 'C7ED65',
  mist: 'E6EEE8',
  paper: 'F4F6F3',
  white: 'FFFFFF',
  text: '23332F',
  muted: '63736D',
  rule: 'D4DDD6',
  coral: 'D36B56',
  amber: 'E4AE45',
  blue: '54809A',
};
const W = 13.333;
const H = 7.5;

function text(slide, value, x, y, w, h, options = {}) {
  slide.addText(value, {
    x, y, w, h,
    fontFace: options.fontFace || 'Aptos',
    fontSize: options.fontSize || 16,
    color: options.color || C.text,
    bold: options.bold || false,
    breakLine: false,
    margin: 0,
    valign: options.valign || 'mid',
    align: options.align || 'left',
    fit: 'shrink',
    charSpacing: 0,
    paraSpaceAfterPt: 0,
    isTextBox: true,
    ...options,
  });
}

function shape(slide, type, x, y, w, h, fill, line = fill, extra = {}) {
  slide.addShape(type, {
    x, y, w, h,
    fill: { color: fill },
    line: { color: line, transparency: line === fill ? 100 : 0, width: 1 },
    ...extra,
  });
}

function box(slide, label, detail, x, y, w, h, opts = {}) {
  shape(slide, S.roundRect, x, y, w, h, opts.fill || C.white, opts.line || C.rule, {
    radius: 0.08,
    rectRadius: 0.08,
  });
  if (opts.tag) {
    text(slide, opts.tag.toUpperCase(), x + 0.16, y + 0.1, w - 0.32, 0.18, {
      fontSize: 9,
      bold: true,
      color: opts.tagColor || C.green,
      charSpacing: 0,
    });
  }
  text(slide, label, x + 0.16, y + (opts.tag ? 0.34 : 0.17), w - 0.32, opts.detail ? 0.38 : h - 0.32, {
    fontSize: opts.labelSize || 16,
    bold: true,
    color: opts.labelColor || C.ink,
    valign: opts.detail ? 'mid' : 'mid',
  });
  if (detail) {
    text(slide, detail, x + 0.16, y + 0.77, w - 0.32, h - 0.88, {
      fontSize: opts.detailSize || 11,
      color: opts.detailColor || C.muted,
      valign: 'top',
      breakLine: false,
      lineSpacingMultiple: 1.0,
    });
  }
}

function arrow(slide, x1, y1, x2, y2, color = C.green, width = 1.6) {
  slide.addShape(S.line, {
    x: x1, y: y1, w: x2 - x1, h: y2 - y1,
    line: { color, width, endArrowType: 'triangle' },
  });
}

function baseSlide(title, section = 'P1 IMPLEMENTATION') {
  const slide = pptx.addSlide();
  slide.background = { color: C.paper };
  text(slide, section, 0.62, 0.27, 6.4, 0.2, {
    fontSize: 9,
    bold: true,
    color: C.green,
    charSpacing: 1,
  });
  text(slide, title, 0.62, 0.61, 12.1, 0.53, {
    fontFace: 'Aptos Display',
    fontSize: 27,
    bold: true,
    color: C.ink,
  });
  shape(slide, S.line, 0.62, 1.31, 12.08, 0.012, C.rule, C.rule);
  text(slide, 'AI CODE REVIEW AGENT  /  MENTOR DEMO', 0.62, 7.15, 8, 0.16, {
    fontSize: 8,
    color: C.muted,
    charSpacing: 0.4,
  });
  text(slide, String(pptx._slides.length).padStart(2, '0'), 12.05, 7.12, 0.55, 0.18, {
    fontSize: 9,
    bold: true,
    color: C.green,
    align: 'right',
  });
  return slide;
}

function bullet(slide, label, detail, x, y, w, accent = C.green) {
  shape(slide, S.ellipse, x, y + 0.08, 0.09, 0.09, accent, accent);
  text(slide, label, x + 0.22, y, w - 0.22, 0.27, {
    fontSize: 15,
    bold: true,
    color: C.ink,
  });
  text(slide, detail, x + 0.22, y + 0.33, w - 0.22, 0.48, {
    fontSize: 11,
    color: C.muted,
    valign: 'top',
  });
}

function notes(slide, content) {
  slide.addNotes(content);
}

// 1. Title
{
  const slide = pptx.addSlide();
  slide.background = { color: C.ink };
  shape(slide, S.rect, 0, 0, 0.2, H, C.lime, C.lime);
  text(slide, 'PROOF OF CONCEPT  /  P1', 0.88, 0.82, 5.5, 0.24, {
    fontSize: 11,
    bold: true,
    color: C.lime,
    charSpacing: 1,
  });
  text(slide, 'AI Code Review Agent', 0.88, 1.62, 10.9, 0.78, {
    fontFace: 'Aptos Display',
    fontSize: 38,
    bold: true,
    color: C.white,
  });
  text(slide, 'GitHub Actions Migration', 0.9, 2.55, 10.5, 0.55, {
    fontSize: 25,
    color: 'C9D9D1',
  });
  shape(slide, S.line, 0.9, 3.46, 4.6, 0.025, C.green, C.green);
  text(slide, 'From webhook-hosted review to a reusable, event-driven PR workflow', 0.9, 3.8, 9.7, 0.55, {
    fontSize: 17,
    color: C.white,
  });
  text(slide, 'P1 completed path', 0.9, 6.3, 2.2, 0.25, {
    fontSize: 11,
    bold: true,
    color: C.lime,
  });
  text(slide, 'P2: deterministic diff mapping + inline comments', 3.2, 6.3, 5.9, 0.25, {
    fontSize: 11,
    color: 'C9D9D1',
  });
  notes(slide, 'Open with the scope: this is the P1 proof of concept and migration story. The deck describes the implementation in the central reviewer repository; it does not claim production certification. P2 is future work and is not implemented.');
}

// 2. Business need
{
  const slide = baseSlide('Business Need', 'WHY THE MIGRATION');
  text(slide, 'REVIEW NEED', 0.8, 1.72, 2.2, 0.2, { fontSize: 9, bold: true, color: C.green, charSpacing: 0.6 });
  text(slide, 'Give pull requests a consistent, policy-guided first-pass review.', 0.8, 2.1, 5.25, 1.12, {
    fontFace: 'Aptos Display', fontSize: 24, bold: true, color: C.ink, valign: 'top',
  });
  text(slide, 'Keep the reviewer, policy, and output contract in one place while using the PR lifecycle already managed by GitHub.', 0.8, 3.43, 5.2, 0.85, {
    fontSize: 15, color: C.muted, valign: 'top',
  });
  shape(slide, S.line, 6.52, 1.8, 0.018, 4.65, C.rule, C.rule);
  bullet(slide, 'Central policy', 'Apply the same enterprise review instructions across consumer repositories.', 7.05, 1.92, 5.1);
  bullet(slide, 'PR-timed execution', 'Start review on opened, reopened, and synchronize events.', 7.05, 3.06, 5.1, C.blue);
  bullet(slide, 'Actionable feedback', 'Post one structured advisory summary to the pull request.', 7.05, 4.2, 5.1, C.coral);
  text(slide, 'Human approval remains required.', 7.27, 5.56, 4.6, 0.32, { fontSize: 13, bold: true, color: C.deep });
  notes(slide, 'Frame the business need as consistency and workflow fit: review happens when a PR changes, the policy is centralized, and results return to GitHub. Avoid claiming measured productivity, defect reduction, or quality improvement; no such metrics are captured in the repository.');
}

// 3. Old vs new architecture
{
  const slide = baseSlide('Webhook POC vs. GitHub Actions', 'ARCHITECTURE COMPARISON');
  text(slide, 'PREVIOUS WEBHOOK PATH', 0.88, 1.57, 4.9, 0.24, { fontSize: 10, bold: true, color: C.coral, charSpacing: 0.5 });
  text(slide, 'P1 ACTIONS PATH', 7.06, 1.57, 4.9, 0.24, { fontSize: 10, bold: true, color: C.green, charSpacing: 0.5 });
  const left = [
    ['GitHub PR', 'Webhook event'],
    ['ngrok', 'Tunnel forwarding'],
    ['FastAPI', 'Always-on listener'],
    ['GitHub MCP', 'PR context access'],
    ['AI review', 'Return findings'],
  ];
  const right = [
    ['GitHub PR', 'pull_request event'],
    ['GitHub Actions', 'Runner lifecycle'],
    ['Reusable workflow', 'Central code + policy'],
    ['Generative Engine', 'Structured response'],
    ['GitHub review', 'PR-level COMMENT'],
  ];
  left.forEach((item, i) => {
    const y = 1.98 + i * 0.87;
    box(slide, item[0], item[1], 0.9, y, 4.7, 0.66, { tag: `0${i + 1}`, tagColor: C.coral, labelSize: 13, detailSize: 10 });
    if (i < left.length - 1) arrow(slide, 3.25, y + 0.68, 3.25, y + 0.83, C.coral, 1.2);
  });
  left.forEach((_, i) => shape(slide, S.line, 6.44, 1.87 + i * 0.87, 0.014, 0.69, C.rule, C.rule));
  right.forEach((item, i) => {
    const y = 1.98 + i * 0.87;
    box(slide, item[0], item[1], 7.0, y, 5.25, 0.66, { tag: `0${i + 1}`, tagColor: C.green, labelSize: 13, detailSize: 10 });
    if (i < right.length - 1) arrow(slide, 9.63, y + 0.68, 9.63, y + 0.83, C.green, 1.2);
  });
  text(slide, 'The webhook diagram is historical context; these services are not part of the current repository.', 0.9, 6.52, 11.7, 0.3, {
    fontSize: 11, color: C.muted, italic: true,
  });
  notes(slide, 'Compare the two paths. The earlier architecture was provided as context and is not implemented in this repository. The new path uses the GitHub PR event, Actions, a reusable workflow, the central reviewer, the model endpoint, validation, and a PR-level review. GitHub REST is used directly by the Python reviewer; the current P1 has no GitHub MCP dependency.');
}

// 4. Challenges
{
  const slide = baseSlide('Challenges in the Previous Architecture', 'MIGRATION DRIVER');
  const items = [
    ['01', 'Always-running service', 'A listener must be available when GitHub sends a webhook.', C.coral],
    ['02', 'ngrok dependency', 'A tunnel and reachable endpoint add setup and configuration steps.', C.amber],
    ['03', 'Webhook maintenance', 'Endpoint setup and failed deliveries require operational attention.', C.blue],
    ['04', 'Operational overhead', 'Service lifecycle, networking, and logs sit outside the PR workflow.', C.green],
  ];
  items.forEach((item, i) => {
    const y = 1.72 + i * 1.11;
    shape(slide, S.roundRect, 0.85, y, 11.65, 0.86, C.white, C.rule);
    shape(slide, S.rect, 0.85, y, 0.09, 0.86, item[3], item[3]);
    text(slide, item[0], 1.13, y + 0.22, 0.45, 0.25, { fontSize: 11, bold: true, color: item[3] });
    text(slide, item[1], 1.82, y + 0.15, 3.1, 0.27, { fontSize: 15, bold: true, color: C.ink });
    text(slide, item[2], 5.08, y + 0.13, 6.95, 0.48, { fontSize: 12, color: C.muted });
  });
  text(slide, 'Motivation, not measured incident or cost data.', 0.88, 6.4, 5.7, 0.27, { fontSize: 10, color: C.muted, italic: true });
  notes(slide, 'Describe these as architectural challenges motivating the change, not measured incidents or costs. GitHub Actions removes the need for the separately running listener and tunnel in this workflow. It still requires secure secret handling, correct permissions, and workflow maintenance.');
}

// 5. New architecture
{
  const slide = baseSlide('GitHub Actions Architecture', 'P1 REQUEST-TO-REVIEW');
  const nodes = [
    ['GitHub PR', 'opened / reopened / synchronize', C.mist],
    ['Reusable workflow', 'workflow_call', C.white],
    ['Central reviewer', 'Python + enterprise policy', C.white],
    ['Generative Engine', 'Capgemini endpoint', C.white],
    ['Pydantic', 'Validate response shape', C.white],
    ['GitHub Review', 'One advisory PR summary', C.lime],
  ];
  const x0 = 0.7;
  const gap = 0.22;
  const nodeW = 1.86;
  nodes.forEach((item, i) => {
    const x = x0 + i * (nodeW + gap);
    box(slide, item[0], item[1], x, 2.31, nodeW, 1.55, {
      fill: item[2], labelSize: 14, detailSize: 10,
    });
    if (i < nodes.length - 1) arrow(slide, x + nodeW + 0.025, 3.07, x + nodeW + gap - 0.02, 3.07);
  });
  shape(slide, S.roundRect, 1.04, 4.52, 11.25, 1.05, C.ink, C.ink);
  text(slide, 'Runner setup', 1.36, 4.78, 1.42, 0.25, { fontSize: 13, bold: true, color: C.lime });
  text(slide, 'Checkout central + target repos   ·   Python 3.12   ·   Install requirements   ·   Run reviewer.main', 2.94, 4.69, 8.96, 0.45, {
    fontSize: 13, color: C.white,
  });
  text(slide, 'Permissions: contents: read  |  pull-requests: write', 1.05, 6.0, 6.3, 0.28, { fontSize: 11, bold: true, color: C.deep });
  text(slide, 'Secret: GEP_API_KEY  ·  GitHub API: built-in github.token', 1.05, 6.35, 7.7, 0.28, { fontSize: 11, color: C.muted });
  notes(slide, 'Walk left to right through the actual workflow. It checks out the central repository at main and the target PR head, sets up Python 3.12, installs requirements, and runs reviewer.main. The job grants contents read and pull-requests write. GEP_API_KEY is required; the GitHub API token is github.token passed as GITHUB_TOKEN.');
}

// 6. Reusable architecture
{
  const slide = baseSlide('Reusable Multi-Repository Design', 'ONE CENTRAL REVIEWER');
  shape(slide, S.roundRect, 0.83, 1.78, 5.4, 4.65, C.ink, C.ink);
  text(slide, 'CENTRAL REVIEWER REPOSITORY', 1.18, 2.08, 4.7, 0.24, { fontSize: 10, bold: true, color: C.lime, charSpacing: 0.5 });
  text(slide, 'Single source of review behavior', 1.18, 2.55, 4.6, 0.4, { fontSize: 19, bold: true, color: C.white });
  const central = ['Reusable workflow', 'Python reviewer', 'Enterprise policy', 'Pydantic models + tests'];
  central.forEach((item, i) => {
    shape(slide, S.roundRect, 1.18, 3.18 + i * 0.66, 4.55, 0.46, '24473F', '365E52');
    text(slide, item, 1.39, 3.27 + i * 0.66, 4.1, 0.22, { fontSize: 12, color: C.white, bold: i === 0 });
  });
  box(slide, 'Repo A', 'ai-code-review-demo\nCaller workflow', 8.28, 2.12, 3.55, 1.31, { tag: 'CONSUMER', tagColor: C.blue, labelSize: 16 });
  box(slide, 'Repo B', 'pets-workshop\nCaller workflow', 8.28, 4.35, 3.55, 1.31, { tag: 'CONSUMER', tagColor: C.coral, labelSize: 16 });
  arrow(slide, 7.96, 2.76, 6.48, 2.76, C.green, 1.8);
  arrow(slide, 7.96, 5.0, 6.48, 5.0, C.green, 1.8);
  text(slide, 'invoke', 6.71, 2.42, 0.95, 0.2, { fontSize: 9, color: C.green, bold: true, align: 'center' });
  text(slide, 'invoke', 6.71, 4.66, 0.95, 0.2, { fontSize: 9, color: C.green, bold: true, align: 'center' });
  text(slide, 'Consumer caller examples are conceptual here; no Repo A or Repo B workflow is checked in.', 0.87, 6.63, 11.5, 0.28, { fontSize: 10, color: C.muted, italic: true });
  notes(slide, 'The central repository holds the reusable workflow, Python reviewer, enterprise policy, models, and tests. Repo A and Repo B are the requested consumer examples; their actual caller workflow files are not present in this workspace. Each consumer needs a minimal caller workflow plus configured secret and cross-repository workflow access. The current reusable YAML checks out this central repo at main.');
}

// 7. Lifecycle
{
  const slide = baseSlide('Workflow Execution Lifecycle', 'PULL_REQUEST TRIGGERS');
  const events = [
    ['opened', 'A pull request is created.', C.green],
    ['reopened', 'A closed PR is reopened.', C.blue],
    ['synchronize', 'New commits update the PR branch.', C.coral],
  ];
  shape(slide, S.line, 2.03, 3.02, 9.33, 0.025, C.rule, C.rule);
  events.forEach((item, i) => {
    const cx = 2.08 + i * 4.57;
    shape(slide, S.ellipse, cx, 2.76, 0.5, 0.5, item[2], item[2]);
    text(slide, String(i + 1).padStart(2, '0'), cx, 2.9, 0.5, 0.18, { fontSize: 9, bold: true, color: C.white, align: 'center' });
    text(slide, item[0], cx - 0.35, 3.58, 2.2, 0.37, { fontSize: 19, bold: true, color: C.ink });
    text(slide, item[1], cx - 0.35, 4.08, 2.7, 0.66, { fontSize: 12, color: C.muted, valign: 'top' });
  });
  shape(slide, S.roundRect, 3.58, 5.47, 6.15, 0.65, C.mist, C.mist);
  text(slide, 'All three events invoke the same reusable review job.', 3.85, 5.65, 5.6, 0.24, { fontSize: 14, bold: true, color: C.deep, align: 'center' });
  notes(slide, 'These trigger names are configured in the README caller workflow example. Opened and reopened initiate review; synchronize reruns review after the PR branch receives new commits. All follow the same reusable workflow path.');
}

// 8. Review engine flow
{
  const slide = baseSlide('Review Engine Flow', 'DIFF TO ADVISORY REVIEW');
  const nodes = [
    ['01', 'Diff', 'Fetch changed-file patches; filter and cap input.', C.blue],
    ['02', 'Policy', 'Load central enterprise review instructions.', C.green],
    ['03', 'Generative Engine', 'Request a JSON-object review response.', C.coral],
    ['04', 'Pydantic', 'Parse fields and reject invalid structure/severity.', C.amber],
    ['05', 'Review', 'Format Markdown and post one PR-level comment.', C.deep],
  ];
  nodes.forEach((item, i) => {
    const x = 0.76 + i * 2.53;
    shape(slide, S.ellipse, x + 0.62, 1.87, 0.5, 0.5, item[3], item[3]);
    text(slide, item[0], x + 0.62, 2.02, 0.5, 0.16, { fontSize: 9, bold: true, color: C.white, align: 'center' });
    box(slide, item[1], item[2], x, 2.69, 2.1, 2.0, { labelSize: 14, detailSize: 11, tag: 'STAGE', tagColor: item[3] });
    if (i < nodes.length - 1) arrow(slide, x + 2.12, 3.68, x + 2.48, 3.68, C.green, 1.4);
  });
  text(slide, 'Input bounds', 0.9, 5.44, 1.25, 0.28, { fontSize: 12, bold: true, color: C.ink });
  text(slide, '40 files max  ·  12,000 chars per patch  ·  100,000 chars total  ·  60s timeout', 2.15, 5.44, 9.9, 0.28, { fontSize: 12, color: C.muted });
  text(slide, 'Defaults from the current reusable workflow; repository variables may override them.', 0.91, 5.9, 10.3, 0.25, { fontSize: 10, color: C.muted, italic: true });
  notes(slide, 'Describe the implemented pipeline. The reviewer obtains changed-file patches from the GitHub REST API, filters generated/build paths, lockfiles, binary extensions, and patchless files, then enforces the configured caps. It loads enterprise_review.md and sends a bounded prompt to the Capgemini OpenAI-compatible chat completions endpoint. It parses the model JSON, validates the Pydantic model, formats the review, and posts a COMMENT review.');
}

// 9. Demo results
{
  const slide = baseSlide('P1 Demo Results', 'WHAT THE REPOSITORY DEMONSTRATES');
  const results = [
    ['01', 'Reusable execution', 'PR event invokes centralized reviewer workflow.', C.green],
    ['02', 'Bounded review input', 'Filters noisy files and limits diff size.', C.blue],
    ['03', 'Structured output', 'Malformed JSON or invalid severity is rejected.', C.coral],
    ['04', 'GitHub feedback', 'One advisory Markdown review is posted to the PR.', C.amber],
  ];
  results.forEach((item, i) => {
    const x = 0.88 + (i % 2) * 6.05;
    const y = 1.8 + Math.floor(i / 2) * 1.76;
    box(slide, item[1], item[2], x, y, 5.55, 1.27, { tag: item[0], tagColor: item[3], labelSize: 15, detailSize: 11 });
  });
  shape(slide, S.roundRect, 0.9, 5.66, 11.55, 0.76, C.mist, C.mist);
  text(slide, 'Unit-tested: filtering  ·  truncation  ·  policy loading  ·  JSON errors  ·  schema rules  ·  rendering', 1.15, 5.9, 11.0, 0.25, {
    fontSize: 11, bold: true, color: C.deep, align: 'center',
  });
  notes(slide, 'Present these as demonstrated code-level outcomes, not business metrics. The tests cover file filtering and truncation, policy loading, prompt untrusted-data language, Markdown rendering, malformed JSON handling, schema validation, severity rejection, and a nullable line. They mock the model endpoint and do not prove a live PR run or model accuracy.');
}

// 10. Languages
{
  const slide = baseSlide('Multi-Language Validation', 'DEMO MATRIX  /  EVIDENCE BOUNDARY');
  const langs = [
    ['Python', 'Unit-level diff examples exist', 'No recorded live PR validation', C.green],
    ['JavaScript', 'Text diff can enter shared review path', 'No language-specific test evidence', C.blue],
    ['Java', 'Text diff can enter shared review path', 'No language-specific test evidence', C.coral],
  ];
  langs.forEach((item, i) => {
    const x = 0.85 + i * 4.18;
    shape(slide, S.roundRect, x, 1.94, 3.75, 2.55, C.white, C.rule);
    shape(slide, S.rect, x, 1.94, 3.75, 0.08, item[3], item[3]);
    text(slide, item[0], x + 0.25, 2.27, 3.2, 0.42, { fontFace: 'Aptos Display', fontSize: 21, bold: true, color: C.ink });
    text(slide, item[1], x + 0.25, 2.92, 3.2, 0.53, { fontSize: 12, color: C.deep, valign: 'top' });
    text(slide, item[2], x + 0.25, 3.73, 3.2, 0.45, { fontSize: 11, color: C.muted, valign: 'top' });
  });
  shape(slide, S.roundRect, 0.89, 5.02, 11.55, 0.83, C.ink, C.ink);
  text(slide, 'Shared text-diff model path  ≠  language-specific parser, compiler, linter, or test suite', 1.14, 5.29, 11.0, 0.28, {
    fontSize: 13, bold: true, color: C.white, align: 'center',
  });
  text(slide, 'Use these three languages as a proposed controlled demo matrix, not a completed P1 test claim.', 0.9, 6.14, 11.0, 0.25, { fontSize: 10, color: C.muted, italic: true });
  notes(slide, 'The requested presentation calls for Python, JavaScript, and Java. Be precise: the review code treats patches as text and uses the same model prompt for all. Python-style examples occur in unit tests, but there are no dedicated JavaScript or Java fixtures and no language-specific analyzer. Propose a small controlled PR for each language as a follow-up validation demo; do not present this matrix as completed validation.');
}

// 11. Sample findings
{
  const slide = baseSlide('Sample Review Findings', 'ILLUSTRATIVE OUTPUT  /  NOT A CAPTURED PR');
  const samples = [
    ['MEDIUM', 'Error handling', 'src/client.py  |  Line unknown', 'Handle the request failure case visible in the diff.', 'Recommendation: handle the expected error and retain useful caller context.', C.amber],
    ['LOW', 'Testing', 'src/validator.js  |  Line unknown', 'The new validation branch lacks a matching test in the reviewed diff.', 'Recommendation: add a focused boundary-input test using project conventions.', C.blue],
  ];
  samples.forEach((item, i) => {
    const x = 0.88 + i * 6.04;
    shape(slide, S.roundRect, x, 1.79, 5.55, 3.57, C.white, C.rule);
    shape(slide, S.rect, x, 1.79, 0.1, 3.57, item[5], item[5]);
    text(slide, item[0], x + 0.32, 2.08, 1.1, 0.24, { fontSize: 10, bold: true, color: item[5] });
    text(slide, item[1], x + 1.5, 2.05, 3.55, 0.3, { fontSize: 13, bold: true, color: C.ink });
    text(slide, item[2], x + 0.32, 2.64, 4.9, 0.26, { fontSize: 10, color: C.muted });
    shape(slide, S.line, x + 0.32, 3.08, 4.88, 0.015, C.rule, C.rule);
    text(slide, 'ISSUE', x + 0.32, 3.3, 0.72, 0.2, { fontSize: 9, bold: true, color: C.green });
    text(slide, item[3], x + 0.32, 3.61, 4.85, 0.55, { fontSize: 13, color: C.text, valign: 'top' });
    text(slide, item[4], x + 0.32, 4.42, 4.85, 0.56, { fontSize: 11, color: C.muted, valign: 'top' });
  });
  text(slide, 'Examples demonstrate the renderer shape only; they are not real AI findings.', 0.9, 5.91, 10.9, 0.27, { fontSize: 10, color: C.muted, italic: true });
  notes(slide, 'Use this slide to show the shape and tone of a finding. These examples are explicitly illustrative and are not copied from a live model response or PR. Both show Line unknown because P1 allows line to be null and has no deterministic mapping to diff hunks. The actual renderer includes severity, category, file, issue, recommendation, and suggested fix.');
}

// 12. Benefits
{
  const slide = baseSlide('Benefits Achieved', 'P1 ARCHITECTURE OUTCOMES');
  const benefits = [
    ['No FastAPI listener', 'No separately hosted webhook service in the P1 path.', C.green],
    ['No ngrok tunnel', 'GitHub Actions starts from the PR event directly.', C.blue],
    ['Reusable workflow', 'Central code and policy can serve multiple repositories.', C.coral],
    ['Structured advisory', 'Pydantic-validated result returns as a PR review comment.', C.amber],
  ];
  benefits.forEach((item, i) => {
    const x = 0.87 + (i % 2) * 6.06;
    const y = 1.85 + Math.floor(i / 2) * 1.78;
    shape(slide, S.roundRect, x, y, 5.58, 1.37, C.white, C.rule);
    shape(slide, S.ellipse, x + 0.27, y + 0.31, 0.32, 0.32, item[2], item[2]);
    text(slide, '+', x + 0.27, y + 0.35, 0.32, 0.2, { fontSize: 14, bold: true, color: C.white, align: 'center' });
    text(slide, item[0], x + 0.8, y + 0.2, 4.4, 0.32, { fontSize: 15, bold: true, color: C.ink });
    text(slide, item[1], x + 0.8, y + 0.66, 4.35, 0.48, { fontSize: 11, color: C.muted, valign: 'top' });
  });
  text(slide, 'The workflow still depends on secure secrets, permissions, GitHub availability, and model service availability.', 0.9, 5.89, 11.4, 0.32, {
    fontSize: 11, color: C.deep, bold: true,
  });
  notes(slide, 'State the practical benefits in scope: no FastAPI listener or ngrok tunnel is required in this P1 execution path; a reusable workflow centralizes behavior; and validated structured output is returned to the PR. Also state what remains: secret configuration, GitHub permissions, external service availability, and human review still matter.');
}

// 13. Limitations
{
  const slide = baseSlide('Current Limitations', 'KNOWN P1 BOUNDARIES');
  const limits = [
    ['Line unknown', 'Model line is nullable and not verified against changed diff positions.', C.coral],
    ['Summary only', 'One PR-level review; no inline comments or resolved threads.', C.amber],
    ['No duplicate handling', 'Repeated findings across synchronize runs are not tracked.', C.blue],
    ['No language toolchain', 'No compile, lint, or target-repository test execution.', C.green],
    ['Operational evidence', 'No recorded live PR/model run or production monitoring in this repo.', C.deep],
  ];
  limits.forEach((item, i) => {
    const y = 1.67 + i * 0.89;
    shape(slide, S.roundRect, 0.88, y, 11.6, 0.68, C.white, C.rule);
    shape(slide, S.rect, 0.88, y, 0.09, 0.68, item[2], item[2]);
    text(slide, item[0], 1.18, y + 0.16, 2.55, 0.28, { fontSize: 13, bold: true, color: C.ink });
    text(slide, item[1], 3.86, y + 0.12, 8.17, 0.39, { fontSize: 11, color: C.muted });
  });
  notes(slide, 'Lead with line mapping: the output may say Line unknown; a numeric model-supplied line is not validated either. The review is a single PR summary, with no inline comments, duplicate handling, or resolved thread lifecycle. Language-specific checks and recorded live end-to-end evidence are absent. These are known P1 boundaries, not surprises.');
}

// 14. P2 roadmap
{
  const slide = baseSlide('P2 Roadmap', 'NEXT: DETERMINISTIC DIFF MAPPING');
  shape(slide, S.roundRect, 0.9, 1.87, 5.65, 3.93, C.ink, C.ink);
  text(slide, 'P2', 1.27, 2.18, 1.2, 0.58, { fontFace: 'Aptos Display', fontSize: 33, bold: true, color: C.lime });
  text(slide, 'Make locations trustworthy', 1.27, 2.92, 4.7, 0.62, { fontSize: 21, bold: true, color: C.white });
  bullet(slide, 'Deterministic diff mapping', 'Resolve findings against actual changed lines and GitHub diff positions.', 1.27, 3.8, 4.72, C.lime);
  bullet(slide, 'Inline comments', 'Post only when the file and line map to a valid changed location.', 1.27, 4.83, 4.72, C.coral);
  box(slide, 'Validation before rollout', 'Cover added/deleted lines, multiple hunks, renames, invalid locations, and fallback summary behavior.', 7.2, 2.55, 4.85, 2.03, {
    tag: 'P2 ACCEPTANCE FOCUS', tagColor: C.green, labelSize: 16, detailSize: 12,
  });
  text(slide, 'No P2 code is included in this P1 deliverable.', 7.23, 5.02, 4.8, 0.38, { fontSize: 12, bold: true, color: C.coral });
  notes(slide, 'P2 is roadmap only. First map model findings deterministically to the actual changed diff, validate GitHub review-comment positions, and test edge cases such as deleted lines, multiple hunks, renames, and invalid locations. Only then create inline comments for findings with valid mappings, preserving a PR-level summary fallback. This deck does not implement P2.');
}

// 15. Conclusion
{
  const slide = pptx.addSlide();
  slide.background = { color: C.deep };
  shape(slide, S.rect, 0, 0, 0.2, H, C.lime, C.lime);
  text(slide, 'CONCLUSION', 0.88, 0.75, 3.5, 0.24, { fontSize: 10, bold: true, color: C.lime, charSpacing: 0.8 });
  text(slide, 'A smaller operational footprint.\nA clearer next step.', 0.88, 1.44, 10.8, 1.5, {
    fontFace: 'Aptos Display', fontSize: 32, bold: true, color: C.white, valign: 'top',
  });
  text(slide, 'PR event  →  reusable workflow  →  central policy  →  validated advisory review', 0.9, 3.42, 11.4, 0.46, {
    fontSize: 17, color: 'D6E3DC',
  });
  shape(slide, S.roundRect, 0.91, 4.53, 11.1, 1.0, '245449', '3F7162');
  text(slide, 'P1 establishes the path.  P2 makes finding locations deterministic.', 1.22, 4.84, 10.5, 0.34, {
    fontSize: 16, bold: true, color: C.lime, align: 'center',
  });
  text(slide, 'AI findings remain advisory; human approval is required.', 0.93, 6.27, 8.1, 0.3, { fontSize: 12, color: C.white });
  notes(slide, 'Close by summarizing the implemented P1 path and its key boundary. GitHub Actions replaces the separate listener and tunnel in this flow; reusable code and policy return a Pydantic-validated advisory review. The next engineering step is deterministic finding-to-diff mapping, which unlocks safe inline placement. Invite questions about permissions, trust boundaries, language evidence, and line mapping.');
}

pptx.writeFile({ fileName: path.join(__dirname, 'ai-code-review-agent-p1.pptx') });