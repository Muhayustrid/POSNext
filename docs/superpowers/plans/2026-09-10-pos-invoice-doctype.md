# POS Invoice DocType Support — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a global switch so POS Next creates ERPNext v16 `POS Invoice` documents (instead of `Sales Invoice`) for new POS transactions, with full hooks, consolidation at shift close, offline dedup, reports union, and frontend awareness.

**Architecture:** One pipeline — the doctype is resolved server-side from a new single DocType (`POS Next Invoice Settings`) via `pos_next/invoice_type.py`; `invoices.py` builds either doctype. POS Invoice carries the same pos_next custom fields; a new `CustomPOSInvoice` controller override accepts POS Next shifts in place of ERPNext POS Opening Entries; closing shift consolidates POS Invoices into `is_consolidated=1` Sales Invoices (GL + stock land there). Reports/HQ union `POS Invoice + non-consolidated Sales Invoice`.

**Tech Stack:** Frappe v16 / ERPNext v16.33 (bench runs in Docker container `erpnext16_dev-frappe-1`, bench root `/workspace/development/frappe-bench`), Python tests via `pos_next/_pn_run_tests.py`, frontend Vue 3 + Pinia in `POS/`.

**Spec:** `docs/superpowers/specs/2026-09-10-pos-invoice-doctype-design.md` (read it first — it carries the spike-verified ERPNext facts this plan relies on).

## Global Constraints

- All bench/test commands run **inside the container** from bench root:
  `docker exec erpnext16_dev-frappe-1 sh -c "cd /workspace/development/frappe-bench && <cmd>"`
- Test runner (macOS `bench` and `./env/bin/python` are broken outside the container):
  `./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py <module> [<module> ...]` — serial only.
- Site: `posnext.localhost`. Never run against any other site.
- Client-sent `doctype` is never trusted for invoice targets (only `"Sales Order"` passes through); the server resolves via `get_pos_invoice_doctype()`.
- POS Next–specific effects on `POS Invoice` doc_events fire **only when `posa_pos_opening_shift` is set** (ownership guard); `pos_stock_update` realtime fires for all POS Invoices. Sales Invoice hooks skip docs with `is_consolidated=1`.
- In POS Invoice mode, credit sale / redeem-credit / partial payment paths must raise a clear `frappe.throw` server-side.
- Existing Sales Invoice behavior must not change when the switch stays on `Sales Invoice` (regression suite must pass untouched).
- Commit after every task, on branch `feat/pos-invoice-doctype`. Conventional-commit style, English.
- No new pip/npm dependencies.

---

### Task 1: Global invoice-type setting (single doctype + resolver module)

**Files:**
- Create: `pos_next/pos_next/doctype/pos_next_invoice_settings/__init__.py`
- Create: `pos_next/pos_next/doctype/pos_next_invoice_settings/pos_next_invoice_settings.json`
- Create: `pos_next/pos_next/doctype/pos_next_invoice_settings/pos_next_invoice_settings.py`
- Create: `pos_next/invoice_type.py`
- Test: `pos_next/test_invoice_type.py`

**Interfaces (produced, used by Tasks 3–10):**
- `pos_next.invoice_type.SALES_INVOICE = "Sales Invoice"`, `POS_INVOICE = "POS Invoice"`
- `pos_next.invoice_type.get_pos_invoice_doctype() -> str` (request-cached)
- `pos_next.invoice_type.get_sales_report_doctypes() -> list[str]` — `["Sales Invoice"]` or `["POS Invoice", "Sales Invoice"]`
- `pos_next.invoice_type.is_pos_next_owned(doc) -> bool` — `bool(doc.get("posa_pos_opening_shift"))`

- [ ] **Step 1: Write the failing test** — `pos_next/test_invoice_type.py`:

```python
import frappe
from frappe.tests.utils import FrappeTestCase

from pos_next.invoice_type import (
	POS_INVOICE,
	SALES_INVOICE,
	get_pos_invoice_doctype,
	get_sales_report_doctypes,
)


def _set_invoice_type(value):
	doc = frappe.get_doc("POS Next Invoice Settings", "POS Next Invoice Settings")
	doc.invoice_type = value
	doc.save(ignore_permissions=True)


class TestInvoiceType(FrappeTestCase):
	def setUp(self):
		frappe.local.pop("_pos_next_invoice_doctype", None)

	def tearDown(self):
		_set_invoice_type(SALES_INVOICE)
		frappe.local.pop("_pos_next_invoice_doctype", None)
		frappe.db.commit()

	def test_defaults_to_sales_invoice(self):
		self.assertEqual(get_pos_invoice_doctype(), SALES_INVOICE)
		self.assertEqual(get_sales_report_doctypes(), ["Sales Invoice"])

	def test_switch_allowed_without_open_shift(self):
		_set_invoice_type(POS_INVOICE)  # no open shift in test tx -> allowed
		self.assertEqual(get_pos_invoice_doctype(), POS_INVOICE)
		self.assertEqual(get_sales_report_doctypes(), ["POS Invoice", "Sales Invoice"])

	def test_switch_rejected_with_open_shift(self):
		# fabricate an open shift row without full insert flow
		from frappe.utils import nowdate

		shift = frappe.get_doc(
			{
				"doctype": "POS Opening Shift",
				"pos_profile": frappe.db.get_value("POS Profile", {"disabled": 0}, "name"),
				"company": frappe.db.get_value("POS Profile", {"disabled": 0}, "company"),
				"user": "Administrator",
				"posting_date": nowdate(),
				"period_start_date": frappe.utils.now_datetime(),
				"balance_details": [],
			}
		).insert(ignore_permissions=True)
		shift.submit()
		try:
			with self.assertRaises(frappe.ValidationError):
				_set_invoice_type(POS_INVOICE)
		finally:
			shift.cancel()
			frappe.delete_doc("POS Opening Shift", shift.name, force=1)
```

- [ ] **Step 2: Run it and verify it fails** — `./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py pos_next.test_invoice_type` → `ModuleNotFoundError: No module named 'pos_next.invoice_type'`.

- [ ] **Step 3: Create the single doctype.** `pos_next_invoice_settings.json`:

