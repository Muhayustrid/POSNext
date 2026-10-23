# POS Next

Frappe/ERPNext app (`pos_next`) with a Vue 3 POS SPA in `POS/`. Origin fork: `Muhayustrid/POSNext`; upstream: `BrainWise-DEV/POSNext`.

@PROJECT_STATE.md

## Stack
- Backend: Frappe `>=16.21,<17`, ERPNext required, Python >=3.10, Ruff for lint/format.
- Frontend (`POS/`): Vue 3.5, Vite 5, Pinia, Tailwind 3, Vitest + Vue Test Utils, Biome for lint/format.
- README badges and CI still say v15; they are stale. Trust `pyproject.toml`.

## Layout
- `pos_next/api/`: whitelisted endpoints (e.g. `hq_monitoring.py`).
- `pos_next/services/`: domain logic (production, barcode, cash mode, recap, reject requests).
- `pos_next/pos_next/`: Frappe module: doctypes, Desk pages, reports, print formats, utils.
- `pos_next/patches/`: versioned patches registered in `patches.txt`.
- `pos_next/tests/`: backend regression tests; `tests/js/` for Desk page helpers.
- `POS/src/`: Vue SPA (components, Pinia stores, composables, `utils/offline`, `utils/print`).
- `pos_next/public/`: Vite build output. `pos_next/translations/`: translation CSVs.
- `docs/`: deploy, localization, offline sync, feature docs. `docs/agents/`: agent-skill config.
- HQ Sales Monitoring is a Frappe Desk page (`pos_next/pos_next/page/hq_sales_monitoring/`, frappe.Chart), NOT Vue.

## Run / test / build
- The bench runs in Docker: container `erpnext16_dev-frappe-1`, bench at `/workspace/development/frappe-bench`. Host `bench`/`python` do not work; use `docker exec`.
- Two sites: `posnext.localhost` (default; device IPs fall here) and `roti-posnext-test.localhost` (port 8001). Run `bench migrate` on both.
- Backend tests: run serially via `pos_next/_pn_run_tests.py` inside the container (set `FRAPPE_STREAM_LOGGING`).
- Frontend: `npm` runs inside the container too. In `POS/`: `npm run dev|build|lint|test:run`. Root `npm test` is a placeholder that always fails.
- Version bump: `./scripts/version-bump.sh [major|minor|patch]` updates `pos_next/__init__.py`, `POS/package.json`, root `package.json`.

## Deploy
`docs/DEPLOY_CHECKLIST.md`: confirm `main == origin/main`; frontend tests + build; deploy `main`; verify asset hash changed; `bench migrate` + restart; check indexes/locales/role fixtures; smoke-test shift open, sale, return, shift close. Rollback = previous commit + migrate.

## Conventions
- Branches: work happens on `main`. `develop` (origin default) intentionally differs; never sync it casually.
- Conventional commits with scope; descriptions in Indonesian.
- SPA list/grid/status layout rules: `docs/UI_GUIDELINES.md`.
- Translations: allowed languages come from POS Settings; new UI strings need CSV entries (`docs/LOCALIZATION.md`).

## Traps
- `pos_next/pos_next/custom/*.json` are desk mirrors. `hooks.py` fixtures export only Role + Custom DocPerm; custom fields come from `install.py`. Check a file's `sync_on_migrate` before assuming it applies.
- Offline invoices queue in IndexedDB with unique offline IDs, deduplicated client- and server-side (`docs/OFFLINE_SYNC.md`). Do not change ID handling casually.
- Printing: iMin logic in `POS/src/utils/print/imin_client.js`, routing in `transport.js`. Hardware dots/DPI are stable; SDK assumptions are not.
- NegativeStockError with stock visible in the grid usually means the site timezone is behind wall clock.
- HQ monitoring results are Redis-cached for 300 seconds, keyed by user, scope and filters.

## Agent skills

### Issue tracker

Issues and specs live as local markdown under `.scratch/<feature-slug>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary (needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix), recorded as a `Status:` line in each issue file. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
