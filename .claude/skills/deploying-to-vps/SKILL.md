---
name: deploying-to-vps
description: Use when deploying ClaudeTrading code changes from local machine to the production VPS. Covers git push, Docker rebuild, bootstrap, schema migration, and live agent memory updates.
---

# Deploying to VPS

## Overview

Code lives on local machine and GitHub. The VPS runs the Docker stack. Deployment = push to GitHub → pull on VPS → rebuild → bootstrap → (optional) agent update script.

**Docker Compose version:** The VPS uses Compose v2 — command is `docker compose` (space, no hyphen). `docker-compose` does not exist and will fail.

---

## Standard Deploy (code change only, no schema/tool/memory changes)

```bash
# Local
git push origin main

# VPS
ssh root@206.189.163.87
cd ~/ClaudeTrader
git pull
docker compose up -d --build     # rebuilds changed images + restarts in one step
```

---

## Deploy with Schema or Tool Changes (run bootstrap after rebuild)

```bash
# Local
git push origin main

# VPS
ssh root@206.189.163.87
cd ~/ClaudeTrader
git pull
docker compose up -d --build
docker compose exec scheduler python -m scheduler.bootstrap
```

Bootstrap is safe to re-run anytime:
- If `.agent_id` exists → skips agent creation, re-registers all tools only
- Schema changes (`ALTER TABLE`, new tables) are idempotent — wrapped in `try/except`
- **Does NOT insert `strategy_versions` v1 seed** when agent already exists — that only runs in the first-ever bootstrap. If you need the v1 row on an existing install, insert it manually (see below).

---

## Deploy with Live Agent Memory Update

When a feature adds new instructions Claude needs to know about, run the one-time update script after bootstrap. These scripts live in `scripts/one_time/`.

**PYTHONPATH is required** — scripts are not inside a package, so `import scheduler` fails without it:

```bash
docker compose exec -e PYTHONPATH=/app scheduler python scripts/one_time/<script>.py
```

---

## Inserting the v1 Strategy Versions Seed (existing installs only)

Bootstrap only seeds the v1 row during first-ever agent creation. On an existing install after adding the `strategy_versions` table, insert it manually:

```bash
docker compose exec -e PYTHONPATH=/app scheduler python3 -c "
from scheduler.tools.sqlite import _connect
from scheduler.agent import INITIAL_STRATEGY_DOC
meta = '## Version metadata\nversion: v1\nstatus: confirmed\npromote_after: 20\nbaseline_win_rate: null\nbaseline_avg_r: null\n\n'
conn = _connect()
conn.execute(\"INSERT OR IGNORE INTO strategy_versions (version, status, doc_text, promote_after) VALUES ('v1', 'confirmed', ?, 20)\", (meta + INITIAL_STRATEGY_DOC,))
conn.commit()
print('done')
conn.close()
"
```

---

## Verifying a Deploy

```bash
# All containers healthy
docker compose ps

# No import errors or crash loops
docker compose logs --tail=50 scheduler

# Schema present (run against scheduler, not letta)
docker compose exec scheduler python3 -c "
from scheduler.tools.sqlite import _connect
conn = _connect()
cols = [r[0] for r in conn.execute(\"SELECT name FROM pragma_table_info('trades')\")]
rows = conn.execute('SELECT version, status FROM strategy_versions').fetchall()
print('trades cols:', cols)
print('strategy_versions:', rows)
conn.close()
"
```

---

## Quick Reference

| Situation | Command |
|---|---|
| Code-only change | `git pull && docker compose up -d --build` |
| Tool signature changed | + `docker compose exec scheduler python -m scheduler.bootstrap` |
| Agent memory needs update | + `docker compose exec -e PYTHONPATH=/app scheduler python scripts/one_time/<script>.py` |
| Check logs | `docker compose logs -f scheduler` |
| Restart one service | `docker compose restart scheduler` |
| Check running containers | `docker compose ps` |

---

## Common Mistakes

| Mistake | Reality |
|---|---|
| Using `docker-compose` | VPS has Compose v2 — use `docker compose` (space) |
| Running one-time scripts without `PYTHONPATH=/app` | `ModuleNotFoundError: No module named 'scheduler'` |
| Assuming bootstrap seeds v1 on existing installs | It doesn't — v1 seed only runs in first-ever `create_new` path |
| Pushing to `master` | Repo uses `main` |
| Rebuilding with `docker compose build` then `up -d` separately | Use `docker compose up -d --build` — one command does both |
| Verifying schema via `letta` container | Use `scheduler` container — letta may not have sqlite3 |
