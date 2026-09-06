# POS Production Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give POS cashiers a guided "Production" dialog (sidebar + mobile profile menu) that consumes materials and produces a finished item through a server-validated Stock Entry Manufacture plus a POS Production Log row.

**Architecture:** Recipes live in a new `POS Production Recipe` doctype (multi-company child table). A new API module `pos_next/api/production.py` derives company/warehouse from the POS Profile server-side, pre-validates stock, submits the Stock Entry and the Log in one transaction. The Vue dialog auto-scales materials from the recipe and lets the cashier edit quantities; batch UI appears only for batch-tracked items.

**Tech Stack:** Frappe 16.32 / ERPNext 16.33, unittest via `_pn_run_tests.py` (site `erpnext16.localhost`), Vue 3 + frappe-ui, vitest, vite build.

**Spec:** `docs/superpowers/specs/2026-09-05-pos-production-design.md` (same branch)

## Global Constraints

- **Work ONLY inside the worktree** `/Users/rotiropi/ERPNext-Project/development/frappe-bench/apps/pos_next/.worktrees/feat-pos-production` (branch `feat/pos-production`). The main checkout at `apps/pos_next` is on `feat/pos-offer-2` and belongs to another agent — NEVER edit, commit, or switch branches there.
- Tests are **serial only** (parallel runs deadlock per `_pn_run_tests.py` docstring).
- Quantities are in the item's **stock UOM** everywhere (recipe rows, dialog, Stock Entry).
- Company and warehouse are always derived **server-side from `pos_profile`** — never accepted from the client.
- Commit style: `feat(pos-production): ...` / `test(pos-production): ...` / `fix(pos-production): ...`, matching repo history.
- No new frontend dependencies. Reuse `frappe-ui` components and existing composables.
- Do not modify any file under `pos_next/api/offers.py`, `promotions.py`, `discount_restriction*`, `overrides/pricing_rule.py`, `overrides/pos_offer_*`, or doctypes `pos_offer*` (other agent's territory).

All paths below are relative to the worktree root unless prefixed with `env/` or `apps/` (bench-relative).

Test runner invocation (from the bench root `/Users/rotiropi/ERPNext-Project/development/frappe-bench`):

```bash
./env/bin/python apps/pos_next/.worktrees/feat-pos-production/pos_next/_pn_run_tests.py [--sync] <modules...>
```

---

### Task 1: Make `_pn_run_tests.py` worktree-aware

The runner computes `BENCH_ROOT` as three levels up from its own file. Inside the worktree (`apps/pos_next/.worktrees/feat-pos-production/pos_next/`) that lands in the wrong place, and new doctypes need a sync step before tests can see them.

**Files:**
- Modify: `pos_next/_pn_run_tests.py` (worktree copy only)

**Interfaces:**
- Produces: `./env/bin/python apps/pos_next/.worktrees/feat-pos-production/pos_next/_pn_run_tests.py [--sync] <modules...>` — exits 0 on pass; `--sync` runs `frappe.model.sync.sync_for("pos_next", force=1)` after connect.

- [ ] **Step 1: Update BENCH_ROOT resolution and add --sync**

Replace the block

```python
SITE = "erpnext16.localhost"
# frappe.init resolves sites/ relative to the cwd, so anchor at the bench root
# (three levels up from apps/pos_next/pos_next/).
BENCH_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
```

with

```python
SITE = "erpnext16.localhost"
# frappe.init resolves sites/ relative to the cwd, so anchor at the bench root.
# In the main checkout that is three levels up; in a worktree under
# .worktrees/<name>/ the depth differs, so walk up until a sites/ dir appears.
_APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _find_bench_root(start):
	cur = start
	while cur != os.path.dirname(cur):
		if os.path.isdir(os.path.join(cur, "sites")) and os.path.isdir(os.path.join(cur, "apps")):
			return cur
		cur = os.path.dirname(cur)
	raise SystemExit("could not locate bench root above %s" % start)


BENCH_ROOT = _find_bench_root(_APP_DIR)
APP_ROOT = _APP_DIR
```

and in `main()`, change the signature and the connect block to:

```python
def main(module_names, sync=False):
	if not module_names:
		print(__doc__, file=sys.stderr)
		return 2

	os.chdir(BENCH_ROOT)

	script_dir = os.path.dirname(os.path.abspath(__file__))
	sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != script_dir]
	if APP_ROOT not in sys.path:
		sys.path.insert(0, APP_ROOT)

	frappe.init(site=SITE, sites_path=SITES_PATH)
	frappe.connect()
	frappe.flags.in_test = True

	if sync:
		from frappe.model.sync import sync_for

		sync_for("pos_next", force=1)
```

and the entry point at the bottom to:

```python
if __name__ == "__main__":
	args = [a for a in sys.argv[1:] if a != "--sync"]
	raise SystemExit(main(args, sync="--sync" in sys.argv[1:]))
```

- [ ] **Step 2: Verify the worktree code path loads**

Run from the bench root:

```bash
./env/bin/python -c "
import sys
sys.path.insert(0, 'apps/pos_next/.worktrees/feat-pos-production')
import pos_next
assert '.worktrees/feat-pos-production' in pos_next.__file__, pos_next.__file__
print('OK', pos_next.__file__)
"
```

Expected: `OK .../apps/pos_next/.worktrees/feat-pos-production/pos_next/__init__.py`. If it prints the main checkout path, STOP and fix sys.path precedence before continuing (the worktree path must be first).

- [ ] **Step 3: Run an existing test module through the worktree runner**

```bash
./env/bin/python apps/pos_next/.worktrees/feat-pos-production/pos_next/_pn_run_tests.py pos_next.api.test_printing
```

Expected: all tests pass or skip (same result as the main-checkout runner — proves harness parity). `--sync` is exercised in Task 2.

- [ ] **Step 4: Commit**

```bash
git add pos_next/_pn_run_tests.py
git commit -m "feat(pos-production): worktree-aware test runner with doctype sync flag"
```

---

### Task 2: Doctypes — Recipe, children, Log

**Files:**
- Create: `pos_next/pos_next/doctype/pos_production_recipe/{__init__.py,pos_production_recipe.json,pos_production_recipe.py}`
- Create: `pos_next/pos_next/doctype/pos_production_recipe_item/{__init__.py,pos_production_recipe_item.json,pos_production_recipe_item.py}`
- Create: `pos_next/pos_next/doctype/pos_production_recipe_company/{__init__.py,pos_production_recipe_company.json,pos_production_recipe_company.py}`
- Create: `pos_next/pos_next/doctype/pos_production_log/{__init__.py,pos_production_log.json,pos_production_log.py}`
- Test: `pos_next/api/test_production.py`

**Interfaces:**
- Produces: DocTypes `POS Production Recipe` (fields `recipe_name`, `production_item`, `output_qty`, `disabled`, tables `items`, `companies`), `POS Production Recipe Item` (`item_code`, `qty`), `POS Production Recipe Company` (`company`, `enabled`), `POS Production Log` (submittable; `recipe`, `production_item`, `qty`, `items_used`, `stock_entry`, `pos_profile`, `company`). Later tasks rely on exactly these fieldnames.

- [ ] **Step 1: Write the failing smoke test**

Create `pos_next/api/test_production.py`:

```python
# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import frappe
from frappe.tests.utils import FrappeTestCase


class TestProductionDoctypes(FrappeTestCase):
	def test_doctypes_and_key_fields_exist(self):
		for doctype, fields in [
			("POS Production Recipe", ["recipe_name", "production_item", "output_qty", "disabled", "items", "companies"]),
			("POS Production Recipe Item", ["item_code", "qty"]),
			("POS Production Recipe Company", ["company", "enabled"]),
			("POS Production Log", ["recipe", "production_item", "qty", "items_used", "stock_entry", "pos_profile", "company"]),
		]:
			meta = frappe.get_meta(doctype)
			for fieldname in fields:
				self.assertTrue(meta.has_field(fieldname), f"{doctype} missing {fieldname}")

	def test_log_is_submittable(self):
		self.assertTrue(frappe.get_meta("POS Production Log").is_submittable)
```

- [ ] **Step 2: Run it and verify it fails**

```bash
./env/bin/python apps/pos_next/.worktrees/feat-pos-production/pos_next/_pn_run_tests.py pos_next.api.test_production
```

Expected: ImportError/does-not-exist failure (doctypes not synced yet).

- [ ] **Step 3: Create the doctype files**

`pos_production_recipe.json`:

```json
{
 "actions": [],
 "autoname": "field:recipe_name",
 "creation": "2026-09-05 00:00:00.000000",
 "doctype": "DocType",
 "engine": "InnoDB",
 "field_order": [
  "section_main",
  "recipe_name",
  "production_item",
  "column_break_main",
  "output_qty",
  "disabled",
  "section_items",
  "items",
  "section_companies",
  "companies"
 ],
 "fields": [
  {"fieldname": "section_main", "fieldtype": "Section Break", "label": "Recipe"},
  {
   "fieldname": "recipe_name",
   "fieldtype": "Data",
   "in_list_view": 1,
   "label": "Recipe Name",
   "reqd": 1,
   "unique": 1
  },
  {
   "fieldname": "production_item",
   "fieldtype": "Link",
   "in_list_view": 1,
   "in_standard_filter": 1,
   "label": "Production Item",
   "options": "Item",
   "reqd": 1
  },
  {"fieldname": "column_break_main", "fieldtype": "Column Break"},
  {
   "default": "1",
   "description": "Finished goods quantity produced by one run of this recipe (stock UOM)",
   "fieldname": "output_qty",
   "fieldtype": "Float",
   "label": "Output Qty",
   "reqd": 1
  },
  {"default": "0", "fieldname": "disabled", "fieldtype": "Check", "label": "Disabled"},
  {"fieldname": "section_items", "fieldtype": "Section Break", "label": "Materials"},
  {
   "fieldname": "items",
   "fieldtype": "Table",
   "label": "Materials",
   "options": "POS Production Recipe Item",
   "reqd": 1
  },
  {"fieldname": "section_companies", "fieldtype": "Section Break", "label": "Companies"},
  {
   "description": "Recipe applies in every company listed here",
   "fieldname": "companies",
   "fieldtype": "Table",
   "label": "Companies",
   "options": "POS Production Recipe Company",
   "reqd": 1
  }
 ],
 "index_web_pages_for_search": 1,
 "is_submittable": 0,
 "links": [],
 "modified": "2026-09-05 00:00:00.000000",
 "modified_by": "Administrator",
 "module": "Pos Next",
 "name": "POS Production Recipe",
 "owner": "Administrator",
 "permissions": [
  {"role": "System Manager", "read": 1, "write": 1, "create": 1, "delete": 1, "submit": 1, "cancel": 1, "report": 1, "email": 1, "print": 1, "share": 1, "export": 1},
  {"role": "Nexus POS Manager", "read": 1, "write": 1, "create": 1, "delete": 1, "report": 1, "email": 1, "print": 1, "share": 1},
  {"role": "Sales Manager", "read": 1, "report": 1, "email": 1, "print": 1}
 ],
 "sort_field": "modified",
 "sort_order": "DESC",
 "states": []
}
```

`pos_production_recipe.py`:

```python
# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class POSProductionRecipe(Document):
	def validate(self):
		self.validate_items_not_finished_item()

	def validate_items_not_finished_item(self):
		for row in self.items:
			if row.item_code == self.production_item:
				frappe.throw(
					_("Material {0} is the production item itself").format(row.item_code)
				)
```

Add `import frappe` and `from frappe import _` at the top of that file.

`pos_production_recipe_item.json`:

```json
{
 "actions": [],
 "creation": "2026-09-05 00:00:00.000000",
 "doctype": "DocType",
 "editable_grid": 1,
 "engine": "InnoDB",
 "field_order": ["item_code", "qty", "stock_uom"],
 "fields": [
  {"fieldname": "item_code", "fieldtype": "Link", "in_list_view": 1, "label": "Item", "options": "Item", "reqd": 1},
  {"default": "1", "fieldname": "qty", "fieldtype": "Float", "in_list_view": 1, "label": "Qty (stock UOM)", "reqd": 1},
  {"fieldname": "stock_uom", "fieldtype": "Data", "in_list_view": 1, "label": "Stock UOM", "read_only": 1}
 ],
 "index_web_pages_for_search": 1,
 "istable": 1,
 "links": [],
 "modified": "2026-09-05 00:00:00.000000",
 "modified_by": "Administrator",
 "module": "Pos Next",
 "name": "POS Production Recipe Item",
 "owner": "Administrator",
 "permissions": [],
 "sort_field": "modified",
 "sort_order": "DESC",
 "states": []
}
```

`pos_production_recipe_item.py`:

```python
# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class POSProductionRecipeItem(Document):
	pass
```

`pos_production_recipe_company.json`:

```json
{
 "actions": [],
 "creation": "2026-09-05 00:00:00.000000",
 "doctype": "DocType",
 "editable_grid": 1,
 "engine": "InnoDB",
 "field_order": ["company", "enabled"],
 "fields": [
  {"fieldname": "company", "fieldtype": "Link", "in_list_view": 1, "label": "Company", "options": "Company", "reqd": 1},
  {"default": "1", "fieldname": "enabled", "fieldtype": "Check", "in_list_view": 1, "label": "Enabled"}
 ],
 "index_web_pages_for_search": 1,
 "istable": 1,
 "links": [],
 "modified": "2026-09-05 00:00:00.000000",
 "modified_by": "Administrator",
 "module": "Pos Next",
 "name": "POS Production Recipe Company",
 "owner": "Administrator",
 "permissions": [],
 "sort_field": "modified",
 "sort_order": "DESC",
 "states": []
}
```

`pos_production_recipe_company.py`:

```python
# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class POSProductionRecipeCompany(Document):
	pass
```

`pos_production_log.json`:

```json
{
 "actions": [],
 "autoname": "format:PPLOG-{YY}{MM}-{#####}",
 "creation": "2026-09-05 00:00:00.000000",
 "doctype": "DocType",
 "engine": "InnoDB",
 "field_order": [
  "recipe",
  "production_item",
  "column_break_1",
  "qty",
  "section_items",
  "items_used",
  "section_refs",
  "stock_entry",
  "column_break_2",
  "pos_profile",
  "company"
 ],
 "fields": [
  {"fieldname": "recipe", "fieldtype": "Link", "in_list_view": 1, "in_standard_filter": 1, "label": "Recipe", "options": "POS Production Recipe", "reqd": 1, "search_index": 1},
  {"fieldname": "production_item", "fieldtype": "Link", "in_list_view": 1, "label": "Production Item", "options": "Item", "reqd": 1},
  {"fieldname": "column_break_1", "fieldtype": "Column Break"},
  {"fieldname": "qty", "fieldtype": "Float", "in_list_view": 1, "label": "Qty Produced", "reqd": 1},
  {"fieldname": "section_items", "fieldtype": "Section Break", "label": "Materials Used"},
  {"description": "JSON snapshot of the materials actually consumed (cashier may have edited the recipe)",
   "fieldname": "items_used", "fieldtype": "Long Text", "label": "Items Used"},
  {"fieldname": "section_refs", "fieldtype": "Section Break", "label": "References"},
  {"fieldname": "stock_entry", "fieldtype": "Link", "in_list_view": 1, "in_standard_filter": 1, "label": "Stock Entry", "options": "Stock Entry", "read_only": 1, "reqd": 1, "search_index": 1},
  {"fieldname": "column_break_2", "fieldtype": "Column Break"},
  {"fieldname": "pos_profile", "fieldtype": "Link", "label": "POS Profile", "options": "POS Profile", "read_only": 1},
  {"fieldname": "company", "fieldtype": "Link", "in_standard_filter": 1, "label": "Company", "options": "Company", "read_only": 1}
 ],
 "index_web_pages_for_search": 1,
 "is_submittable": 1,
 "links": [],
 "modified": "2026-09-05 00:00:00.000000",
 "modified_by": "Administrator",
 "module": "Pos Next",
 "name": "POS Production Log",
 "owner": "Administrator",
 "permissions": [
  {"role": "System Manager", "read": 1, "write": 1, "create": 1, "delete": 1, "submit": 1, "cancel": 1, "amend": 1, "report": 1, "email": 1, "print": 1, "share": 1, "export": 1},
  {"role": "Nexus POS Manager", "read": 1, "write": 1, "create": 1, "delete": 1, "submit": 1, "cancel": 1, "report": 1, "email": 1, "print": 1, "share": 1, "export": 1},
  {"role": "POSNext Cashier", "read": 1, "create": 1, "submit": 1, "print": 1}
 ],
 "sort_field": "modified",
 "sort_order": "DESC",
 "states": []
}
```

`pos_production_log.py`:

```python
# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class POSProductionLog(Document):
	pass
```

Every doctype folder also needs an empty `__init__.py` (copy the pattern from `pos_next/pos_next/doctype/pos_coupon/__init__.py`).

- [ ] **Step 4: Sync and run the smoke test**

```bash
./env/bin/python apps/pos_next/.worktrees/feat-pos-production/pos_next/_pn_run_tests.py --sync pos_next.api.test_production
```

Expected: `test_doctypes_and_key_fields_exist` PASS, `test_log_is_submittable` PASS. If sync_for reports errors, fix the JSONs (common causes: missing `module`, wrong `"module": "Pos Next"`, duplicate fieldnames).

- [ ] **Step 5: Commit**

```bash
git add pos_next/pos_next/doctype/pos_production_* pos_next/api/test_production.py
git commit -m "feat(pos-production): recipe and log doctypes with multi-company scope"
```

---

### Task 3: API — `get_production_recipes`

**Files:**
- Create: `pos_next/api/production.py`
- Modify: `pos_next/api/test_production.py` (append test class)

**Interfaces:**
- Consumes: doctypes from Task 2; `erpnext.stock.doctype.batch.batch.get_batch_qty`.
- Produces: `get_production_recipes(pos_profile)` →
  `{"pos_profile": str, "company": str, "warehouse": str, "recipes": [{"name", "recipe_name", "production_item", "production_item_name", "output_qty", "fg_stock": float, "fg_has_batch_no": bool, "items": [{"item_code", "item_name", "qty", "stock_uom", "has_batch_no", "available_qty": float, "batches": [{"batch_no", "qty", "expiry_date"}]}]}]}` — Task 5's dialog consumes exactly these keys.

- [ ] **Step 1: Write the failing tests**

Append to `pos_next/api/test_production.py`:

```python
import uuid

from pos_next.api.production import get_production_recipes


def _make_test_item(code_suffix=""):
	code = f"PRD-T-{uuid.uuid4().hex[:8]}{code_suffix}"
	frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": code,
			"item_name": code,
			"item_group": frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups",
			"stock_uom": "Nos",
		}
	).insert(ignore_permissions=True)
	return code


class TestGetProductionRecipes(FrappeTestCase):
	def setUp(self):
		self.pos_profile = frappe.db.get_value("POS Profile", {"disabled": 0}, "name")
		if not self.pos_profile:
			self.skipTest("no POS Profile on this site")
		self.company, self.warehouse = frappe.db.get_value(
			"POS Profile", self.pos_profile, ["company", "warehouse"]
		)
		if not self.warehouse:
			self.warehouse = frappe.db.get_value(
				"Warehouse", {"company": self.company, "is_group": 0, "disabled": 0}, "name"
			)
		self.fg = _make_test_item()
		self.mat = _make_test_item()
		self.other_company = frappe.db.get_value(
			"Company", {"name": ["!=", self.company]}, "name"
		)
		if not self.other_company:
			self.skipTest("only one company on this site")

	def _make_recipe(self, companies):
		return frappe.get_doc(
			{
				"doctype": "POS Production Recipe",
				"recipe_name": f"Recipe {uuid.uuid4().hex[:6]}",
				"production_item": self.fg,
				"output_qty": 5,
				"items": [{"item_code": self.mat, "qty": 2}],
				"companies": [{"company": c, "enabled": 1} for c in companies],
			}
		).insert(ignore_permissions=True)

	def test_lists_enabled_recipes_for_profile_company(self):
		self._make_recipe([self.company])
		self._make_recipe([self.other_company])
		payload = get_production_recipes(self.pos_profile)
		self.assertEqual(payload["company"], self.company)
		ours = [r for r in payload["recipes"] if r["production_item"] == self.fg]
		self.assertEqual(len(ours), 1)
		recipe = ours[0]
		self.assertEqual(recipe["output_qty"], 5)
		self.assertEqual(len(recipe["items"]), 1)
		row = recipe["items"][0]
		self.assertEqual(row["item_code"], self.mat)
		self.assertEqual(row["qty"], 2)
		self.assertEqual(row["stock_uom"], "Nos")
		self.assertIn("available_qty", row)
		self.assertIn("has_batch_no", row)
		self.assertIn("batches", row)
		self.assertIn("fg_stock", recipe)
		self.assertIn("fg_has_batch_no", recipe)

	def test_disabled_recipe_excluded(self):
		name = self._make_recipe([self.company])
		frappe.db.set_value("POS Production Recipe", name, "disabled", 1)
		payload = get_production_recipes(self.pos_profile)
		self.assertFalse(any(r["name"] == name for r in payload["recipes"]))

	def test_disabled_company_row_excluded(self):
		name = self._make_recipe([self.company, self.other_company])
		# disable the profile-company row; recipe must disappear for this company
		doc = frappe.get_doc("POS Production Recipe", name)
		for row in doc.companies:
			if row.company == self.company:
				row.enabled = 0
		doc.save(ignore_permissions=True)
		payload = get_production_recipes(self.pos_profile)
		self.assertFalse(any(r["name"] == name for r in payload["recipes"]))
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
./env/bin/python apps/pos_next/.worktrees/feat-pos-production/pos_next/_pn_run_tests.py pos_next.api.test_production
```

Expected: `ImportError: cannot import name 'get_production_recipes'`.

- [ ] **Step 3: Implement `pos_next/api/production.py`**

```python
# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt
from erpnext.stock.doctype.batch.batch import get_batch_qty


def _resolve_profile(pos_profile):
	"""Company + warehouse come from the POS Profile server-side, never the client."""
	if not pos_profile:
		frappe.throw(_("POS Profile is required"))
	company, warehouse = frappe.db.get_value("POS Profile", pos_profile, ["company", "warehouse"])
	if not company:
		frappe.throw(_("POS Profile {0} has no company").format(pos_profile))
	if not warehouse:
		warehouse = frappe.db.get_value(
			"Warehouse", {"company": company, "is_group": 0, "disabled": 0}, "name"
		)
		if not warehouse:
			frappe.throw(_("No warehouse found for company {0}").format(company))
	return company, warehouse


def _item_flags(item_codes):
	"""item_code -> {item_name, stock_uom, has_batch_no} for the given codes."""
	if not item_codes:
		return {}
	rows = frappe.get_all(
		"Item",
		filters={"name": ["in", item_codes]},
		fields=["name", "item_name", "stock_uom", "has_batch_no"],
	)
	return {r.name: r for r in rows}


def _stock_qty(item_code, warehouse):
	bin_qty = frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "actual_qty")
	return flt(bin_qty)


@frappe.whitelist()
def get_production_recipes(pos_profile):
	"""Enabled recipes for the profile's company, with stock/batch info per material."""
	try:
		company, warehouse = _resolve_profile(pos_profile)

		recipes = frappe.get_all(
			"POS Production Recipe",
			filters={"disabled": 0},
			fields=["name", "recipe_name", "production_item", "output_qty"],
			order_by="recipe_name",
		)
		if not recipes:
			return {"pos_profile": pos_profile, "company": company, "warehouse": warehouse, "recipes": []}

		# multi-company scope: keep recipes with an enabled company row for this company
		scoped = {
			r.parent
			for r in frappe.get_all(
				"POS Production Recipe Company",
				filters={"parenttype": "POS Production Recipe", "company": company, "enabled": 1},
				pluck="parent",
			)
		}
		recipes = [r for r in recipes if r.name in scoped]

		item_rows = frappe.get_all(
			"POS Production Recipe Item",
			filters={"parenttype": "POS Production Recipe", "parent": ["in", [r.name for r in recipes]]},
			fields=["parent", "item_code", "qty"],
		)
		by_recipe = {}
		for row in item_rows:
			by_recipe.setdefault(row.parent, []).append(row)

		flags = _item_flags(
			[r.production_item for r in recipes] + [r.item_code for rows in by_recipe.values() for r in rows]
		)

		out = []
		for r in recipes:
			fg = flags.get(r.production_item, frappe._dict())
			items = []
			for row in by_recipe.get(r.name, []):
				info = flags.get(row.item_code, frappe._dict())
				items.append(
					{
						"item_code": row.item_code,
						"item_name": info.item_name or row.item_code,
						"qty": flt(row.qty),
						"stock_uom": info.stock_uom or "",
						"has_batch_no": bool(info.has_batch_no),
						"available_qty": _stock_qty(row.item_code, warehouse),
						"batches": _batch_list(row.item_code, warehouse) if info.has_batch_no else [],
					}
				)
			out.append(
				{
					"name": r.name,
					"recipe_name": r.recipe_name,
					"production_item": r.production_item,
					"production_item_name": fg.item_name or r.production_item,
					"output_qty": flt(r.output_qty),
					"fg_stock": _stock_qty(r.production_item, warehouse),
					"fg_has_batch_no": bool(fg.has_batch_no),
					"items": items,
				}
			)
		return {"pos_profile": pos_profile, "company": company, "warehouse": warehouse, "recipes": out}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Get Production Recipes Error")
		frappe.throw(_("Error fetching production recipes: {0}").format(str(e)))


def _batch_list(item_code, warehouse):
	out = []
	for b in get_batch_qty(warehouse=warehouse, item_code=item_code) or []:
		if flt(b.qty) > 0:
			out.append(
				{
					"batch_no": b.batch_no,
					"qty": flt(b.qty),
					"expiry_date": frappe.db.get_value("Batch", b.batch_no, "expiry_date"),
				}
			)
	out.sort(key=lambda x: x["expiry_date"] or "9999-12-31")
	return out
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
./env/bin/python apps/pos_next/.worktrees/feat-pos-production/pos_next/_pn_run_tests.py pos_next.api.test_production
```

Expected: all PASS (including Task 2 smoke tests).

- [ ] **Step 5: Commit**

```bash
git add pos_next/api/production.py pos_next/api/test_production.py
git commit -m "feat(pos-production): recipe listing API scoped by company with stock/batch info"
```

---

### Task 4: API — `create_production`

**Files:**
- Modify: `pos_next/api/production.py` (append)
- Modify: `pos_next/api/test_production.py` (append test class)

**Interfaces:**
- Consumes: `_resolve_profile`, `_stock_qty`, `_batch_list` from Task 3.
- Produces: `create_production(recipe, qty, items, pos_profile, batches=None)` where `items` is a JSON string or list of `{"item_code": str, "qty": float}` and `batches` is a JSON string or dict `{item_code: batch_no}`. Returns `{"stock_entry": name, "production_log": name, "production_item": str, "qty": float}`. Raises `frappe.ValidationError` with a cashier-readable message on any pre-flight failure. Task 5's dialog calls exactly this.

- [ ] **Step 1: Write the failing tests**

Append to `pos_next/api/test_production.py`:

```python
import json

from frappe import ValidationError

from pos_next.api.production import create_production


def _seed_stock(item_code, warehouse, qty, batch_no=None):
	se = frappe.new_doc("Stock Entry")
	se.purpose = "Material Receipt"
	se.company = frappe.db.get_value("Warehouse", warehouse, "company")
	se.set_stock_entry_type()
	row = {
		"item_code": item_code,
		"qty": qty,
		"t_warehouse": warehouse,
		"use_serial_batch_fields": 1,
	}
	if batch_no:
		row["batch_no"] = batch_no
	se.append("items", row)
	se.insert()
	se.submit()
	return se.name


class TestCreateProduction(FrappeTestCase):
	def setUp(self):
		self.pos_profile = frappe.db.get_value("POS Profile", {"disabled": 0}, "name")
		if not self.pos_profile:
			self.skipTest("no POS Profile on this site")
		self.company, self.warehouse = frappe.db.get_value(
			"POS Profile", self.pos_profile, ["company", "warehouse"]
		)
		if not self.warehouse:
			self.warehouse = frappe.db.get_value(
				"Warehouse", {"company": self.company, "is_group": 0, "disabled": 0}, "name"
			)
		self.fg = _make_test_item()
		self.mat = _make_test_item()
		self.recipe = frappe.get_doc(
			{
				"doctype": "POS Production Recipe",
				"recipe_name": f"CR {uuid.uuid4().hex[:6]}",
				"production_item": self.fg,
				"output_qty": 1,
				"items": [{"item_code": self.mat, "qty": 2}],
				"companies": [{"company": self.company, "enabled": 1}],
			}
		).insert(ignore_permissions=True)

	def _bin_qty(self, item_code):
		return flt(
			frappe.db.get_value(
				"Bin", {"item_code": item_code, "warehouse": self.warehouse}, "actual_qty"
			)
		)

	def test_happy_path_moves_stock_and_creates_log(self):
		_seed_stock(self.mat, self.warehouse, 10)
		result = create_production(
			recipe=self.recipe.name,
			qty=3,
			items=json.dumps([{"item_code": self.mat, "qty": 6}]),
			pos_profile=self.pos_profile,
		)
		# material consumed, finished goods produced
		self.assertEqual(self._bin_qty(self.mat), 4)
		self.assertEqual(self._bin_qty(self.fg), 3)

		se = frappe.get_doc("Stock Entry", result["stock_entry"])
		self.assertEqual(se.docstatus, 1)
		self.assertEqual(se.purpose, "Manufacture")
		self.assertIn("POS Production:", se.remark)
		self.assertTrue(any(d.is_finished_item for d in se.items))

		log = frappe.get_doc("POS Production Log", result["production_log"])
		self.assertEqual(log.docstatus, 1)
		self.assertEqual(log.stock_entry, se.name)
		self.assertEqual(log.recipe, self.recipe.name)
		self.assertEqual(flt(log.qty), 3)
		used = json.loads(log.items_used)
		self.assertEqual(used[0]["item_code"], self.mat)

	def test_insufficient_stock_rejected_before_entry(self):
		_seed_stock(self.mat, self.warehouse, 1)
		with self.assertRaises(ValidationError) as ctx:
			create_production(
				recipe=self.recipe.name,
				qty=1,
				items=json.dumps([{"item_code": self.mat, "qty": 5}]),
				pos_profile=self.pos_profile,
			)
		self.assertIn(self.mat, str(ctx.exception))
		# nothing was created
		self.assertEqual(
			frappe.db.count("Stock Entry", {"remark": ["like", "POS Production:%"]}), 0
		)

	def test_recipe_of_other_company_rejected(self):
		other = frappe.db.get_value("Company", {"name": ["!=", self.company]}, "name")
		if not other:
			self.skipTest("only one company on this site")
		frappe.get_doc(
			{
				"doctype": "POS Production Recipe",
				"recipe_name": f"OC {uuid.uuid4().hex[:6]}",
				"production_item": self.fg,
				"output_qty": 1,
				"items": [{"item_code": self.mat, "qty": 1}],
				"companies": [{"company": other, "enabled": 1}],
			}
		).insert(ignore_permissions=True)
		with self.assertRaises(ValidationError):
			create_production(
				recipe=frappe.get_all("POS Production Recipe", limit=1, order_by="creation desc")[0].name,
				qty=1,
				items=json.dumps([{"item_code": self.mat, "qty": 1}]),
				pos_profile=self.pos_profile,
			)

	def test_batch_material_consumes_chosen_batch(self):
		mat_b = _make_test_item()
		frappe.db.set_value("Item", mat_b, "has_batch_no", 1)
		batch = frappe.new_doc("Batch")
		batch.batch_id = f"B-{uuid.uuid4().hex[:8]}"
		batch.item = mat_b
		batch.insert(ignore_permissions=True)
		_seed_stock(mat_b, self.warehouse, 4, batch_no=batch.name)

		result = create_production(
			recipe=self.recipe.name,
			qty=2,
			items=json.dumps(
				[
					{"item_code": self.mat, "qty": 2},
					{"item_code": mat_b, "qty": 3},
				]
			),
			pos_profile=self.pos_profile,
			batches=json.dumps({mat_b: batch.name}),
		)
		se = frappe.get_doc("Stock Entry", result["stock_entry"])
		row = next(d for d in se.items if d.item_code == mat_b)
		self.assertTrue(row.serial_and_batch_bundle or row.batch_no)
		self.assertEqual(flt(frappe.db.get_value("Batch", batch.name, "batch_qty")), 1)

	def test_finished_good_with_batch_gets_new_batch(self):
		frappe.db.set_value("Item", self.fg, "has_batch_no", 1)
		_seed_stock(self.mat, self.warehouse, 10)
		result = create_production(
			recipe=self.recipe.name,
			qty=2,
			items=json.dumps([{"item_code": self.mat, "qty": 4}]),
			pos_profile=self.pos_profile,
		)
		se = frappe.get_doc("Stock Entry", result["stock_entry"])
		fg_row = next(d for d in se.items if d.is_finished_item)
		self.assertTrue(fg_row.serial_and_batch_bundle or fg_row.batch_no)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
./env/bin/python apps/pos_next/.worktrees/feat-pos-production/pos_next/_pn_run_tests.py pos_next.api.test_production
```

Expected: `ImportError: cannot import name 'create_production'`.

- [ ] **Step 3: Implement `create_production` (append to `pos_next/api/production.py`)**

```python
import json
from uuid import uuid4


def _parse_items(items):
	if isinstance(items, str):
		items = json.loads(items)
	return [
		{"item_code": d["item_code"], "qty": flt(d.get("qty"))}
		for d in items
		if d.get("item_code")
	]


@frappe.whitelist()
def create_production(recipe, qty, items, pos_profile, batches=None):
	"""Consume materials and produce the recipe's item in one Manufacture Stock Entry."""
	try:
		company, warehouse = _resolve_profile(pos_profile)
		qty = flt(qty)
		if qty <= 0:
			frappe.throw(_("Production quantity must be greater than zero"))

		recipe_doc = frappe.get_doc("POS Production Recipe", recipe)
		if recipe_doc.disabled:
			frappe.throw(_("Recipe {0} is disabled").format(recipe_doc.recipe_name))
		if not any(c.company == company and c.enabled for c in recipe_doc.companies):
			frappe.throw(
				_("Recipe {0} is not available for company {1}").format(recipe_doc.recipe_name, company)
			)

		materials = _parse_items(items)
		if not materials:
			frappe.throw(_("At least one material is required"))
		batches = json.loads(batches) if isinstance(batches, str) else (batches or {})

		flags = _item_flags([m["item_code"] for m in materials] + [recipe_doc.production_item])

		# ---- pre-flight stock validation (trust boundary: client data is untrusted) ----
		merged = {}
		for m in materials:
			if m["qty"] <= 0:
				frappe.throw(_("Quantity for {0} must be greater than zero").format(m["item_code"]))
			if m["item_code"] == recipe_doc.production_item:
				frappe.throw(_("Material {0} is the production item itself").format(m["item_code"]))
			if m["item_code"] in merged:
				merged[m["item_code"]]["qty"] += m["qty"]
			else:
				merged[m["item_code"]] = dict(m)

		for m in merged.values():
			info = flags.get(m["item_code"])
			if not info:
				frappe.throw(_("Item {0} does not exist").format(m["item_code"]))
			if info.has_batch_no:
				batch_no = batches.get(m["item_code"])
				if not batch_no:
					frappe.throw(_("Batch is required for material {0}").format(m["item_code"]))
				batch_qty = flt(
					frappe.db.get_value("Batch", batch_no, "batch_qty")
				)
				if batch_qty < m["qty"]:
					frappe.throw(
						_("Material {0} batch {1} has only {2}, need {3}").format(
							m["item_code"], batch_no, batch_qty, m["qty"]
						)
					)
				m["batch_no"] = batch_no
			else:
				available = _stock_qty(m["item_code"], warehouse)
				if available < m["qty"]:
					frappe.throw(
						_("Material {0} is short by {1} in {2}").format(
							m["item_code"], m["qty"] - available, warehouse
						)
					)

		# ---- build the Manufacture Stock Entry ----
		se = frappe.new_doc("Stock Entry")
		se.company = company
		se.purpose = "Manufacture"
		se.remark = f"POS Production: {recipe_doc.recipe_name}"
		se.set_stock_entry_type()

		for m in merged.values():
			row = {
				"item_code": m["item_code"],
				"qty": m["qty"],
				"s_warehouse": warehouse,
				"use_serial_batch_fields": 1,
			}
			if m.get("batch_no"):
				row["batch_no"] = m["batch_no"]
			se.append("items", row)

		fg_info = flags.get(recipe_doc.production_item)
		fg_row = {
			"item_code": recipe_doc.production_item,
			"qty": qty,
			"t_warehouse": warehouse,
			"is_finished_item": 1,
			"use_serial_batch_fields": 1,
		}
		if fg_info and fg_info.has_batch_no:
			fg_batch = frappe.new_doc("Batch")
			fg_batch.batch_id = f"{recipe_doc.production_item}-{uuid4().hex[:6].upper()}"
			fg_batch.item = recipe_doc.production_item
			fg_batch.insert(ignore_permissions=True)
			fg_row["batch_no"] = fg_batch.name
		se.append("items", fg_row)

		# Cashiers have no Stock Entry doctype permission; the POS Production Log
		# insert below stays permission-enforced and is the real access gate.
		se.flags.ignore_permissions = True
		se.insert()
		se.submit()

		snapshot = [
			{"item_code": m["item_code"], "qty": m["qty"], "batch_no": m.get("batch_no")}
			for m in merged.values()
		]
		log = frappe.new_doc("POS Production Log")
		log.recipe = recipe_doc.name
		log.production_item = recipe_doc.production_item
		log.qty = qty
		log.items_used = json.dumps(snapshot)
		log.stock_entry = se.name
		log.pos_profile = pos_profile
		log.company = company
		log.insert()
		log.submit()

		return {
			"stock_entry": se.name,
			"production_log": log.name,
			"production_item": recipe_doc.production_item,
			"qty": qty,
		}
	except ValidationError:
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Create Production Error")
		frappe.throw(_("Error creating production: {0}").format(str(e)))
```

Add to the imports at the top of the file: `from frappe import ValidationError` and `from uuid import uuid4`.

- [ ] **Step 4: Run all production tests**

```bash
./env/bin/python apps/pos_next/.worktrees/feat-pos-production/pos_next/_pn_run_tests.py pos_next.api.test_production
```

Expected: all PASS. If `set_stock_entry_type` throws (no Manufacture Stock Entry Type on the site), create it inside `_seed_stock`-style setup in the failing test's setUp: `frappe.get_doc({"doctype": "Stock Entry Type", "name": "Manufacture", ...})` — but do not change the API; ERPNext ships a default "Manufacture" type on migrated sites.

- [ ] **Step 5: Commit**

```bash
git add pos_next/api/production.py pos_next/api/test_production.py
git commit -m "feat(pos-production): manufacture stock entry + log creation with preflight stock checks"
```

---

### Task 5: Frontend — `ProductionDialog.vue`

**Files:**
- Create: `POS/src/components/pos/ProductionDialog.vue`
- Test: `POS/src/components/pos/ProductionDialog.test.js`

**Interfaces:**
- Consumes: `pos_next.api.production.get_production_recipes(pos_profile)`, `pos_next.api.production.create_production(recipe, qty, items, pos_profile, batches)`, `pos_next.api.items.get_items(pos_profile, search_term, limit)` for the add-material search.
- Produces: Vue component with props `{ modelValue: Boolean, posProfile: String, company: String, currency: String }`, emits `update:modelValue`, `production-created`. Task 6 mounts it with `v-model="showProductionDialog"`, `:pos-profile="shiftStore.profileName"`, `:company="shiftStore.profileCompany"`, `:currency="shiftStore.profileCurrency"`.

- [ ] **Step 1: Write the component**

`POS/src/components/pos/ProductionDialog.vue`:

```vue
<template>
	<Dialog v-model="show" :options="{ title: __('Production'), size: '4xl' }">
		<template #body-content>
			<!-- STEP 1: pick recipe -->
			<template v-if="!selectedRecipe">
				<input
					v-model="search"
					type="text"
					:placeholder="__('Search recipe...')"
					class="w-full mb-3 px-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
				/>
				<div v-if="loadingRecipes" class="py-10 text-center text-sm text-gray-500">
					{{ __("Loading recipes...") }}
				</div>
				<div v-else-if="!filteredRecipes.length" class="py-10 text-center text-sm text-gray-500">
					{{ __("No recipes available for this outlet") }}
				</div>
				<div v-else class="flex flex-col gap-2 max-h-[60vh] overflow-y-auto">
					<button
						v-for="r in filteredRecipes"
						:key="r.name"
						class="w-full text-start px-4 py-3 border border-gray-200 rounded-lg hover:border-blue-400 hover:bg-blue-50 transition-colors"
						@click="selectRecipe(r)"
					>
						<div class="flex items-center justify-between">
							<span class="font-medium text-gray-900">{{ r.recipe_name }}</span>
							<span class="text-xs text-gray-500">
								{{ __("makes {0} × {1}", [r.output_qty, r.production_item_name]) }}
							</span>
						</div>
						<div class="text-xs mt-1" :class="canMake(r) ? 'text-green-600' : 'text-red-500'">
							{{ canMake(r) ? __("Materials available") : __("Materials insufficient") }}
						</div>
					</button>
				</div>
			</template>

			<!-- STEP 2: produce -->
			<template v-else>
				<div class="flex items-center justify-between mb-3">
					<div>
						<div class="font-medium text-gray-900">{{ selectedRecipe.recipe_name }}</div>
						<div class="text-xs text-gray-500">
							{{ __("Output per run: {0} × {1}", [selectedRecipe.output_qty, selectedRecipe.production_item_name]) }}
						</div>
					</div>
					<button class="text-sm text-gray-500 hover:text-gray-800" @click="backToRecipes">
						{{ __("Change recipe") }}
					</button>
				</div>

				<label class="block text-sm font-medium text-gray-700 mb-1">
					{{ __("Quantity to produce ({0})", [selectedRecipe.production_item_name]) }}
				</label>
				<input
					v-model.number="outputQty"
					type="number"
					min="0"
					step="any"
					class="w-full px-3 py-2 mb-3 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
				/>

				<table class="w-full text-sm">
					<thead>
						<tr class="text-start text-xs text-gray-500 uppercase">
							<th class="py-1 text-start">{{ __("Material") }}</th>
							<th class="py-1 text-start">{{ __("Qty") }}</th>
							<th class="py-1 text-start">{{ __("Stock") }}</th>
							<th class="py-1 text-start" v-if="hasAnyBatch">{{ __("Batch") }}</th>
							<th></th>
						</tr>
					</thead>
					<tbody>
						<tr v-for="(row, idx) in materialRows" :key="row.item_code" class="border-t border-gray-100">
							<td class="py-1.5 pe-2">
								<div>{{ row.item_name }}</div>
								<div v-if="rowHasInsufficientStock(row)" class="text-xs text-red-500">
									{{ __("insufficient") }}
								</div>
							</td>
							<td class="py-1.5 pe-2 w-24">
								<input
									v-model.number="row.qty"
									type="number"
									min="0"
									step="any"
									class="w-full px-2 py-1 border border-gray-300 rounded"
								/>
							</td>
							<td class="py-1.5 pe-2 text-gray-500 whitespace-nowrap">
								{{ row.available_qty }} {{ row.stock_uom }}
							</td>
							<td v-if="hasAnyBatch" class="py-1.5 pe-2 w-48">
								<select
									v-if="row.has_batch_no"
									v-model="row.batch_no"
									class="w-full px-2 py-1 border border-gray-300 rounded"
								>
									<option v-if="!row.batches.length" value="" disabled>
										{{ __("No batch in stock") }}
									</option>
									<option v-for="b in row.batches" :key="b.batch_no" :value="b.batch_no">
										{{ b.batch_no }} ({{ b.qty }})
									</option>
								</select>
							</td>
							<td class="py-1.5 text-end">
								<button
									class="text-red-500 hover:text-red-700 text-xs"
									@click="materialRows.splice(idx, 1)"
								>
									{{ __("Remove") }}
								</button>
							</td>
						</tr>
					</tbody>
				</table>

				<!-- add material -->
				<div class="mt-2 flex gap-2">
					<input
						v-model="newItemSearch"
						type="text"
						:placeholder="__('Add material by name / code...')"
						class="flex-1 px-3 py-2 text-sm border border-gray-300 rounded-lg"
						@input="debouncedSearchItems"
					/>
				</div>
				<div v-if="newItemResults.length" class="mt-1 border border-gray-200 rounded-lg max-h-40 overflow-y-auto">
					<button
						v-for="it in newItemResults"
						:key="it.item_code"
						class="w-full text-start px-3 py-2 text-sm hover:bg-gray-50"
						@click="addMaterial(it)"
					>
						{{ it.item_name || it.item_code }} <span class="text-xs text-gray-400">{{ it.item_code }}</span>
					</button>
				</div>

				<div v-if="errorMessage" class="mt-3 text-sm text-red-600">{{ errorMessage }}</div>

				<div class="mt-4 flex justify-end gap-2">
					<Button variant="subtle" @click="show = false">{{ __("Cancel") }}</Button>
					<Button variant="solid" :loading="submitting" :disabled="!canSubmit" @click="submit">
						{{ __("Process Production") }}
					</Button>
				</div>
			</template>
		</template>
	</Dialog>
</template>

<script setup>
import { Button, Dialog, createResource } from "frappe-ui";
import { computed, ref, watch } from "vue";

const props = defineProps({
	modelValue: Boolean,
	posProfile: String,
	company: String,
	currency: { type: String, default: "" },
});

const emit = defineEmits(["update:modelValue", "production-created"]);

const show = ref(props.modelValue);
watch(() => props.modelValue, (v) => {
	show.value = v;
	if (v) loadRecipes();
});
watch(show, (v) => emit("update:modelValue", v));

const loadingRecipes = ref(false);
const recipes = ref([]);
const search = ref("");
const selectedRecipe = ref(null);
const outputQty = ref(1);
const materialRows = ref([]);
const submitting = ref(false);
const errorMessage = ref("");
const newItemSearch = ref("");
const newItemResults = ref([]);

const filteredRecipes = computed(() => {
	if (!search.value) return recipes.value;
	const term = search.value.toLowerCase();
	return recipes.value.filter(
		(r) =>
			r.recipe_name.toLowerCase().includes(term) ||
			r.production_item_name.toLowerCase().includes(term)
	);
});

const hasAnyBatch = computed(() => materialRows.value.some((r) => r.has_batch_no));

function canMake(recipe) {
	return recipe.items.every((i) =>
		i.has_batch_no
			? i.batches.some((b) => b.qty >= i.qty)
			: i.available_qty >= i.qty
	);
}

const recipesResource = createResource({
	url: "pos_next.api.production.get_production_recipes",
	auto: false,
	onSuccess(data) {
		recipes.value = data.recipes || [];
		loadingRecipes.value = false;
	},
	onError(err) {
		errorMessage.value = err?.messages?.join("\n") || err || "Failed to load recipes";
		loadingRecipes.value = false;
	},
});

function loadRecipes() {
	if (!props.posProfile) return;
	loadingRecipes.value = true;
	errorMessage.value = "";
	selectedRecipe.value = null;
	recipesResource.submit({ pos_profile: props.posProfile });
}

function selectRecipe(recipe) {
	selectedRecipe.value = recipe;
	outputQty.value = recipe.output_qty;
	scaleRows();
}

function scaleRows() {
	const r = selectedRecipe.value;
	if (!r) return;
	const factor = r.output_qty ? outputQty.value / r.output_qty : 0;
	materialRows.value = r.items.map((i) => ({
		item_code: i.item_code,
		item_name: i.item_name,
		stock_uom: i.stock_uom,
		has_batch_no: i.has_batch_no,
		available_qty: i.available_qty,
		batches: i.batches || [],
		qty: +(i.qty * factor).toFixed(4),
		batch_no: i.batches.length ? bestBatch(i) : "",
	}));
}

function bestBatch(row) {
	// FIFO: batches already sorted by expiry date from the API
	const fit = row.batches.find((b) => b.qty >= row.qty);
	return (fit || row.batches[0] || {}).batch_no || "";
}

watch(outputQty, scaleRows);

function rowHasInsufficientStock(row) {
	return row.has_batch_no
		? !row.batches.some((b) => b.batch_no === row.batch_no && b.qty >= row.qty)
		: row.available_qty < row.qty;
}

const canSubmit = computed(
	() =>
		!!selectedRecipe.value &&
		outputQty.value > 0 &&
		materialRows.value.length > 0 &&
		materialRows.value.every(
			(r) => r.qty > 0 && (!r.has_batch_no || !!r.batch_no)
		)
);

const createResource$ = createResource({
	url: "pos_next.api.production.create_production",
	auto: false,
	onSuccess(result) {
		submitting.value = false;
		emit("production-created", result);
		show.value = false;
	},
	onError(err) {
		submitting.value = false;
		errorMessage.value = err?.messages?.join("\n") || err || "Production failed";
	},
});

function submit() {
	errorMessage.value = "";
	submitting.value = true;
	const batches = {};
	for (const r of materialRows.value) {
		if (r.has_batch_no) batches[r.item_code] = r.batch_no;
	}
	createResource$.submit({
		recipe: selectedRecipe.value.name,
		qty: outputQty.value,
		items: materialRows.value.map((r) => ({ item_code: r.item_code, qty: r.qty })),
		pos_profile: props.posProfile,
		batches,
	});
}

const itemsResource = createResource({
	url: "pos_next.api.items.get_items",
	auto: false,
	onSuccess(data) {
		newItemResults.value = (data || []).slice(0, 8).map((d) => ({
			item_code: d.item_code || d.name,
			item_name: d.item_name,
		}));
	},
});

let searchTimer;
function debouncedSearchItems() {
	clearTimeout(searchTimer);
	searchTimer = setTimeout(() => {
		if (!newItemSearch.value || !props.posProfile) {
			newItemResults.value = [];
			return;
		}
		itemsResource.submit({
			pos_profile: props.posProfile,
			search_term: newItemSearch.value,
			limit: 8,
		});
	}, 300);
}

function addMaterial(it) {
	if (materialRows.value.some((r) => r.item_code === it.item_code)) return;
	materialRows.value.push({
		item_code: it.item_code,
		item_name: it.item_name || it.item_code,
		stock_uom: "",
		has_batch_no: false, // plain add; batch UI only for recipe-known batch items
		available_qty: 0,
		batches: [],
		qty: 1,
		batch_no: "",
	});
	newItemSearch.value = "";
	newItemResults.value = [];
}

function backToRecipes() {
	selectedRecipe.value = null;
	errorMessage.value = "";
}
</script>
```

Note: `addMaterial` intentionally adds rows without batch tracking info — a manually added batch item would be rejected by the server with "Batch is required". If that proves annoying in manual testing, load `has_batch_no`/`batches` for the picked item via `pos_next.api.items.get_batch_serial_details` in a follow-up; do not block v1 on it.

- [ ] **Step 2: Write a light unit test**

`POS/src/components/pos/ProductionDialog.test.js` (pattern of `ShiftClosingDialog.test.js`; if that file mocks frappe-ui, copy its mock setup verbatim):

```js
import { describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import ProductionDialog from "./ProductionDialog.vue";

vi.mock("frappe-ui", () => ({
	Dialog: { template: "<div><slot name='body-content' /></div>", props: ["modelValue"] },
	Button: { template: "<button><slot /></button>", props: ["loading", "disabled", "variant"] },
	createResource: () => ({ submit: () => {}, reload: () => {} }),
}));

describe("ProductionDialog", () => {
	it("renders recipe list placeholder when opened with no recipes", async () => {
		const wrapper = mount(ProductionDialog, {
			props: { modelValue: true, posProfile: "POS-1", company: "Co", currency: "IDR" },
		});
		await wrapper.vm.$nextTick();
		expect(wrapper.text()).toContain("Loading recipes");
	});
});
```

- [ ] **Step 3: Run the frontend checks**

```bash
cd POS && npm install && npx vitest run src/components/pos/ProductionDialog.test.js && npm run lint
```

Expected: test passes; biome reports no new errors in the new files (run `npx biome check --write src/components/pos/ProductionDialog.vue src/components/pos/ProductionDialog.test.js` to auto-format).

- [ ] **Step 4: Commit**

```bash
git add POS/src/components/pos/ProductionDialog.vue POS/src/components/pos/ProductionDialog.test.js
git commit -m "feat(pos-production): two-step production dialog with editable materials and batch pickers"
```

---

### Task 6: Wiring — sidebar button, mobile menu, POSSale

**Files:**
- Modify: `POS/src/components/pos/ManagementSlider.vue`
- Modify: `POS/src/pages/POSSale.vue`

**Interfaces:**
- Consumes: `ProductionDialog` from Task 5 (props/emits as defined there).
- Produces: sidebar emits `menu-clicked('production')`; `POSSale.handleManagementMenuClick` gains a `production` branch; a `showProductionDialog` ref exists; the dialog is mounted.

- [ ] **Step 1: Add the sidebar button**

In `ManagementSlider.vue`, add props and a button after the Invoices button (before the spacer div):

```js
const props = defineProps({
	showProduction: { type: Boolean, default: false },
});
```

```html
<!-- Production -->
<button
	v-if="props.showProduction"
	@click="handleMenuClick('production')"
	:class="[
		'w-12 h-12 rounded-lg flex items-center justify-center transition-all relative group',
		activeMenu === 'production'
			? 'bg-amber-100 text-amber-600'
			: 'text-gray-600 hover:bg-gray-100 hover:text-gray-900',
	]"
	:title="__('Production')"
>
	<FeatherIcon name="tool" class="w-5 h-5" />
	<div
		class="absolute start-full ms-2 px-2 py-1 bg-gray-900 text-white text-xs rounded opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none whitespace-nowrap z-50"
	>
		{{ __("Production") }}
	</div>
</button>
```

- [ ] **Step 2: Wire POSSale.vue**

In `POSSale.vue`:

a) Import and register the dialog near the other dialog imports:

