"""Hook-side contract test for walk-in customer wiring.

SCOPE DROP (port of selling_additional's test_walk_in_asset.py):
The source module was a 12-test contract suite over a browser asset,
``public/js/pos_walk_in_customer.js``, that mounted a walk-in input into ERPNext's Desk
POS page (frappe.ui.PointOfSale) via a MutationObserver: file-existence, forbidden-pattern
scans, selector pins, observer lifecycle ordering, duplicate-guard regexes, and a
hooks.page_js registration pin. pos_next does not ship that asset and does not extend the
Desk POS page at all -- the POS front end is the Vue SPA under ``POS/``, whose walk-in
customer UI is built in SPA tasks 2.9 (PaymentDialog buyer-name field) and 2.13 (checkout
integration). Every browser-asset assertion therefore tests a surface that no longer exists
in this app and is dropped rather than ported. What remains in scope is the hook-side
contract: the server validator is registered on Sales Invoice.validate in the hooks module,
no Desk POS page/app JS re-introduces the retired asset wiring, and the function itself is
importable and behaves as the doc-event handler.
"""

import unittest

import frappe

HOOK_KEY = "pos_next.walk_in.validate_walk_in_customer_name"


class TestWalkInHookContract(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		import pos_next.hooks as hooks

		cls.hooks = hooks

	def test_validator_registered_on_sales_invoice_validate(self):
		"""hooks.doc_events['Sales Invoice']['validate'] must include the walk-in handler path."""
		handlers = self.hooks.doc_events.get("Sales Invoice", {}).get("validate") or []
		if isinstance(handlers, str):
			handlers = [handlers]
		self.assertIn(
			HOOK_KEY,
			handlers,
			msg=f"{HOOK_KEY} is not registered as a Sales Invoice.validate handler: {handlers}",
		)

	def test_hook_path_resolves_to_the_validator(self):
		"""The registered dotted path must import cleanly and be callable with (doc, method)."""
		resolved = frappe.get_attr(HOOK_KEY)
		self.assertTrue(callable(resolved), msg=f"{HOOK_KEY} resolved to a non-callable: {resolved!r}")

	def test_no_desk_pos_page_js_asset_wiring(self):
		"""The retired Desk POS walk-in asset must not be re-registered through page_js.

		Source counterpart: test_hooks_registration_and_no_app_include_js pinned
		page_js['point-of-sale'][0] == 'public/js/pos_walk_in_customer.js'. Retargeted as the
		inverse: that asset no longer exists, so no page_js entry may reference it.
		"""
		page_js = getattr(self.hooks, "page_js", {}) or {}
		for page, entries in page_js.items():
			if isinstance(entries, str):
				entries = [entries]
			for entry in entries:
				self.assertNotIn(
					"pos_walk_in_customer",
					str(entry),
					msg=f"Retired Desk POS walk-in asset re-registered under page_js[{page!r}]: {entry}",
				)

	def test_app_include_js_absent_or_without_walk_in_asset(self):
		"""app_include_js must stay falsy or, if set, must not load the retired walk-in asset."""
		app_include_js = getattr(self.hooks, "app_include_js", None)
		if isinstance(app_include_js, str):
			entries = [app_include_js]
		else:
			entries = list(app_include_js or [])
		for entry in entries:
			self.assertNotIn(
				"pos_walk_in_customer",
				str(entry),
				msg=f"Retired walk-in asset loaded globally via app_include_js: {entry}",
			)