```json
{
 "actions": [],
 "creation": "2026-09-10 00:00:00.000000",
 "doctype": "DocType",
 "engine": "InnoDB",
 "field_order": ["invoice_type"],
 "fields": [
  {
   "default": "Sales Invoice",
   "fieldname": "invoice_type",
   "fieldtype": "Select",
   "label": "Invoice Type Created via POS Screen",
   "options": "Sales Invoice\nPOS Invoice"
  }
 ],
 "index_web_pages_for_search": 1,
 "issingle": 1,
 "links": [],
 "modified": "2026-09-10 00:00:00.000000",
 "modified_by": "Administrator",
 "module": "POS Next",
 "name": "POS Next Invoice Settings",
 "owner": "Administrator",
 "permissions": [
  {"create": 1, "email": 1, "print": 1, "read": 1, "role": "System Manager", "share": 1, "write": 1}
 ],
 "sort_field": "modified",
 "sort_order": "DESC",
 "states": []
}
```

`pos_next_invoice_settings.py` — verify the literal Pending status against `_ensure_offline_uniqueness` in `pos_next/api/invoices.py` (~line 1239) first and use what the code actually writes:

```python
import frappe
from frappe import _
from frappe.model.document import Document


class POSNextInvoiceSettings(Document):
	def validate(self):
		before = self.get_doc_before_save()
		if before and before.invoice_type != self.invoice_type:
			self.validate_switch()

	def validate_switch(self):
		open_shifts = frappe.db.count("POS Opening Shift", {"docstatus": 1, "status": "Open"})
		if open_shifts:
			frappe.throw(
				_("Invoice Type cannot be changed while {0} POS Opening Shift(s) are open.").format(
					frappe.bold(open_shifts)
				)
			)
		pending = frappe.db.count("Offline Invoice Sync", {"status": "Pending"})
		if pending:
			frappe.throw(
				_("Invoice Type cannot be changed while {0} offline invoice(s) are pending sync.").format(
					frappe.bold(pending)
				)
			)
```

`__init__.py` is empty. `module` must be `POS Next` (matches `modules.txt`).

- [ ] **Step 4: Create `pos_next/invoice_type.py`:**

```python
"""Global invoice-doctype resolution for POS Next (see spec 2026-09-10)."""

import frappe

SALES_INVOICE = "Sales Invoice"
POS_INVOICE = "POS Invoice"
_SINGLE = "POS Next Invoice Settings"


def get_pos_invoice_doctype():
	"""The doctype new POS transactions are created in (request-cached)."""
	cached = getattr(frappe.local, "_pos_next_invoice_doctype", None)
	if cached:
		return cached
	value = frappe.db.get_single_value(_SINGLE, "invoice_type") or SALES_INVOICE
	if value not in (SALES_INVOICE, POS_INVOICE):
		value = SALES_INVOICE
	frappe.local._pos_next_invoice_doctype = value
	return value


def get_sales_report_doctypes():
	"""Doctypes sales reporting reads. In POS Invoice mode, legacy Sales
	Invoices are still included (consolidated ones excluded at query level)."""
	if get_pos_invoice_doctype() == POS_INVOICE:
		return [POS_INVOICE, SALES_INVOICE]
	return [SALES_INVOICE]


def is_pos_next_owned(doc):
	"""True when a POS Invoice was created by POS Next (vs ERPNext built-in POS)."""
	return bool(doc.get("posa_pos_opening_shift"))
```

- [ ] **Step 5: Register + migrate** — add `"POS Next Invoice Settings"` nowhere manually; `bench --site posnext.localhost migrate` inside the container picks the doctype folder up (run `./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py --sync` only if migrate misses it).

- [ ] **Step 6: Run tests** — module passes (3 tests).

- [ ] **Step 7: Commit** — `git add -A pos_next/pos_next/doctype/pos_next_invoice_settings pos_next/invoice_type.py pos_next/test_invoice_type.py && git commit -m "feat(pos): global POS/Sales invoice type switch"`

---

### Task 2: Custom fields on POS Invoice + child-table columns

**Files:**
- Modify: `pos_next/install.py` (CUSTOM_FIELDS dict, ~line 30)
- Modify: `pos_next/pos_next/doctype/sales_invoice_reference/sales_invoice_reference.json` (add `pos_invoice` Link)
- Modify: `pos_next/pos_next/doctype/offline_invoice_sync/offline_invoice_sync.json` (add `pos_invoice` Link)
- Test: `pos_next/test_invoice_type.py` (extend)

**Interfaces (produced):** DB columns `tabPOS Invoice`.`posa_pos_opening_shift|discount_confirmation_code|pos_applied_offer_rules|pos_queue_number|pos_queue_date|buyer_name`, `tabPOS Invoice Item`.`pos_package*|pos_offer_item_rules`, `tabSales Invoice Reference`.`pos_invoice`, `tabOffline Invoice Sync`.`pos_invoice`.

- [ ] **Step 1: Write the failing test** — append to `pos_next/test_invoice_type.py`:

```python
class TestPOSInvoiceCustomFields(FrappeTestCase):
	def test_columns_exist(self):
		from pos_next.install import CUSTOM_FIELDS, after_migrate

		after_migrate()  # idempotent
		for dt, fields in CUSTOM_FIELDS.items():
			if dt not in ("POS Invoice", "POS Invoice Item", "Sales Invoice Reference",
			              "Offline Invoice Sync"):
				continue
			for f in fields:
				self.assertTrue(
					frappe.db.has_column(dt, f["fieldname"]), f"{dt}.{f['fieldname']} missing"
				)
```

- [ ] **Step 2: Run** → fails (`posa_pos_opening_shift missing`).

- [ ] **Step 3: Implement.** In `install.py` `CUSTOM_FIELDS` add (copy field defs verbatim from the Sales Invoice entries above them; only insert_after anchors differ):

