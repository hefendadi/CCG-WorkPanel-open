# CCG WorkPanel security

The bundled credentials are for the loopback-only local demo.
Do not reuse them in any deployed environment or commit runtime databases,
signing secrets, uploads, certificates or environment files.

## Report a vulnerability privately

Use GitHub Private Vulnerability Reporting: open this repository's
[Security advisories](https://github.com/hefendadi/CCG-WorkPanel-open/security/advisories)
page and select **Report a vulnerability**. Submit the report through that
private channel so maintainers can investigate and coordinate a fix using
GitHub Security Advisories.

Do not submit sensitive vulnerability details through public Issues, pull
requests or comments. Do not include real credentials, tokens or business data,
even in a private report. Use a minimal synthetic reproduction instead.

If private vulnerability reporting is unavailable, open an
[Issue](https://github.com/hefendadi/CCG-WorkPanel-open/issues/new) titled
**Private security reporting channel requested**. Include only the request for
a private contact method; do not describe the vulnerability or attach evidence.
Wait for the maintainer to provide a private channel before sharing details.
GitHub's feature is available for public repositories when enabled by the
maintainer; this fallback applies when the private reporting button is absent.

## Local content checks

Run `python -m demo.security_scan` for the repository content scan. The scanner
is a review aid, not proof that arbitrary externally supplied data is safe.
