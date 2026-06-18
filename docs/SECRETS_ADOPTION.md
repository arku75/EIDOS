> ⚖️ **EIDOS © 2026 SER · Licencia [ESSL v1.0](../LICENSE) — propietaria, source-available.** Prohibida la replicación, el uso comercial y construir un producto competidor. Todo cambio o propuesta debe documentarse en `THIRD_PARTY_CHANGES.md` y comunicarse a SER. EIDOS **no** es open source.

# Secrets Adoption Guide

This document explains how contributors should provide secrets and sensitive configuration values without committing them into the repository.

## Why this matters

- Secrets must never be stored in `git` or in a public GitHub repository.
- This repo must remain safe to clone and inspect without exposing API tokens, passwords, private keys, or runtime credentials.
- The release packaging script creates a sanitized copy under `release_tmp/` and a tarball named `EIDOS_sanitized_RELEASE.tar.gz`.
- Users should add their own secrets locally after cloning.

## What must stay out of the repo

Do not commit:

- `.env` files with real keys or passwords
- runtime folders such as `.eidos/`
- API key files like `api_keys.json`, `*.pem`, `*.key`
- local service credentials, SSH keys, or personal tokens
- backup archives containing secrets

## Recommended workflow for contributors

### 1. Use `.env.example`

Provide a file called `.env.example` with placeholder names only.

Example:

```env
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
EIDOS_TELEGRAM_TOKEN=
EIDOS_TELEGRAM_ALLOWED=
EIDOS_TELEGRAM_ADMIN_ID=
EIDOS_TELEGRAM_CHAT_ID=
DISCORD_BOT_TOKEN=
DATABASE_URL=
```

After cloning, each contributor should create a local `.env`:

```bash
cp .env.example .env
```

Then fill in the actual secret values on their machine.

### 2. Keep `.env` local and gitignored

Ensure the repository contains a `.gitignore` entry for `.env`:

```gitignore
.env
.eidos/
*.db
*.sqlite
*.pem
*.key
```

Never commit the local `.env` file.

### 3. Prefer environment variables at runtime

For Linux and macOS, run the app with:

```bash
export OPENAI_API_KEY="sk-..."
export TELEGRAM_BOT_TOKEN="..."
python3 eidos_main.py
```

Or source a local `.env` from your shell:

```bash
source .env
```

### 4. Use service environment configuration for production

For systemd, use an EnvironmentFile outside the repo:

```ini
[Service]
EnvironmentFile=/etc/eidos/eidos.env
ExecStart=/usr/bin/python3 /home/ser/EIDOS/eidos_main.py
```

Keep `/etc/eidos/eidos.env` private and do not add it to git.

### 5. GitHub Actions and CI

In CI, store values in GitHub Secrets rather than code.

- Go to `Settings > Secrets and variables > Actions`
- Add secrets like `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc.
- Reference them in workflows as `${{ secrets.OPENAI_API_KEY }}`

## Example variables to configure

Use the variable names your local setup requires. Common examples:

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `EIDOS_TELEGRAM_TOKEN`
- `EIDOS_TELEGRAM_ALLOWED`
- `EIDOS_TELEGRAM_ADMIN_ID`
- `EIDOS_TELEGRAM_CHAT_ID`
- `EIDOS_SECRET_KEY`
- `DISCORD_BOT_TOKEN`
- `MONGODB_URI`
- `DATABASE_URL`
- `OLLAMA_URL`
- `EIDOS_FAST_MODEL`
- `LINE_CHANNEL_SECRET`
- `ZALO_WEBHOOK_SECRET`
- `MATRIX_PASSWORD`

## Good practices

- Treat secrets as environment configuration, not code.
- Do not paste real tokens into issues, PR descriptions, screenshots, or public chat.
- Rotate or revoke any secret accidentally exposed immediately.

## What to do if a secret is exposed

1. Revoke or rotate the credential right away.
2. Remove it from local files and from any public logs.
3. Do not push the exposed secret back to GitHub.
4. If needed, create a new secret and update local configuration.

## Before publishing or sharing code publicly

1. Revoke any token/key that may have been exposed.
2. Delete state files or databases (`.eidos/`, `*.db`, `*.sqlite`).
3. Audit Git history for secrets (use `git-filter-repo` if needed).
4. Never upload models or personal data; use Releases or Git LFS if necessary.

## Secret management

- Keep credentials in environment variables or secrets services (GitHub Secrets
  for CI).
- Always provide `.env.example` without real values.
- The only supported secrets file is `~/.eidos/secrets.env` (`chmod 600`).

## Security contact

Report vulnerabilities to SER via Telegram: [https://t.me/ARKUu_12_8](https://t.me/ARKUu_12_8)

---

## Summary

This project should be public-ready with placeholders only.
Contributors must add secrets locally and keep them out of the repository.