```python
		"POS Invoice": [
			# same five dicts as "Sales Invoice": buyer_name, discount_confirmation_code,
			# pos_applied_offer_rules, pos_queue_number, pos_queue_date
			# (insert_after chain identical — POS Invoice shares the field layout)
			# PLUS:
			{
				"fieldname": "posa_pos_opening_shift",
				"label": "POS Opening Shift",
				"fieldtype": "Link",
				"options": "POS Opening Shift",
				"insert_after": "pos_profile",
				"read_only": 1,
				"no_copy": 1,
				"print_hide": 1,
				"translatable": 0,
			},
		],
		"POS Invoice Item": [
			# same five dicts as "Sales Invoice Item": pos_package, pos_package_instance,
			# pos_package_role, pos_package_snapshot, pos_offer_item_rules
		],
		"Sales Invoice Reference": [
			{
				"fieldname": "pos_invoice",
				"label": "POS Invoice",
				"fieldtype": "Link",
				"options": "POS Invoice",
				"insert_after": "sales_invoice",
			},
		],
		"Offline Invoice Sync": [
			{
				"fieldname": "pos_invoice",
				"label": "POS Invoice",
				"fieldtype": "Link",
				"options": "POS Invoice",
				"insert_after": "sales_invoice",
				"read_only": 1,
			},
		],
```

Also add the same `posa_pos_opening_shift` dict to the existing `"Sales Invoice"` list **only if** `frappe.db.has_column("Sales Invoice", "posa_pos_opening_shift")` is False on this site (it exists today via ERPNext core — skip if present). Check how `install.py` applies CUSTOM_FIELDS (look for the loop calling `create_custom_fields`); `after_migrate` runs it, so the test's `after_migrate()` call materializes columns. For the two doctype JSONs, add matching field dicts to `fields` + field_order so a fresh install has them schema-side too.

- [ ] **Step 4: Run tests** → pass.

- [ ] **Step 5: Commit** — `feat(pos): pos_next custom fields on POS Invoice, sync + closing-shift children`

---

### Task 3: `CustomPOSInvoice` controller override

**Files:**
- Modify: `pos_next/overrides/sales_invoice.py` (append class)
- Modify: `pos_next/hooks.py:142` (`override_doctype_class`)
- Test: `pos_next/test_invoice_type.py` (extend)

**Interfaces (consumes):** Task 1 helper, Task 2 `posa_pos_opening_shift` column.
**Interfaces (produces):** `pos_next.overrides.sales_invoice.CustomPOSInvoice` registered for `"POS Invoice"`.

- [ ] **Step 1: Write the failing test:**

```python
class TestCustomPOSInvoice(FrappeTestCase):
	def setUp(self):
		self.profile = frappe.db.get_value(
			"POS Profile", {"disabled": 0}, ["name", "company", "warehouse"], as_dict=True
		)
		if not self.profile:
			self.skipTest("no POS Profile")

	def _opening_shift(self):
		return frappe.get_doc(
			{
				"doctype": "POS Opening Shift",
				"pos_profile": self.profile.name,
				"company": self.profile.company,
				"user": "Administrator",
				"posting_date": frappe.utils.nowdate(),
				"period_start_date": frappe.utils.now_datetime(),
				"balance_details": [],
			}
		).insert(ignore_permissions=True)

	def test_pos_next_shift_replaces_opening_entry_requirement(self):
		shift = self._opening_shift()
		shift.submit()
		try:
			doc = frappe.new_doc("POS Invoice")
			doc.update(
				{
					"customer": frappe.get_all("Customer", pluck="name", limit=1)[0],
					"is_pos": 1,
					"update_stock": 1,
					"pos_profile": self.profile.name,
					"company": self.profile.company,
					"set_warehouse": self.profile.warehouse,
					"posa_pos_opening_shift": shift.name,
					"currency": frappe.db.get_value(
						"Company", self.profile.company, "default_currency"
					),
				}
			)
			doc.append("items", {"item_code": "NONEXISTENT-ITEM", "qty": 1, "rate": 1})
			doc.append("payments", {"mode_of_payment": "Cash", "amount": 1})
			# Opening-entry gate must NOT fire; the item error proves we got past it
			with self.assertRaises(Exception) as ctx:
				doc.insert()
			self.assertNotIn("POS Opening Entry", str(ctx.exception))
		finally:
			shift.cancel()
			frappe.delete_doc("POS Opening Shift", shift.name, force=1)
```

- [ ] **Step 2: Run** → fails with `ValidationError: No open POS Opening Entry found...` (message contains "POS Opening Entry").

- [ ] **Step 3: Implement** — in `pos_next/overrides/sales_invoice.py`:

```python
from erpnext.accounts.doctype.pos_invoice.pos_invoice import POSInvoice  # near other erpnext imports


class CustomPOSInvoice(CustomSalesInvoice, POSInvoice):
	"""POS Invoice lifecycle (ERPNext) + POS Next customizations.

	MRO: CustomPOSInvoice -> CustomSalesInvoice -> POSInvoice -> SalesInvoice.
	ERPNext's POS Invoice validate/on_submit win over SalesInvoice's; POS Next's
	update_packing_list / use_serial_batch_fields handling is inherited.
	"""

	def validate_pos_opening_entry(self):
		from pos_next.invoice_type import is_pos_next_owned

		if is_pos_next_owned(self):
			status = frappe.db.get_value(
				"POS Opening Shift", self.posa_pos_opening_shift, "status"
			)
			if status != "Open":
				frappe.throw(
					_("POS Opening Shift {0} is not open.").format(
						frappe.bold(self.posa_pos_opening_shift)
					)
				)
			return
		super().validate_pos_opening_entry()
```

In `pos_next/hooks.py`:

```python
override_doctype_class = {
	"Sales Invoice": "pos_next.overrides.sales_invoice.CustomSalesInvoice",
	"POS Invoice": "pos_next.overrides.sales_invoice.CustomPOSInvoice",
}
```

