# Security policy

sourceshelf is a local, single-user tool maintained on a best-effort basis. There is no support commitment and no response-time guarantee.

## Scope and threat model

- `sourceshelf serve` is read-only, binds to 127.0.0.1 and rejects unexpected `Host` headers (DNS rebinding). Do not expose it to a network. The page is served with a strict Content-Security-Policy (hashes of the inline script and style, no `unsafe-inline`, no framing).
- The `confidential: yes` flag keeps a fiche's text out of the index, search snippets and the summary model. It is a best-effort local safeguard, not an access-control system. Anyone with file access can read `data/`.
- `sourceshelf add` and `sourceshelf run` fetch only http(s) URLs, refuse hosts that resolve to non-public addresses (also after redirects, and for IPv4 addresses embedded in IPv6 forms such as `::ffff:`, NAT64 and 6to4; the `Authorization` header of `sourceshelf describe` is dropped on a redirect to another host; `KB_ALLOW_PRIVATE=1` overrides) and refuse downloads over 50 MB. The address check happens before connecting, so DNS rebinding between check and connection is a residual risk. Fetched content is untrusted input (PDF, HTML, docx parsers run on it; docx with a DTD is refused).
- `sourceshelf search --exact` skips confidential fiches and their texts. Also skipped: symlinked and hardlinked paths and files with byte-identical content (a plain copy under another name). A modified copy (different bytes) is not recognised; keep confidential material out of searched folders. `text:` and `file:` paths outside `data/` are ignored.
- `KB_ENDPOINT` must be https (http only for localhost) before the API key is sent.
- `sourceshelf describe` sends source text to the configured model endpoint (a third party by default). It only runs on explicit command.

## Reporting

Report a vulnerability privately through GitHub security advisories on this repository (Security tab, "Report a vulnerability"). Please do not open a public issue for it. Fixes are made when time allows.
