# Security Policy

We take the safety of GeneralManager deployments seriously. This policy
describes the supported versions, how to report vulnerabilities, and how the
maintainers handle disclosures.

## Supported Versions

Security fixes are backported only to actively maintained branches.

| Version | Supported |
| ------- | --------- |
| `main`  | ✅ |
| Latest released minor (currently `0.19.x`) | ✅ |
| Older releases | ❌ |

If you rely on an older release, please upgrade before requesting a fix.

## Reporting a Vulnerability

- Email `tkleindick@yahoo.de` with the subject line `SECURITY` and include a
  proof of concept, affected versions, and impact assessment. Encrypting the
  report is not required but feel free to reach out for a PGP key if needed.
- Alternatively, use the GitHub “Report a vulnerability” workflow to open a
  private Security Advisory.

Please do **not** open a public issue for security problems.

## Disclosure Process

1. We acknowledge new reports within ten business days.
2. Once the issue is validated we coordinate on a fix, release timeline, and
   disclosure plan with you.
3. A patch release is published and the advisory is updated with credits for
   the reporter (if desired).

We aim to ship critical fixes as quickly as possible and prioritize issues that
allow remote code execution or data exposure.

## Security Best Practices

- Run the latest release and apply system-level security updates promptly.
- Pin dependencies in production and review changelogs before upgrading.
- Limit access to the Django admin, GraphQL endpoints, and management commands
  to trusted networks.
- Take regular backups of both data and configuration so you can roll back if a
  compromise occurs.

Thank you for practicing responsible disclosure and helping keep the community
safe.
