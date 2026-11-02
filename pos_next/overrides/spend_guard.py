import frappe
from frappe import _


def before_cancel(doc, method=None):
	shift = doc.get("posa_pos_opening_shift")
	if not shift:
		return
	# Current reads see the winning closing without MariaDB rejecting the older snapshot.
	frappe.db.sql(
		"""SET STATEMENT innodb_snapshot_isolation=0 FOR
		SELECT name FROM `tabPOS Opening Shift` WHERE name=%s FOR UPDATE""",
		shift,
	)
	closing = frappe.db.sql(
		"""SET STATEMENT innodb_snapshot_isolation=0 FOR
		SELECT name FROM `tabPOS Closing Shift`
		WHERE pos_opening_shift=%s AND docstatus=1 LIMIT 1 FOR UPDATE""",
		shift,
	)
	if closing:
		frappe.throw(
			_(
				"This spend belongs to a closed shift ({0}). Record a correction instead of cancelling."
			).format(closing[0][0])
		)
