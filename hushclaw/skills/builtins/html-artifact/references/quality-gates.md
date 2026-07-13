# HTML artifact quality gates

## Content

- State audience, time range, units, and the decision the report supports.
- Distinguish sourced facts, calculations, and interpretation.
- Place the most important conclusion in the first viewport.
- Include source notes and definitions near the claims they support.

## Visual

- Use a consistent spacing scale and no more than two font families.
- Keep body text at least 15px and normal text contrast at least 4.5:1.
- Give every chart a title, unit, time range, and concise text takeaway.
- Verify 1440px, 1024px, and 390px widths without horizontal page overflow.
- Use print rules to remove controls and prevent important blocks from splitting.

## Runtime

- Bundle dependencies locally; remote resources fail managed validation.
- Use no scripts for `static-report`.
- Do not use inline event handlers or `javascript:` URLs.
- Do not assume access to cookies, parent frames, local storage, WebSockets, or HushClaw APIs.
- Treat artifact data as a snapshot and display its generated/data timestamp.

## Delivery

- Run `inspect_html_artifact` until `ok` is true.
- Visually inspect rendered output when browser tools are available.
- Publish exactly once with `publish_html_artifact` after validation.
