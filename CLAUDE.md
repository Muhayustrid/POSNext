# Orchestration policy

You are the main orchestrator for this project. You have three custom subagents available via the Task tool: `explorer`, `implementer`, and `reviewer`, each pinned to a different model tier. Delegate instead of doing everything yourself.

## Delegation strategy

**`explorer` (haiku)** — repository exploration, finding files/symbols/references/call sites, tracing simple execution flow, searching configs and dependencies, gathering context before implementation. Do not spend `implementer` or `reviewer` budget on this.

**`implementer` (sonnet)** — the default coding worker: implementing features, fixing straightforward bugs, refactoring, writing tests, modifying UI, API integration, executing a clear implementation plan, independent implementation subtasks. If multiple implementation subtasks are genuinely independent, dispatch several `implementer` calls in parallel and say explicitly how many and how they're split (e.g. "run 3 implementer subagents in parallel, one per module") — Claude Code does not parallelize on a vague "if possible."

**`reviewer` (opus)** — difficult debugging, root-cause analysis, architecture decisions, complex cross-module reasoning, subtle correctness problems, concurrency/state consistency issues, security-sensitive changes, independent review of risky or important implementer output, and unresolved problems after implementer has failed or remains uncertain. Do not use for trivial tasks or routine implementation.

## Workflow for substantial tasks

1. Delegate exploration/context-gathering to `explorer`.
2. Form an implementation plan from the findings.
3. Delegate well-scoped implementation work to `implementer`.
4. Run `implementer` subagents in parallel when subtasks are independent — be explicit about the split.
5. Use `reviewer` for hard decisions, unresolved problems, or independent review of high-risk changes.
6. Integrate results yourself and verify the final state. Do not blindly trust a subagent's self-report on risky changes — inspect the diff yourself or route it through `reviewer`.

## Ground rules

- Cheapest capable tier first: explorer → implementer → reviewer. Escalate only when it's actually necessary.
- Before doing substantial exploration, implementation, debugging, or review yourself, ask: "would this be better delegated?" If yes, delegate.
- Don't delegate tiny tasks where delegation costs more than doing it directly.
- Don't spawn subagents just to look agentic, and don't duplicate the same work across agents unless independent verification is genuinely useful.
- Subagents cannot spawn their own subagents — all orchestration happens in this main session.
- You (the orchestrator) remain responsible for: task decomposition, choosing the right subagent, integrating results, final verification, and communicating the outcome to the user.

Apply this policy automatically, without being asked to use subagents each time.

---
# Project guide

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout

This is a Frappe app (`pos_next`) that plugs into an existing bench, plus a separate Vue 3 SPA.

```
pos_next/          Python backend (Frappe app): DocTypes, whitelisted API, overrides, patches
POS/               Vue 3 + Vite + Pinia + frappe-ui SPA. Own package.json, own node_modules.
pos_next/public/pos/   Vite build output. GITIGNORED — never edit by hand.
pos_next/www/pos.html  Frappe page that serves /pos. Written by the build. GITIGNORED.
docs/              Architecture write-ups: STARTUP_SEQUENCE, OFFLINE_SYNC, PRICING_AND_SUBMISSION,
                   OFFERS_AND_PROMOTIONS, VERSION_CONTROL, LOCALIZATION, Wallet-*.md
POS/.claude.md     Frontend conventions. Partly stale (see "API calls" below) — trust the code.
```

Version lives in three places that must stay in sync: `pos_next/__init__.py` (`__version__`),
`POS/package.json`, and `package.json`. Use `./scripts/version-bump.sh [major|minor|patch]`
rather than editing them individually.

## Environment: bench runs in Docker

The bench this app is installed into is a Docker install. **Host `bench` and `./env/bin/python`
do not work** — the venv symlinks point at paths that only exist in the container.

```bash
# Any bench command
docker exec erpnext16_dev-frappe-1 bash -lc \
  "cd /workspace/development/frappe-bench && bench --site erpnext16.localhost <command>"
```

Containers: `erpnext16_dev-frappe-1`, `erpnext16_dev-mariadb-1`, `erpnext16_dev-redis-cache-1`,
`erpnext16_dev-redis-queue-1`. Site: `erpnext16.localhost`. The host `apps/pos_next` directory is
bind-mounted to `/workspace/development/frappe-bench/apps/pos_next`, so host edits are live
immediately. Frappe/ERPNext in this bench are **v16**; `pyproject.toml` and CI still target
`version-15`.

