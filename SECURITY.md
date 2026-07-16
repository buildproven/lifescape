# Security Policy

## Supported version

Security fixes are applied to the latest version on `main`.

## Automated controls

- QA Architect configuration and security validation
- Gitleaks secret detection
- npm and Python dependency vulnerability audits
- ESLint security rules for the browser client
- Locked Python and npm dependency installation
- Ruff, strict mypy, unit/integration/system/browser tests, and package builds

## Reporting a vulnerability

Do not open a public issue. Use GitHub’s private vulnerability-reporting or security-advisory
workflow for `brettstark73/lifescape-engine` and include reproduction steps, impact, and any known
mitigation.

Never include credentials, private evidence, or personal data in a report.