```js
import ProductionDialog from "@/components/pos/ProductionDialog.vue";
```

b) Add state + permission check near `handleManagementMenuClick`:

```js
const showProductionDialog = ref(false);
const canProduction = ref(false);

onMounted(async () => {
	const { checkPermission } = usePermissions();
	canProduction.value = await checkPermission("POS Production Log", "create");
});

function openProduction() {
	if (offlineStore.isOffline) return;
	showProductionDialog.value = true;
}
```

(If `usePermissions` is not yet imported in POSSale.vue, add `import { usePermissions } from "@/composables/usePermissions";`. If `offlineStore` has a different name in this file, use the same offline store reference other offline-gated actions use.)

c) Add the menu branch inside `handleManagementMenuClick`:

```js
} else if (menuItem === "production") {
	openProduction();
}
```

d) Pass the flag to the slider (line ~261):

```html
<ManagementSlider :show-production="canProduction" @menu-clicked="handleManagementMenuClick" />
```

e) Mount the dialog next to the other dialogs (copy the props pattern of `OffersDialog` around line 607):

```html
<!-- Production Dialog -->
<ProductionDialog
	v-model="showProductionDialog"
	:pos-profile="shiftStore.profileName"
	:company="shiftStore.profileCompany"
	:currency="shiftStore.profileCurrency"
	@production-created="handleProductionCreated"
/>
```