Two consequences worth knowing:

- For a multi-statement debug script, write the `.py` **inside `apps/pos_next/`** (host `/tmp` is
  not the container `/tmp`) and run it with `env/bin/python` after `frappe.init(site=...)` /
  `frappe.connect()`.
- The host clock is WIB (UTC+7); the containers run `Etc/UTC`. See the stock trap below.

## Commands

All frontend commands run from `POS/`. Package manager is **yarn** — never npm.

```bash
# Frontend
yarn dev                                  # Vite on :8080, proxies /app|/api|/assets|/files|/printview to :8000
yarn build                                # vite build --base=/assets/pos_next/pos/ → pos_next/public/pos/
yarn lint                                 # biome check .            (frontend linter)
yarn lint:fix                             # biome check --write .
yarn test / yarn test:run                 # vitest — configured in package.json but NO test files exist yet

# Backend, through the container
docker exec erpnext16_dev-frappe-1 bash -lc "cd /workspace/development/frappe-bench && bench build --app pos_next"
docker exec erpnext16_dev-frappe-1 bash -lc "cd /workspace/development/frappe-bench && bench --site erpnext16.localhost migrate"
docker exec erpnext16_dev-frappe-1 bash -lc "cd /workspace/development/frappe-bench && bench --site erpnext16.localhost clear-cache"

# One Python test, no bench runner (the usual way to work here)
docker exec erpnext16_dev-frappe-1 bash -lc "cd /workspace/development/frappe-bench && \
  bench --site erpnext16.localhost execute pos_next.api.offers.<something>"      # ad-hoc call
docker exec erpnext16_dev-frappe-1 bash -lc "cd /workspace/development/frappe-bench && \
  ./env/bin/python -m unittest pos_next.test_packed_items_regression -v"        # mock-based suite
```

`pre-commit` is the gate for Python and legacy JS: `pip install pre-commit && pre-commit install`
from the app root. It runs ruff (import sort, lint, format) plus prettier and eslint on
js/vue/scss. Python style: tabs for indentation, line length 110, double quotes (`pyproject.toml`).
JS style: tabs, double quotes, semicolons omitted (`POS/biome.json`). `.editorconfig` also fixes
JSON at 2 spaces — doctype schema files are machine-generated, keep them that way.

Do not rely on `bench run-tests` against a dev site with demo data loaded: ERPNext's test
bootstrap dies with `DuplicateEntryError: ('Price List', 'Standard Buying')`. Run a single
`unittest`/`FrappeTestCase` module directly instead, or assert via `bench execute`.

## Architecture

### One server call boots the app

`pos_next/api/bootstrap.py:get_initial_data` returns locale, precision, current shift, POS
Profile, POS Settings, and payment methods in a single round trip. `POS/src/main.js` runs
CSRF-token acquisition and user resolution **in parallel**, mounts the app, then preloads
bootstrap non-blockingly. Precision settings are consumed by `utils/currency.js#initPrecision`
before any price is formatted — if you add a setting the UI needs at first paint, it belongs in
bootstrap, not in a per-component `createResource`.

Socket.IO (`POS/src/socket.js`, emitting side `pos_next/realtime_events.py`) pushes live stock
and POS Profile updates. `socket.js` imports `../../../../sites/common_site_config.json` by
relative path, so the Vite config only resolves when the app lives at `apps/pos_next` inside a
bench.

### Frontend state lives in Pinia stores, not components

`POS/src/stores/` holds the domain logic (`posCart`, `itemSearch`, `posOffers`, `posShift`,
`posSync`, `stock`, `bootstrap`); `POS/src/composables/` holds view-facing helpers. The three
routes are `POSSale`, `Login`, and a catch-all — this is not a many-page app, `pages/POSSale.vue`
composes everything else. Expect large files (`PaymentDialog.vue` ~3.5k lines, `POSSale.vue`
~3k, `itemSearch.js` ~2.3k); extract into stores/composables rather than adding page routes.

### API calls: use the wrapper

`POS/.claude.md` says JS utilities should use `window.frappe.call`. That is dead — there are
**zero** `window.frappe.call` sites in `POS/src`. The actual convention:

- `.vue` files and components: `createResource` from `frappe-ui` (reactive loading/error state).
- Everything else (`stores/`, `composables/`, `utils/`):
  `import { call } from "@/utils/apiWrapper"`.

