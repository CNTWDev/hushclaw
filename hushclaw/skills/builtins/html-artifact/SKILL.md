---
name: html-artifact
description: Create polished, data-aware HTML reports, interactive reports, dashboards, and mini-app artifacts when the user explicitly asks for HTML, a webpage, a visual report, or an interactive deliverable. Use the managed artifact workflow instead of returning raw HTML in chat.
---

# Managed HTML artifacts

Treat HTML as a managed deliverable, not chat formatting.

## Workflow

1. Determine the artifact type:
   - `static-report` for research, analysis, executive reports, and printable deliverables. Do not use JavaScript.
   - `interactive-report` only when filters, chart interactions, or client-side exploration materially help. Keep all assets local.
   - `mini-app` only when the user requests input, calculation, or application behavior.
2. Establish audience, decision goal, data scope, evidence, and visual hierarchy before writing code. Reuse known user preferences; ask only when a missing choice would materially change the result.
   For formal reports or unfamiliar layouts, read `{baseDir}/references/quality-gates.md` before implementation.
3. Separate facts and data from presentation. Preserve source URLs, metric definitions, dates, units, and uncertainty in the report.
4. Start from the restrained report patterns in `{baseDir}/assets/report-template/`. Adapt content and hierarchy; keep the design tokens and accessibility foundations unless the user requests a different identity.
5. Write a real local file or directory under the workspace. Bundle CSS, JavaScript, images, fonts, and data locally; do not depend on CDNs or runtime network requests.
6. Call `inspect_html_artifact(path, entrypoint, artifact_type)`. Fix every blocking error and address warnings that affect the requested deliverable.
7. For visual work, preview the entry page with the browser tools at desktop and narrow viewport sizes. Check overflow, empty states, chart labels, console errors, and print layout. Repair local problems instead of rewriting a correct report from scratch.
8. Call `publish_html_artifact`, not `make_download_url`, for the final HTML deliverable. Return the structured artifact result and briefly identify what it contains.

## Design rules

- Lead with the decision or conclusion, then evidence and detail.
- Use typography, spacing, alignment, and restrained color before decoration.
- Prefer semantic HTML, one `h1`, a `main` landmark, real tables for tabular data, and text summaries for charts.
- Keep line length readable, support mobile widths, and include `@media print` for reports.
- Avoid decorative dashboards, excessive cards, gradients, glass effects, animation, and unlabeled charts.
- Never place credentials, API keys, private source data, or HushClaw APIs in generated page code.

## Completion contract

Complete only when validation passes, the artifact is registered, and the response contains the managed preview link. If browser rendering is unavailable, disclose that visual QA was not performed while still completing static validation.
