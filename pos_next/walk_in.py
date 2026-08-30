import frappe
from frappe import _
from frappe.utils import cint


def validate_walk_in_customer_name(doc, method=None) -> None:
	# Port deviation: the source guarded on `is_created_using_pos` because that app
	# ran on a tree where ERPNext's Desk POS set the flag; pos_next is a
	# Sales-Invoice-only tree where no code sets it, so the guard silently disabled
	# this validation. The function body is self-gating (empty buyer_name returns
	# immediately), so the early-return is dropped rather than replaced.
	name = (doc.get("buyer_name") or "").strip()
	if not name:
		return

	profile_name = doc.get("pos_profile")
	profile = (
		frappe.db.get_value(
			"POS Profile",
			profile_name,
			["customer", "disabled"],
			as_dict=True,
		)
		if profile_name
		else None
	)
	if not profile:
		frappe.throw(_("A walk-in customer name requires an existing POS Profile."))
	if cint(profile.disabled):
		frappe.throw(_("POS Profile {0} is disabled.").format(profile_name))
	if not profile.customer:
		frappe.throw(_("POS Profile {0} has no default Customer.").format(profile_name))

	customer_disabled = frappe.db.get_value("Customer", profile.customer, "disabled")
	if customer_disabled is None:
		frappe.throw(_("Default Customer {0} does not exist.").format(profile.customer))
	if cint(customer_disabled):
		frappe.throw(_("Default Customer {0} is disabled.").format(profile.customer))
	if doc.get("customer") != profile.customer:
		frappe.throw(
			_("A walk-in customer name applies only to default Customer {0}.").format(profile.customer)
		)