`utils/apiWrapper.js` wraps `frappe-ui`'s `call` and retries once after force-refreshing the CSRF
token, which is what keeps long-lived cashier sessions from failing on token expiry. Going around
it re-introduces that failure. `main.js` additionally installs a CSRF-aware
`resourceFetcher`, so `createResource` gets the same retry.

User-facing notifications go through `useToast()` (`showSuccess`/`showError`/`showWarning`).
Never `window.frappe.msgprint` and never frappe-ui's `toast.create`.

All user-visible strings must be wrapped in `__('...')`, with variables as
`__('Text {0} and {1}.', [a, b])` — no template literals inside `__`, no split/concatenated
strings, no multi-line strings. Plurals are one full `__()` call per form. Translations live in
`pos_next/translations/{ar,id,pt-br}.csv`.

### Offline is worker-only, cache-first

Every IndexedDB operation runs in `POS/src/workers/offline.worker.js` behind
`POS/src/utils/offline/workerClient.js` (`offlineWorker.*`). Touching Dexie (`utils/offline/db.js`)
from the main thread blocks the UI and is a bug. `workerClient` also receives the CSRF token from
`main.js` so the worker can make authenticated API calls itself.

Loading order is always cache-first: check `isOffline()` or `await offlineWorker.isCacheReady()`
before deciding between cache and server. A `createResource({ auto: true })` that must render
offline is wrong; pre-warm the cache while online
(`cacheItemsFromServer` → `offlineWorker.cacheItems`).

The DB schema is the `CURRENT_SCHEMA` object in `db.js` — it auto-hashes and version-bumps, so
adding a field there is the whole migration. Index syntax: `&` unique, `++` autoincrement,
`*` multi-entry, `[a+b]` compound. Dexie cannot key on booleans: query `synced` with
`.filter(x => x.synced === false)`, never `.where('synced').equals(false)`.

Deduplication across sync retries is the `offline_id` UUID: offline invoices carry it, the
`Offline Invoice Sync` DocType holds it uniquely, and `submit_invoice` /
`check_offline_invoice_synced` consult it before creating anything. Touch that path with the
"offline invoice created twice" failure mode in mind (`docs/OFFLINE_SYNC.md`).

### Pricing: client computes, server re-verifies

The golden rule is that POS keeps `price_list_rate` as the untouched original and derives
`rate`, `discount_percentage`, `discount_amount` separately, so the UI can show "was X, now Y" and
reports stay accurate. Tax-inclusive extraction and discount stacking are documented in
`docs/PRICING_AND_SUBMISSION.md`. `api/invoices.py` re-validates every number on submit
(`validate_cart_items`, `submit_invoice`) — never trust a client total, and never let a client
field overwrite a server-managed one (`_strip_server_managed_fields`).

POS Next creates **Sales Invoice**, not POS Invoice — there are zero `POS Invoice` references in
`api/invoices.py`, and `doctype="Sales Invoice"` is the default threaded through the APIs.

The offers engine is layered on top of ERPNext's own Pricing Rule / Promotional Scheme machinery,
not a parallel implementation: `api/offers.py` reads schemes and standalone rules,
`api/promotions.py` evaluates them, `POS Offer` / `POS Coupon` are POS Next DocTypes, and
`pos_only=1` on a Pricing Rule restricts it to POS transactions. `docs/OFFERS_AND_PROMOTIONS.md`
covers the store side (`posOffers.js` eligibility + `posCart.js` application). Offers that need
per-customer limits are tracked by `One Time Customer Offer Usage`, whose usage is recorded on
`on_submit` and released on `on_cancel`.

### Pricing-rule overrides are monkeypatches, and that is load-bearing

ERPNext's pricing code is made of module-level functions, which Frappe has no hook system for
(`override_doctype_class` covers classes; `override_whitelisted_methods` covers only whitelisted
HTTP endpoints). So `pos_next/__init__.py` patches `get_other_conditions` and replaces
`apply_price_discount_rule` **at import time**, wrapped in try/except so a changed ERPNext
internal degrades instead of crashing the app. When behavior around discounts differs from what
`pos_next/overrides/pricing_rule.py` appears to do, check whether the patch actually applied.

`install.py` has a similar defensive structure: `POS Settings` is a DocType both ERPNext and POS
Next ship. ERPNext's sync runs *after* ours during `bench migrate` and wins, so
`reclaim_pos_settings_doctype` drops ERPNext's table and reinstalls ours from `after_migrate`.
It is idempotent and must stay that way.

### Custom Fields: `custom/*.json` is a mirror, not a mechanism