Audit `pos_next/overrides/packed_item.py`: if it monkey-patches keyed lookup by doctype name, extend to `POS Invoice Item` (same pattern, conditional on the module's existing structure).

- [ ] **Step 4: Run tests** → pass (also rerun `pos_next.test_packed_items_regression` for the audit).

- [ ] **Step 5: Commit** — `feat(pos): CustomPOSInvoice accepts POS Next opening shifts`

---

### Task 4: Full POS Invoice doc_events with ownership + consolidated guards

**Files:**
- Create: `pos_next/pos_invoice_events.py`
- Modify: `pos_next/hooks.py` (`doc_events["POS Invoice"]`, ~line 206)
- Modify (add 2-line guard): `pos_next/api/sales_invoice_hooks.py` (`validate`, `before_cancel`, `record_one_time_offer_usage`, `release_one_time_offer_usage`), `pos_next/api/wallet.py` (`validate_wallet_payment`, `process_loyalty_to_wallet`), `pos_next/api/packages.py` (`validate_invoice_packages`), `pos_next/overrides/discount_code.py` (`validate_invoice_discounts`, `record_code_usage_on_submit`), `pos_next/overrides/pos_offer_usage.py` (`validate_invoice_offers`, `record_offer_usage_on_submit`, `release_offer_usage_on_cancel`), `pos_next/overrides/queue_counter.py` (`bump_queue_counter`), `pos_next/shift_schedule.py` (`validate_invoice`), `pos_next/realtime_events.py` (`emit_invoice_created_event`)
- Test: `pos_next/test_pos_invoice_events.py`

**Interfaces (produces):** guard snippet used everywhere:

```python
if cint(doc.get("is_consolidated")):
	return
```

- [ ] **Step 1: Write the failing test:**

```python
import frappe
from frappe.tests.utils import FrappeTestCase


def _minimal_posi(owned=True):
	profile = frappe.db.get_value(
		"POS Profile", {"disabled": 0}, ["name", "company", "warehouse"], as_dict=True
	)
	doc = frappe.new_doc("POS Invoice")
	doc.update(
		{
			"customer": frappe.get_all("Customer", pluck="name", limit=1)[0],
			"is_pos": 1,
			"update_stock": 1,
			"pos_profile": profile.name,
			"company": profile.company,
			"set_warehouse": profile.warehouse,
			"currency": frappe.db.get_value("Company", profile.company, "default_currency"),
		}
	)
	if owned:
		doc.posa_pos_opening_shift = "POSA-OS-DOES-NOT-EXIST"
	doc.append("items", {"item_code": frappe.get_all("Item", pluck="name", limit=1)[0],
	                     "qty": 1, "rate": 1, "warehouse": profile.warehouse})
	doc.append("payments", {"mode_of_payment": "Cash", "amount": 1})
	return doc


class TestPOSInvoiceEventGuards(FrappeTestCase):
	def test_owned_invoice_requires_real_shift(self):
		doc = _minimal_posi(owned=True)
		with self.assertRaises(frappe.ValidationError) as ctx:
			doc.insert()
		self.assertIn("POS Opening Shift", str(ctx.exception))

	def test_unowned_invoice_skips_pos_next_gates(self):
		# built-in-POS invoice: no posa_pos_opening_shift -> must not trip the
		# pos_next ownership checks (insert may still fail on unrelated ERPNext
		# validation, but never on POS Opening Shift / pos_next gates)
		doc = _minimal_posi(owned=False)
		try:
			doc.insert()
		except Exception as e:
			self.assertNotIn("POS Opening Shift", str(e))

	def test_consolidated_flag_snippet(self):
		from pos_next.overrides.queue_counter import bump_queue_counter

		doc = frappe._dict(doctype="Sales Invoice", is_consolidated=1, company="X",
		                   posting_date="2026-09-10", name="SI-CONS-1")
		bump_queue_counter(doc)  # must be a no-op, not raise
		self.assertFalse(frappe.db.exists("POS Queue Counter", {"company": "X"}))
```

- [ ] **Step 2: Run** → failures (owned invoice passes the opening-entry gate instead of throwing on fake shift; queue bump creates row).

- [ ] **Step 3: Implement.** `pos_next/pos_invoice_events.py`:

```python
"""POS Invoice doc_events.

ERPNext's built-in POS also creates POS Invoices on this site, so every
pos_next-specific effect is gated on ownership (posa_pos_opening_shift set).
The stock realtime broadcast is deliberately NOT gated: POS Next terminals
must see built-in-POS sales too.
"""

from pos_next import realtime_events
from pos_next.api import packages, sales_invoice_hooks, wallet
from pos_next.invoice_type import is_pos_next_owned
from pos_next.overrides import discount_code, pos_offer_usage, queue_counter
from pos_next.shift_schedule import validate_invoice as validate_shift_schedule
from pos_next.overrides.pricing_rule import apply_min_max_price_discounts


def validate(doc, method=None):
	apply_min_max_price_discounts(doc, method)  # ungated: pricing applies to all
	if not is_pos_next_owned(doc):
		return
	sales_invoice_hooks.validate(doc, method)
	wallet.validate_wallet_payment(doc, method)
	packages.validate_invoice_packages(doc, method)
	discount_code.validate_invoice_discounts(doc, method)
	pos_offer_usage.validate_invoice_offers(doc, method)
	validate_shift_schedule(doc, method)


def before_cancel(doc, method=None):
	if not is_pos_next_owned(doc):
		return
	sales_invoice_hooks.before_cancel(doc, method)


def on_submit(doc, method=None):
	realtime_events.emit_stock_update_event(doc, method)  # ungated
	if not is_pos_next_owned(doc):
		return
	wallet.process_loyalty_to_wallet(doc, method)
	sales_invoice_hooks.record_one_time_offer_usage(doc, method)
	discount_code.record_code_usage_on_submit(doc, method)
	pos_offer_usage.record_offer_usage_on_submit(doc, method)
	queue_counter.bump_queue_counter(doc, method)


def on_cancel(doc, method=None):
	realtime_events.emit_stock_update_event(doc, method)  # ungated
	if not is_pos_next_owned(doc):
		return
	sales_invoice_hooks.release_one_time_offer_usage(doc, method)
	pos_offer_usage.release_offer_usage_on_cancel(doc, method)


def after_insert(doc, method=None):
	if not is_pos_next_owned(doc):
		return
	realtime_events.emit_invoice_created_event(doc, method)
```

Replace `hooks.py` `"POS Invoice"` entry with:

```python
	"POS Invoice": {
		"validate": "pos_next.pos_invoice_events.validate",
		"before_cancel": "pos_next.pos_invoice_events.before_cancel",
		"on_submit": "pos_next.pos_invoice_events.on_submit",
		"on_cancel": "pos_next.pos_invoice_events.on_cancel",
		"after_insert": "pos_next.pos_invoice_events.after_insert",
	},
```

Add the consolidated guard (first lines of each function, after the signature/docstring):

```python
	if doc.get("is_consolidated"):
		return
```

to every function listed in **Files** (Sales Invoice side). Consolidated SIs are generated by the merge log; their POS effects were already recorded by the underlying POS Invoices. Verify each guarded function signature is `def fn(doc, method=None)` — where a hook receives only `doc`, guard with the same two lines.

- [ ] **Step 4: Run** — `pos_next.test_pos_invoice_events` passes; rerun the existing suites `pos_next.api.test_queue pos_next.api.test_offers pos_next.api.test_discount_code pos_next.test_promotions` to prove no regression.

- [ ] **Step 5: Commit** — `feat(pos): full POS Invoice event chain with ownership and consolidated guards`

---

### Task 5: Server-resolved doctype through `update_invoice` / `submit_invoice`

**Files:**
- Modify: `pos_next/api/invoices.py` — `_strip_server_managed_fields` (line 346), `update_invoice` (line 785: doctype at 797, branches 841/982/1014/1073), `submit_invoice` (line 1348: doctype at 1395, branches 1430/1443/1467/1494/1534), `update_invoice`-adjacent `_set_payment_accounts` call path, offline dedup block (~1408–1448)
- Test: `pos_next/api/test_pos_invoice_submit.py`

**Interfaces (consumes):** Task 1 `get_pos_invoice_doctype`, Task 2 columns, Task 3 override, Task 4 hooks.

- [ ] **Step 1: Write the failing test** (full-cycle; mirrors spike flow):

```python
import json

import frappe
from frappe.tests.utils import FrappeTestCase

from pos_next.api.invoices import submit_invoice
from pos_next.invoice_type import POS_INVOICE, SALES_INVOICE


class TestSubmitInvoicePOSIMode(FrappeTestCase):
	def setUp(self):
		self.profile = frappe.db.get_value(
			"POS Profile", {"disabled": 0}, ["name", "company", "warehouse"], as_dict=True
		)
		self.item = frappe.get_all("Item", {"disabled": 0}, pluck="name", limit=1)[0]
		self.customer = frappe.get_all("Customer", pluck="name", limit=1)[0]
		self._mode = frappe.get_all("POS Payment Method", {"parent": self.profile.name},
		                            pluck="mode_of_payment", limit=1)
		if not self._mode:
			self.skipTest("profile has no payment methods")
		# stock so validation passes
		frappe.db.set_value("Bin", {"item_code": self.item, "warehouse": self.profile.warehouse},
		                    "actual_qty", 5, update_modified=False) if frappe.db.exists(
			"Bin", {"item_code": self.item, "warehouse": self.profile.warehouse}) else None
		setting = frappe.get_doc("POS Next Invoice Settings", "POS Next Invoice Settings")
		setting.invoice_type = POS_INVOICE
		setting.save(ignore_permissions=True)
		frappe.local.pop("_pos_next_invoice_doctype", None)
		shift = frappe.get_doc({
			"doctype": "POS Opening Shift", "pos_profile": self.profile.name,
			"company": self.profile.company, "user": "Administrator",
			"posting_date": frappe.utils.nowdate(),
			"period_start_date": frappe.utils.now_datetime(),
			"balance_details": [{"mode_of_payment": self._mode[0], "amount": 0}],
		}).insert(ignore_permissions=True)
		shift.submit()
		self.shift = shift

	def tearDown(self):
		for name in frappe.get_all("POS Invoice", {"posa_pos_opening_shift": self.shift.name},
		                           pluck="name"):
			doc = frappe.get_doc("POS Invoice", name)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("POS Invoice", name, force=1, ignore_permissions=True)
		if getattr(self, "shift", None):
			self.shift.reload()
			if self.shift.docstatus == 1:
				self.shift.cancel()
			frappe.delete_doc("POS Opening Shift", self.shift.name, force=1,
			                  ignore_permissions=True)
		setting = frappe.get_doc("POS Next Invoice Settings", "POS Next Invoice Settings")
		setting.invoice_type = SALES_INVOICE
		setting.save(ignore_permissions=True)
		frappe.local.pop("_pos_next_invoice_doctype", None)
		frappe.db.commit()

	def test_submit_creates_pos_invoice(self):
		payload = {
			"pos_profile": self.profile.name,
			"posa_pos_opening_shift": self.shift.name,
			"customer": self.customer,
			"doctype": "Sales Invoice",  # client guess must be ignored
			"items": [{"item_code": self.item, "qty": 1, "rate": 100,
			           "warehouse": self.profile.warehouse}],
			"payments": [{"mode_of_payment": self._mode[0], "amount": 100}],
		}
		result = submit_invoice(invoice=payload)
		name = result.get("name")
		self.assertTrue(name)
		doc = frappe.get_doc("POS Invoice", name)
		self.assertEqual(doc.doctype, "POS Invoice")
		self.assertEqual(doc.docstatus, 1)
		self.assertEqual(doc.posa_pos_opening_shift, self.shift.name)
		self.assertEqual(doc.paid_amount, doc.grand_total)

	def test_credit_sale_blocked_in_posi_mode(self):
		payload = {
			"pos_profile": self.profile.name, "posa_pos_opening_shift": self.shift.name,
			"customer": self.customer, "pos_next_credit_sale": 1,
			"items": [{"item_code": self.item, "qty": 1, "rate": 100,
			           "warehouse": self.profile.warehouse}],
			"payments": [],
		}
		with self.assertRaises(frappe.ValidationError):
			submit_invoice(invoice=payload)
```

- [ ] **Step 2: Run** → fails (creates Sales Invoice / no throw).

- [ ] **Step 3: Implement** in `invoices.py`:

1. `_strip_server_managed_fields`: add `cleaned.pop("doctype", None)`.
2. New local resolver near the top:

```python
def _resolve_target_doctype(payload_doctype):
	"""Sales Order drafts pass through; invoice doctypes are server-resolved."""
	if payload_doctype == "Sales Order":
		return "Sales Order"
	return get_pos_invoice_doctype()
```

3. `update_invoice` line 797: `doctype = _resolve_target_doctype(data.get("doctype"))`; keep `data.setdefault("doctype", doctype)`.
4. `submit_invoice` line 1395: same replacement (after `_strip_server_managed_fields`, `invoice.get("doctype")` is None → resolved).
5. Replace the `if doctype == "Sales Invoice":` guards that must apply to BOTH doctypes with `if doctype != "Sales Order":` — audit each of lines 841, 982, 1014, 1073, 1467, 1494, 1534 individually: offer-stash write (982–1011), `is_pos/update_stock/set_warehouse` (1014–1018), negative return payments (1071–1080), `update_stock = 1` (1467), `_set_payment_accounts` (1494), write-off (1534) → all become `doctype != "Sales Order"`. The `_validate_stock_on_invoice` check (613) stays Sales-Invoice-only; add the equivalent for POS Invoice (POSInvoice has its own stock validation, but `_collect_stock_errors` uses Bin — see Task 7 for effective stock).
6. Credit-sale block, at the top of `submit_invoice`'s try block (~line 1426):

```python
		if doctype == "POS Invoice" and (
			cint(invoice.get("pos_next_credit_sale"))
			or cint(invoice.get("pos_next_redeemed_customer_credit"))
			or flt(invoice.get("partially_paid") or 0)
		):
			frappe.throw(
				_("Credit sales and partial payments are not supported in POS Invoice mode. "
				"Switch Invoice Type to Sales Invoice to use them.")
			)
```

(Grep `pos_next_credit_sale` in `invoices.py` for where the server normally sets the flag and place the block before any draft creation.)
7. Returns listing/flows: `validate_return_items` (686) already takes `doctype` — thread `get_pos_invoice_doctype()` through its callers. The returnable/search queries at lines 2042, 2081, 2137, 2175, 2226, 2257 (`frappe.qb.DocType("Sales Invoice")` hardcodes in `get_returnable_invoices`, `search_invoices_for_return`, `get_invoice_for_return`, `check_invoice_return_validity`) resolve the doctype the same way. Add a test:

```python
	def test_returnable_lists_pos_invoice(self):
		from pos_next.api.invoices import get_returnable_invoices

		payload = {
			"pos_profile": self.profile.name, "posa_pos_opening_shift": self.shift.name,
			"customer": self.customer,
			"items": [{"item_code": self.item, "qty": 1, "rate": 100,
			           "warehouse": self.profile.warehouse}],
			"payments": [{"mode_of_payment": self._mode[0], "amount": 100}],
		}
		result = submit_invoice(invoice=payload)
		invoices = get_returnable_invoices(
			pos_profile=self.profile.name, search_text=result.get("name")
		)
		names = [row.get("name") for row in (invoices if isinstance(invoices, list)
		                                     else invoices.get("invoices", []))]
		self.assertIn(result.get("name"), names)
```

(Adjust the call signature to the actual `get_returnable_invoices` params — check its `@frappe.whitelist()` signature first.)

- [ ] **Step 4: Run** → both tests pass; rerun `pos_next.api.test_invoices_strip_fields pos_next.api.test_invoices_offer_relay` for regression.

- [ ] **Step 5: Commit** — `feat(pos): server-resolved invoice doctype in submit pipeline`

---

### Task 6: Offline dedup across doctypes

**Files:**
- Modify: `pos_next/api/invoices.py` — `_ensure_offline_uniqueness` (1154), `_reuse_sync_record` (1144), `_complete_offline_sync` (1257), `check_offline_invoice_synced` (1311), existence checks at 1210–1211 and 1336–1337
- Modify: `pos_next/pos_next/doctype/offline_invoice_sync/offline_invoice_sync.py` (if it hardcodes `sales_invoice` on save)
- Test: `pos_next/api/test_pos_invoice_submit.py` (extend)

- [ ] **Step 1: Write the failing test:**

```python
	def test_offline_dedup_cross_doctype(self):
		offline_id = "pos_offline_test_xmode1"
		frappe.db.delete("Offline Invoice Sync", {"offline_id": offline_id})
		payload = {
			"pos_profile": self.profile.name, "posa_pos_opening_shift": self.shift.name,
			"customer": self.customer, "offline_id": offline_id,
			"items": [{"item_code": self.item, "qty": 1, "rate": 100,
			           "warehouse": self.profile.warehouse}],
			"payments": [{"mode_of_payment": self._mode[0], "amount": 100}],
		}
		first = submit_invoice(invoice=payload)
		frappe.db.commit()
		second = submit_invoice(invoice=payload)  # replay must return the same invoice
		self.assertEqual(first.get("name"), second.get("name"))
		sync = frappe.db.get_value("Offline Invoice Sync", {"offline_id": offline_id},
		                           ["pos_invoice", "sales_invoice"], as_dict=1)
		self.assertEqual(sync.pos_invoice, first.get("name"))
		frappe.db.delete("Offline Invoice Sync", {"offline_id": offline_id})
```

- [ ] **Step 2: Run** → fails (second submit duplicates or sync row lacks `pos_invoice`).

- [ ] **Step 3: Implement.** Everywhere the sync record's invoice is read/written, branch on which column is set:

```python
def _sync_invoice_field(doctype):
	return "pos_invoice" if doctype == "POS Invoice" else "sales_invoice"
```

- `_ensure_offline_uniqueness` / `_complete_offline_sync`: write the created invoice name into `_sync_invoice_field(doctype)` (pass the doctype down or read `get_pos_invoice_doctype()`).
- Existence checks (1210–1211, 1336–1337): resolve the actual invoice by trying both columns — `invoice_name = existing_sync.sales_invoice or existing_sync.pos_invoice`, then `frappe.db.exists(existing_sync.sales_invoice and "Sales Invoice" or "POS Invoice", invoice_name)` — implement as a small helper `_sync_existing_invoice(sync) -> tuple[str, str] | None` returning `(doctype, name)`.
- `check_offline_invoice_synced` returns whichever exists.

- [ ] **Step 4: Run** → pass; also `pos_next.api.test_session_summary` regression.

- [ ] **Step 5: Commit** — `feat(pos): offline invoice dedup across Sales/POS Invoice`

---

### Task 7: Effective stock (unconsolidated POS Invoice deduction)

**Files:**
- Create: helper in `pos_next/invoice_type.py` — `get_unconsolidated_posi_qty(warehoused_by_item: dict) -> dict`
- Modify: `pos_next/api/invoices.py` — `_get_available_stock` (511) and `_collect_stock_errors` (528) call path
- Modify: `pos_next/api/items.py` — `get_item_stock`, `get_stock_quantities`, `get_item_warehouse_availability` (grep `def get_.*stock` for the full list)
- Test: `pos_next/api/test_pos_invoice_submit.py` (extend)

- [ ] **Step 1: Write the failing test:**

```python
	def test_effective_stock_blocks_oversell(self):
		from pos_next.api.invoices import validate_cart_items

		# submit one invoice consuming qty 5 of the stocked item (Bin above)
		payload = {
			"pos_profile": self.profile.name, "posa_pos_opening_shift": self.shift.name,
			"customer": self.customer,
			"items": [{"item_code": self.item, "qty": 5, "rate": 100,
			           "warehouse": self.profile.warehouse}],
			"payments": [{"mode_of_payment": self._mode[0], "amount": 500}],
		}
		submit_invoice(invoice=payload)
		frappe.db.commit()
		# Bin still shows 5 (no SLE until consolidation) — cart validation must
		# see effective qty 0 and reject another unit.
		with self.assertRaises(frappe.ValidationError):
			validate_cart_items(
			 [{"item_code": self.item, "qty": 1, "warehouse": self.profile.warehouse}],
			 pos_profile=self.profile.name,
			)
```

- [ ] **Step 2: Run** → fails (Bin unchanged, validation passes).

- [ ] **Step 3: Implement.** In `invoice_type.py`:

```python
def get_unconsolidated_posi_qty(item_codes, warehouse):
	"""Sold-but-unconsolidated POS Invoice qty per item for a warehouse.
	SLEs only appear at consolidation, so this is the intraday reservation."""
	if get_pos_invoice_doctype() != POS_INVOICE or not item_codes:
		return {}
	data = frappe.db.sql(
		"""
		select item.item_code, sum(item.stock_qty)
		from `tabPOS Invoice` inv, `tabPOS Invoice Item` item
		where item.parent = inv.name
		  and inv.docstatus = 1 and ifnull(inv.consolidated_invoice,'') = ''
		  and ifnull(inv.is_return, 0) = 0
		  and item.warehouse = %(warehouse)s
		  and item.item_code in %(items)s
		group by item.item_code
		""",
		{"warehouse": warehouse, "items": item_codes},
	)
	return {item_code: qty for item_code, qty in data}
```

Apply in `_get_available_stock` (subtract from Bin qty), and in `items.py` stock endpoints (same subtraction where available qty is computed for display/checkout; find them with `grep -n "actual_qty\|Bin" pos_next/api/items.py`). POS Next–owned filter is not needed here — built-in-POS unconsolidated sales must also reduce effective stock.

- [ ] **Step 4: Run** → pass; regression `pos_next.api.test_packages`.

- [ ] **Step 5: Commit** — `feat(pos): effective stock subtracts unconsolidated POS Invoices`

---

### Task 8: Closing shift — POS Invoice transactions + real consolidation

**Files:**
- Modify: `pos_next/pos_next/doctype/pos_closing_shift/pos_closing_shift.py` — `get_pos_invoices` (~354), `make_closing_shift_from_opening` (~519), `_process_invoice` (verify it writes `invoice_field`), `on_submit` (~75), `submit_closing_shift` if separate (~632)
- Test: `pos_next/tests/test_pos_invoice_closing.py`

**Interfaces (consumes):** Task 2 `pos_invoice` column on Sales Invoice Reference; ERPNext `consolidate_pos_invoices(pos_invoices=[{"pos_invoice": name, "is_return": 0|1}], closing_entry=None)` (synchronous without closing_entry).

- [ ] **Step 1: Write the failing test:**

```python
import frappe
from frappe.tests.utils import FrappeTestCase

from pos_next.api.invoices import submit_invoice
from pos_next.invoice_type import POS_INVOICE


class TestClosingConsolidation(FrappeTestCase):
	# setUp/tearDown mirror TestSubmitInvoicePOSIMode (shift + submitted POSI);
	# factor the helpers into a shared _posi_test_utils.py module in the same
	# directory and import from both test files.

	def test_close_shift_consolidates(self):
		# setUp submitted one POSI with customer C
		posi_name = frappe.get_all("POS Invoice",
			{"posa_pos_opening_shift": self.shift.name}, pluck="name")[0]
		customer = frappe.db.get_value("POS Invoice", posi_name, "customer")

		closing = frappe.call(
			"pos_next.pos_next.doctype.pos_closing_shift.pos_closing_shift"
			".make_closing_shift_from_opening",
			opening_shift=frappe.json.dumps({"name": self.shift.name}),
		)
		closing_doc = frappe.get_doc(closing)
		closing_doc.insert(ignore_permissions=True)
		closing_doc.submit()

		posi = frappe.get_doc("POS Invoice", posi_name)
		self.assertTrue(posi.consolidated_invoice)
		cons = frappe.get_doc("Sales Invoice", posi.consolidated_invoice)
		self.assertEqual(cint(cons.is_consolidated), 1)
		self.assertGreater(frappe.db.count("GL Entry",
			{"voucher_no": cons.name}), 0)
		self.assertGreater(frappe.db.count("Stock Ledger Entry",
			{"voucher_no": cons.name}), 0)
		log = frappe.db.exists("POS Invoice Merge Log", {"pos_invoice": posi_name})
		self.assertTrue(log)
		frappe.db.commit()
```

- [ ] **Step 2: Run** → fails (`consolidated_invoice` empty — consolidation never called).

- [ ] **Step 3: Implement.**

1. `get_pos_invoices`: drop the `use_pos_invoice = False` stub — `doctype = doctype or get_pos_invoice_doctype()`.
2. `make_closing_shift_from_opening` (~519): `doctype = get_pos_invoice_doctype()`; `invoice_field = "pos_invoice" if doctype == "POS Invoice" else "sales_invoice"`. Verify `_process_invoice(...)` populates `txn[invoice_field] = invoice.name` (grep `invoice_field` in the file; if it always writes `sales_invoice`, fix it to use the variable).
3. `on_submit` — after `_set_closing_entry_invoices()`:

```python
		if self._has_pos_invoice_transactions():
			self._consolidate_pos_invoices()

	def _has_pos_invoice_transactions(self):
		return any(d.get("pos_invoice") for d in self.pos_transactions)

	def _consolidate_pos_invoices(self):
		from erpnext.accounts.doctype.pos_invoice_merge_log.pos_invoice_merge_log import (
			consolidate_pos_invoices,
		)

		invoices = [
			{"pos_invoice": d.pos_invoice,
			 "is_return": cint(frappe.db.get_value("POS Invoice", d.pos_invoice, "is_return"))}
			for d in self.pos_transactions
			if d.get("pos_invoice")
		]
		consolidate_pos_invoices(pos_invoices=invoices)
```

(Existing `_clear_closing_entry_invoices` already cancels merge logs on reopen — no change.)

- [ ] **Step 4: Run** → pass. Also rerun the existing closing-shift test module if present (`grep -rl "Closing Shift" pos_next/pos_next/doctype/pos_closing_shift/`) and `pos_next.api.test_session_summary`.

- [ ] **Step 5: Commit** — `feat(pos): closing shift consolidates POS Invoices into Sales Invoices`

---

### Task 9: Wallet doctype-awareness + reports/HQ union

**Files:**
- Modify: `pos_next/api/wallet.py:84` (hardcoded `invoice_type: "Sales Invoice"`)
- Modify: `pos_next/api/hq_monitoring.py` (11 "Sales Invoice" refs), `pos_next/pos_next/report/sales_vs_shifts_report/sales_vs_shifts_report.py` (16), `cashier_performance_report` (4), `inventory_impact_and_fast_movers_report` (3), `offline_sync_and_system_health_report` (3), `payments_and_cash_control_report` (2); session summary in `pos_next/api/shifts.py` (grep `Sales Invoice`)
- Test: `pos_next/tests/test_pos_invoice_reports.py`

- [ ] **Step 1: Write the failing test** — seed one submitted POSI + one legacy SI (same company, both non-consolidated), then:

```python
	def test_report_union_counts_once(self):
		# after setUp: 1 POSI (unconsolidated) + 1 plain SI, same company/today
		from pos_next.pos_next.doctype.sales_vs_shifts_report.sales_vs_shifts_report import (
			execute,
		)

		columns, data = execute(filters={"company": self.company,
		                                 "from_date": frappe.utils.today(),
		                                 "to_date": frappe.utils.today()})
		total = sum(flt(row.get("grand_total") or 0) for row in data
		            if isinstance(row, dict))
		self.assertEqual(total, 200)  # 100 + 100, counted once each
```

- [ ] **Step 2: Run** → fails (POS Invoice row missing → 100).

- [ ] **Step 3: Implement.** Transformation pattern applied to each file's query builder:

```python
from pos_next.invoice_type import SALES_INVOICE, get_sales_report_doctypes

total_by_column = {}
for dt in get_sales_report_doctypes():
	doc = frappe.qb.DocType(dt)
	q = (  # the file's existing query, with `doc` in place of the old DocType("Sales Invoice")
		frappe.qb.from_(doc).select(...)
		# ... unchanged body ...
	)
	if dt == SALES_INVOICE:
		q = q.where(IfNull(doc.is_consolidated, 0) == 0)
	rows = q.run(as_dict=True)
	# aggregate/extend the report's result structure per its existing shape
```

Apply at every `frappe.qb.DocType("Sales Invoice")` / `"Sales Invoice"` filter in the six files + `hq_monitoring.py` + `shifts.py` session summary (grep anchors: `grep -n "Sales Invoice" <file>`). Column names are identical across both doctypes (shared schema) — no column mapping needed. `wallet.py:84`: `{"invoice_type": doc.doctype, "invoice": doc.name, ...}`.

- [ ] **Step 4: Run** → pass; regression: `pos_next.tests.test_hq_monitoring`.

- [ ] **Step 5: Commit** — `feat(pos): reports and HQ read POS Invoice + legacy Sales Invoice union`

---

### Task 10: Frontend awareness

**Files:**
- Modify: `pos_next/api/bootstrap.py` — `_get_pos_settings` (~195): `settings["invoice_type"] = get_pos_invoice_doctype()`
- Modify: `POS/src/stores/posSettings.js` — default `invoice_type: "Sales Invoice"` (line ~24 area) + `const isPosInvoiceMode = computed(() => settings.value.invoice_type === "POS Invoice")` (export ~388)
- Modify: `POS/src/components/sale/PaymentDialog.vue` + `POS/src/composables/usePaymentCalculations.js` — credit-sale / pay-on-receivable affordances gated on `allowCreditSale && !isPosInvoiceMode` (grep `allowCreditSale` and `receivable` in both files for the exact spots)
- Modify: invoice history/returns dialogs — Desk deep-links use `invoice.doctype || "Sales Invoice"` (grep `sales-invoice` in `POS/src/components/sale/` and `POS/src/components/invoices/`)
- Test: `cd POS && npm run build` (assert exit 0); backend test asserting bootstrap exposes the key:

```python
	def test_bootstrap_exposes_invoice_type(self):
		from pos_next.api.bootstrap import get_initial_data
		# authenticated context is unavailable in unit runner; test the helper path
		from pos_next.api.bootstrap import _get_pos_settings
		profile = frappe.get_cached_doc("POS Profile", self.profile.name)
		settings = _get_pos_settings(profile)
		self.assertIn(settings["invoice_type"], ("Sales Invoice", "POS Invoice"))
```

- [ ] Steps: implement the four file groups above → `npm run build` → run backend test → commit `feat(pos): expose invoice type to POS UI, hide credit paths in POS Invoice mode`.

---

### Task 11: Full regression + spec checklist

- [ ] Run the entire suite: `./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py` with every module listed in previous tasks plus the pre-existing ones found by `ls pos_next/api/test_*.py pos_next/test_*.py pos_next/tests/test_*.py`. All pass in Sales Invoice default mode.
- [ ] Flip the site setting to `POS Invoice`, run `pos_next.api.test_pos_invoice_submit pos_next.tests.test_pos_invoice_closing pos_next.tests.test_pos_invoice_reports`, flip back. All pass.
- [ ] Walk the spec's Goal/Non-Goals list one by one; note deviations in the PR description.
- [ ] Commit any fixups; merge `feat/pos-invoice-doctype` → `main` only after user approval.
