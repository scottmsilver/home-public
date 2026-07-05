# CLAUDE.md — home-public

**This repo is PUBLIC** (`github.com/scottmsilver/home-public`). Treat everything
committed here as world-readable, forever. Real per-deployment values live in the
**separate private `home-instance` repo** — never here.

## Never commit private/deployment details to this repo

Do not add — in code, comments, docs (`docs/`, `docs/superpowers/`), examples,
tests, or commit messages — any of:

- **Real domains/hostnames** — the deployment's actual domain or any subdomain.
  Use `example.com` (`home.example.com`, `home.i.example.com`, `auth.example.com`).
- **Real IPs** — LAN or container addresses. Use documentation ranges only:
  `192.168.1.10`, `10.0.0.x`, `127.0.0.1`.
- **Infrastructure IDs** — Cloudflare tunnel UUIDs, account IDs, zone IDs, KV
  namespace IDs. Use zeroed placeholders (`11111111-1111-1111-1111-111111111111`).
- **Personal emails / allow-list members** — use `you@gmail.com`, `person2@gmail.com`.
  The real `allowed_emails` list belongs in `home-instance`.
- **Secrets** — HMAC/broker secrets, session secrets, API keys, tokens. These are
  read from env vars or `~/.home/` at runtime and must never be checked in.

Config here is `home.toml.example` only — a template with placeholder values.
The filled-in `home.toml` (and anything with real values) stays in `home-instance`.

## Before every commit

Grep the staged diff for leaks. If any of these match with a *real* value
(not a placeholder), stop and replace it:

```bash
git diff --cached | grep -inE 'oursilverfamily|[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|@gmail\.com'
```

Allowed matches: `example.com`, documentation IPs above, zeroed UUIDs, and the
`you@…`/`person2@…` placeholders. Anything else is a leak — fix it before committing.

## If a leak already landed

Scrubbing the working tree is not enough — the value stays in git history and in
any GitHub PR diff. Full remediation: rewrite history with `git-filter-repo
--replace-text`/`--replace-message`, then recreate the repo fresh (deleting the
old one removes the leaky PR archives), then push. Keep a backup bundle first.