with the handler (place near `handleManagementMenuClick`):

```js
const { showSuccess } = useToast();

function handleProductionCreated(result) {
	showSuccess(__("Production complete: {0} × {1}", [result.production_item, result.qty]));
	handleRefresh();
}
```

Add `import { useToast } from "frappe-ui";` to the imports if POSSale.vue does not already import it (if a `useToast()` destructure already exists, reuse it instead of adding a second one).

f) Mobile entry: inside the `#menu-items` template at line ~35, add after the "View Shift" button:

```html
<button
	v-if="canProduction && !offlineStore.isOffline"
	class="w-full text-start px-4 py-2.5 text-sm text-gray-700 hover:bg-amber-50 flex items-center gap-3 transition-colors lg:hidden"
	@click="openProduction()"
>
	<svg class="w-5 h-5 text-amber-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
		<path
			stroke-linecap="round"
			stroke-linejoin="round"
			stroke-width="2"
			d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4"
		/>
	</svg>
	<span>{{ __("Production") }}</span>
</button>
```

- [ ] **Step 3: Lint, test, build**

```bash
cd POS && npm run lint && npx vitest run && npm run build
```

Expected: lint clean, all vitest suites pass, build succeeds and writes new hashed assets into `../pos_next/public/pos/assets/`.

- [ ] **Step 4: Commit (including built assets, repo convention)**

```bash
cd .. && git add POS/src pos_next/public/pos && git commit -m "feat(pos-production): sidebar + mobile profile menu entry with permission gating"
```

- [ ] **Step 5: Full backend regression**

```bash
./env/bin/python apps/pos_next/.worktrees/feat-pos-production/pos_next/_pn_run_tests.py pos_next.api.test_production pos_next.api.test_printing
```

Expected: all pass. Then commit any remaining fixes:

```bash
git add -A && git commit -m "fix(pos-production): wiring fixes from regression run"
```

(Skip this commit if there is nothing to fix.)

---

## Manual verification (post-plan, on the dev site)

Not part of a task: on `erpnext16.localhost` create one recipe (e.g. Kopi Susu from Kopi Bubuk + Susu) with the outlet company listed, log in as a cashier with the `POSNext Cashier` role, open the POS page, produce once, and check the Stock Entry + Log on the desk. Run in coordination with the user since the site is shared with the other agent.
