# Security Policy

## Supported version

Security fixes are applied to the latest `main` branch.

## Never commit

- `.env` files or API tokens
- OAuth client secrets or refresh tokens
- browser cookies, Playwright storage state, or Chrome user-data directories
- YouTube/Amazon/ChatGPT/VoAI authenticated session data
- `jobs/`, logs, screenshots, generated media, or product/comment histories
- private keys and credential JSON files

Use environment variables and keep authenticated Chrome profiles outside this repository. `.env.example` contains placeholders only.

## Reporting a vulnerability

Open a GitHub Security Advisory instead of a public issue when the report may expose credentials or private data. Do not paste a live credential into an issue, pull request, commit, or screenshot.

If a secret is ever committed, revoke/rotate it first, then rewrite every affected Git ref. Deleting it in a later commit is not sufficient.
