# /files Download Hardening Verification

## Scope

This checklist verifies the hardened `/files` pipeline end-to-end:

- structured download metadata from tools
- URL normalization and trusted link rules
- API-key compatibility for download endpoints
- protection against fabricated absolute domains in UI rendering

## Automated checks

- `python -m pytest tests/test_tools.py -v`
  - `test_write_file_files_prefix_returns_download_url`
  - `test_make_download_url_returns_structured_relative_url`
  - `test_make_download_url_includes_absolute_when_public_base_set`

Result recorded during implementation:
- 14 passed

## Manual E2E checklist

1. Generate a file with tool chain and confirm returned payload is JSON with:
   - `trusted: true`
   - `url: /files/<id_name>`
   - optional `absolute_url` only when `public_base_url` is configured.
2. Click generated download link in WebUI on same origin:
   - file should download successfully.
3. Enable `server.api_key` and verify:
   - `/files/...` download works from WebUI with appended `api_key`.
4. Post a message containing a fake absolute download URL from an unknown domain:
   - UI should not render it as a trusted download button.
5. Post a message containing a relative `/files/...` token:
   - UI should render it as a download button.

## Rollback note

If UI rendering regresses, keep backend URL normalization in place and temporarily
re-enable previous text-only linkification behavior while preserving trusted-domain checks.
