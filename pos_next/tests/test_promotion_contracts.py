"""Promotions domain contract tests (Task 3).

Asserts that ZERO functions or methods under ``pos_next/promotions/*.py``
carry a ``@frappe.whitelist`` / ``@whitelist`` decorator, and that the ``Promotion``
master carries exactly the role permissions design section 18 mandates.

The AST scan covers the literal decorator text used in this repository:
``@frappe.whitelist``, ``@whitelist``, or any attribute whose last segment
is ``whitelist``.
"""

import ast
import json
from pathlib import Path

import frappe
from frappe.tests import IntegrationTestCase

# Design section 18 as landed in pos_next: full authoring rights for System
# Manager and Administrator (pos_next has no Sales Manager equivalent that may
# delete); Nexus POS Manager manages masters but may not delete. POSNext
# Cashier is read-only — the minimum the public promotion discovery and quote
# wrappers need from the DocPerm gate; master editing is a manager concern
# (orchestrator ruling: source-faithful, no cashier-editable masters).
PROMOTION_ROLE_RIGHTS = {
	"System Manager": {"read": 1, "write": 1, "create": 1, "delete": 1},
	"Administrator": {"read": 1, "write": 1, "create": 1, "delete": 1},
	"Nexus POS Manager": {"read": 1, "write": 1, "create": 1, "delete": 0},
	"POSNext Cashier": {"read": 1, "write": 0, "create": 0, "delete": 0},
}
PROMOTION_CHILD_DOCTYPES = (
	"promotion_component",
	"promotion_choice_group",
	"promotion_option",
	"promotion_outlet",
	"pos_promotion_selection",
)


class TestPromotionContracts(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.db.rollback)

	def _doctype_json(self, folder: str) -> dict:
		path = (
			Path(frappe.get_app_path("pos_next"))
			/ "pos_next"
			/ "doctype"
			/ folder
			/ f"{folder}.json"
		)
		self.assertTrue(path.is_file(), msg=f"DocType JSON not found: {path}")
		return json.loads(path.read_text(encoding="utf-8"))

	def test_promotion_master_permissions_match_design(self):
		"""The shipped JSON, not the database: this is the definition the migrate imports."""
		perms = self._doctype_json("promotion")["permissions"]

		self.assertEqual(
			[row["role"] for row in perms],
			list(PROMOTION_ROLE_RIGHTS),
			msg=f"Promotion permission roles mismatch: {perms}",
		)
		for row in perms:
			for right, expected in PROMOTION_ROLE_RIGHTS[row["role"]].items():
				self.assertEqual(
					int(row.get(right) or 0),
					expected,
					msg=f"Promotion permission {row['role']}.{right} is {row.get(right)!r}, expected {expected}",
				)

	def test_promotion_master_grants_no_other_role(self):
		"""Only the explicit master and POS discovery roles gain access."""
		roles = {row["role"] for row in self._doctype_json("promotion")["permissions"]}
		self.assertEqual(
			roles - set(PROMOTION_ROLE_RIGHTS),
			set(),
			msg=f"Promotion grants unexpected roles: {sorted(roles - set(PROMOTION_ROLE_RIGHTS))}",
		)

	def test_promotion_children_carry_no_permission_rows(self):
		"""Child tables inherit the parent's permissions; their own rows would diverge."""
		for folder in PROMOTION_CHILD_DOCTYPES:
			definition = self._doctype_json(folder)
			self.assertEqual(int(definition.get("istable") or 0), 1, msg=f"{folder} must stay a child table")
			self.assertEqual(
				definition.get("permissions"),
				[],
				msg=f"{folder} must carry no permission rows, got: {definition.get('permissions')}",
			)

	def test_promotion_permissions_are_live_on_this_site(self):
		"""The imported definition, so a stale JSON that never synced still fails."""
		live = frappe.get_all(
			"DocPerm",
			filters={"parent": "Promotion", "parenttype": "DocType"},
			fields=["role", "read", "write", "create", "delete"],
		)
		self.assertEqual(
			{row["role"] for row in live},
			set(PROMOTION_ROLE_RIGHTS),
			msg=f"live Promotion DocPerm roles mismatch: {live}",
		)
		for row in live:
			for right, expected in PROMOTION_ROLE_RIGHTS[row["role"]].items():
				self.assertEqual(
					int(row.get(right) or 0),
					expected,
					msg=f"live Promotion {row['role']}.{right} is {row.get(right)!r}, expected {expected}",
				)

	def test_no_whitelisted_functions_in_promotions_package(self):
		promotions_dir = Path(frappe.get_app_path("pos_next")) / "promotions"
		self.assertTrue(promotions_dir.is_dir(), msg=f"promotions dir not found: {promotions_dir}")

		offending: list[str] = []
		for py_file in promotions_dir.glob("*.py"):
			source = py_file.read_text(encoding="utf-8")
			try:
				tree = ast.parse(source, filename=str(py_file))
			except SyntaxError as err:
				self.fail(f"Syntax error in {py_file}: {err}")
				continue

			for node in ast.walk(tree):
				if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
					for deco in getattr(node, "decorator_list", []):
						if _is_whitelist_decorator(deco):
							offending.append(
								f"{py_file.name}:{node.name} has a @whitelist decorator (line {node.lineno})"
							)

		self.assertEqual(
			offending,
			[],
			msg="Whitelisted functions are forbidden under pos_next/promotions/: "
			+ "; ".join(offending),
		)


def _is_whitelist_decorator(deco) -> bool:
	"""Return True if the decorator node represents a whitelist decorator."""
	# Handles: @whitelist, @frappe.whitelist(...), @frappe.whitelist, @whitelist(...)
	# decorator is the node WITHOUT the @. Could be Name, Attribute, or Call.
	target = deco

	# Unwrap Call: @frappe.whitelist(...)
	if isinstance(target, ast.Call):
		target = target.func

	# Attribute: frappe.whitelist -> "whitelist" == attr
	if isinstance(target, ast.Attribute):
		return target.attr == "whitelist"

	# Name: @whitelist
	if isinstance(target, ast.Name):
		return target.id == "whitelist"

	return False
