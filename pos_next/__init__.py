try:
	import frappe
except ModuleNotFoundError:  # pragma: no cover - frappe may not be installed during setup
	frappe = None

__version__ = "2.16.0"


def console(*data):
	"""Publish data to browser console for debugging"""
	if frappe:
		frappe.publish_realtime("toconsole", data, user=frappe.session.user)


# Patch get_other_conditions to exclude pos_only pricing rules from non-POS documents.
# No Frappe hook exists for non-whitelisted module-level functions (override_whitelisted_methods
# only works for @frappe.whitelist() HTTP endpoints, override_doctype_class only for DocType
# classes). This is the standard Python module init approach — runs once at import.
try:
	from erpnext.accounts.doctype.pricing_rule import utils as pr_utils

	from pos_next.overrides.pricing_rule import patch_get_other_conditions

	patch_get_other_conditions(pr_utils)
except Exception:
	pass


try:
	from erpnext.accounts.doctype.pricing_rule import pricing_rule as _erpnext_pricing_rule

	from pos_next.overrides.pricing_rule import (
		apply_price_discount_rule as _pos_next_apply_price_discount_rule,
	)

	_erpnext_pricing_rule.apply_price_discount_rule = _pos_next_apply_price_discount_rule
except Exception:
	if frappe:
		frappe.log_error(frappe.get_traceback(), "Pricing Rule Override Error")


try:
	from erpnext.accounts.doctype.promotional_scheme import promotional_scheme as _promotional_scheme

	for _min_max_field in ("apply_discount_on_price", "min_or_max_discount_qty_limit"):
		if _min_max_field not in _promotional_scheme.price_discount_fields:
			_promotional_scheme.price_discount_fields.append(_min_max_field)
except Exception:
	if frappe:
		frappe.log_error(frappe.get_traceback(), "Promotional Scheme Field Patch Error")

# Frappe/ERPNext compatibility shim:
# ERPNext may pass do_not_round_fields to round_floats_in, but older Frappe
# versions don't accept that kwarg.
try:
	from frappe.model.document import Document

	from pos_next.overrides.frappe_compat import patch_round_floats_in_signature

	patch_round_floats_in_signature(Document)
except Exception:
	pass

# Patch packed item keying to avoid duplicate Product Bundle rows in Packed Items
# during repeated save/submit cycles in POS flows.
try:
	from erpnext.stock.doctype.packed_item import packed_item as packed_item_module

	from pos_next.overrides.packed_item import patch_packed_item_keying

	patch_packed_item_keying(packed_item_module)
except Exception:
	pass

# Patch POS reserved qty: POS Next POS Invoices already moved stock at submit,
# so ERPNext must not subtract them again as "reserved" (false negative stock).
try:
	from erpnext.accounts.doctype.pos_invoice import pos_invoice as pos_invoice_module

	from pos_next.overrides.pos_reserved_qty import patch_pos_reserved_qty

	patch_pos_reserved_qty(pos_invoice_module)
except Exception:
	if frappe:
		frappe.log_error(frappe.get_traceback(), "POS Reserved Qty Patch Error")

# Patch Document.round_floats_in for ERPNext/Frappe compatibility:
# newer ERPNext may pass do_not_round_fields, while older Frappe
# only supports fieldnames.
try:
	from frappe.model import document as document_module

	from pos_next.overrides.rounding_compat import patch_round_floats_in_compat

	patch_round_floats_in_compat(document_module)
except Exception:
	pass


# frappe 16.36: WorkspaceSidebar.get_can_read_items forgets to return, so
# get_cached() stores None and every DocType/Report sidebar link (POS Invoice,
# ...) drops out for non-Administrator users. Guarded no-op once frappe drops
# the buggy method (develop already rewrote the class).
def _apply_workspace_sidebar_hotfix():
	from frappe.desk.doctype.workspace_sidebar.workspace_sidebar import WorkspaceSidebar

	if "get_can_read_items" not in vars(WorkspaceSidebar):
		return

	def get_can_read_items(self):
		if not self.user.can_read:
			self.user.build_permissions()
		return self.user.can_read

	WorkspaceSidebar.get_can_read_items = get_can_read_items


if frappe:
	try:
		_apply_workspace_sidebar_hotfix()
	except Exception:
		pass