`pos_next/pos_next/custom/*.json` (sales_invoice, customer, pos_profile, …) looks like a fixture
directory but nothing reads it. `hooks.py` `fixtures` exports only `Role` and `Custom DocPerm`.
Fields like `Sales Invoice.posa_pos_opening_shift` exist in the DB only because someone created
them in Desk. A fresh site therefore comes up **without** them, and the failure surfaces at first
sale.

When adding a Custom Field, create it from code — an upsert in `install.py` called from
`after_install` and `after_migrate` — and treat the JSON as a review artifact. `install.py`'s
docstring claiming fixtures carry custom fields and print formats is wrong. Also note a Custom
Field cannot carry a unique index, so uniqueness has to come from construction.

### Guard optional ERPNext fields

Cross-version drift is a recurring source of crashes here. Check before reading fields that may
not exist on the installed ERPNext:

```python
if hasattr(pos_profile_doc, "customer_group") and pos_profile_doc.customer_group:
    ...
frappe.db.table_exists("POS Coupon")   # before any query against an app-owned table
```

Likewise, `POS Payment Method` has **no** `default_account` column on v16 (fields are `default`,
`mode_of_payment`, `allow_in_returns`), so `Unknown column 'default_account'` errors mean a query
assumed the wrong table. The correct source is `Mode of Payment Account`
(`{"parent": mode_of_payment, "company": company}` → `default_account`), which is what the first
branch of `api/invoices.py#get_payment_account` does; its later fallbacks still select
`ppm.default_account` from `tabPOS Payment Method` and fail on this version.

### Backend map

`api/` is the whitelisted surface, one module per domain (`invoices`, `items`, `promotions`,
`pos_profile`, `shifts`, `wallet`, `credit_sales`, `partial_payments`, `offers`, `branding`,
`localization`, `qz`, `auth`, `bootstrap`). Shared field lists and defaults live in
`api/constants.py` because `bootstrap.py` and `pos_profile.py` must agree.
`api/sales_invoice_hooks.py` is doc-event code, not an endpoint.

DocTypes are all in one module (`pos_next/pos_next/`, `modules.txt` = `POS Next`) and cluster
into: shift lifecycle (`pos_opening_shift`, `pos_closing_shift` + details), promotions
(`pos_offer`, `pos_coupon`, `one_time_customer_offer_usage`), money (`wallet`,
`wallet_transaction`, `bank_deposits`, `partial_payments`), offline sync
(`offline_invoice_sync`, `sales_invoice_reference`, `pos_payment_entry_reference`), config
(`pos_settings`, `pos_barcode_rules`, `pos_allowed_locale`), Egypt locale (`governorate`,
`district`), and `brainwise_branding` (license/branding enforcement, monitored by the hourly
`tasks/branding_monitor.py` scheduler job).

Python tests come in two flavors: mock-only `unittest.TestCase` (`api/test_offers.py`,
`test_packed_items_regression.py`) which run anywhere, and DB-backed `FrappeTestCase`
(`test_promotions.py`, `api/test_items.py`) which need a live site plus ERPNext demo company/
warehouse. `test_promotions.py` is the CI suite (`.github/workflows/ci.yml`) and prefixes all
data `_PNXT_TEST_` so it can clean up. Prefer constructing your own fixtures over assuming
existing records.

## Debugging traps

- **`NegativeStockError` while the item grid shows stock.** The grid reads `tabBin`; invoice submit
  sums `tabStock Ledger Entry` only up to server "now". If the site timezone is behind the
  wall clock that posted the receipt, the receipt is future-dated and contributes zero, so
  available quantity is 0. Diagnose by comparing
  `frappe.db.get_value('Bin', {...}, 'actual_qty')` against
  `erpnext.stock.utils.get_stock_balance(item, warehouse)`; if they disagree, check
  System Settings → Time Zone before touching code or enabling negative stock.
- **Two `api` modules with similar names.** `api/offers.py` (fetches/serializes rules and schemes)
  and `api/promotions.py` (evaluates eligibility and applies discounts). `POS Offer` is a DocType;
  `posOffers.js` is the frontend store.
- **A stale built bundle.** `pos_next/public/pos/` and `pos_next/www/pos.html` are generated; if
  the served UI doesn't match source, rebuild rather than edit output.
- **`ignore_csrf: 1` in `site_config.json`** makes CSRF errors disappear locally and reappear in
  production. Keep it off unless actively debugging the dev-server proxy, and verify any auth
  change with the token check enabled.
