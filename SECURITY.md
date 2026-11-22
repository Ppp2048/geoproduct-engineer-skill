# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately: on GitHub, open the repository's **Security** tab and choose **Report a vulnerability**. Do not open a public issue, pull request or discussion for a security problem.

Include:
- what an attacker can do;
- the affected version or commit;
- steps or a proof of concept;
- any configuration it depends on, such as the HTTP transport or allowed folders.

You should hear back within seven days. Fixes go into a new release, and the advisory is published once the fix is available. Reporters are credited unless they prefer otherwise.

## Supported versions

Only the latest release, and `main`, receive security fixes while the project is before 1.0.

## Scope

GeoProduct Engineer runs tools on behalf of an AI agent. The model's output, fetched web pages and provider responses are therefore all treated as untrusted. These are vulnerabilities:

- **Code execution.** Code that runs on the user's machine and was not written by the user: from a pipeline spec, a tool argument, a fetched document or a provider response.
- **File access.** Reading or writing outside the workspace and the allowed folders, for example by path traversal, links or archive contents.
- **Network access.** Making `fetch_source_data` or a provider reach a private, loopback or link-local address (server-side request forgery), including through DNS or redirects. Bypassing `robots.txt`, the size or time limits, or the content-type allowlist.
- **Leaking secrets.** Exposing environment variables, credentials or local paths outside the workspace through tool results, logs, exports or error messages.
- **Exposed transport.** Reaching the HTTP transport from beyond the loopback interface.
- **Skipped confirmation.** Getting past the confirmation required for large external workloads, or replacing an existing export without `overwrite: true`.
- **Tampered evidence.** Making evidence or provenance that the toolkit records as checked silently untrue.

These are not vulnerabilities, but are welcome as ordinary issues:

- **Wrong data.** Wrong or out-of-date data from a provider or a source; the toolkit records where data came from, but it cannot vouch for it.
- **Wrong conclusions.** An agent drawing wrong conclusions despite the recorded evidence and limitations.
- **Resource use.** Using resources within the documented limits, such as a large but bounded pipeline the user confirmed.
- **Exposed HTTP server.** Problems that need the user to run the HTTP server on a non-loopback address, which the server refuses.

[docs/architecture.md](docs/architecture.md) (section 9) describes the security model these rules come from.
