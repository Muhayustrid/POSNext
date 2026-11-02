import frappe
from frappe.utils import flt


def shift_spend(opening_shift: str, *, for_update=False) -> list[dict]:
	"""Net drawer credits on the account stamped when the voucher was submitted."""
	# MariaDB snapshot isolation otherwise rejects current reads of newly committed vouchers.
	current_read = "SET STATEMENT innodb_snapshot_isolation=0 FOR" if for_update else ""
	lock = "FOR UPDATE" if for_update else ""
	rows = []
	for doctype in ("Purchase Invoice", "Journal Entry", "Payment Entry"):
		rows.extend(
			frappe.db.sql(
				f"""{current_read}
			SELECT %s AS voucher_type, v.name AS voucher_no, v.mode_of_payment,
				v.posa_shift_account AS account,
				SUM(gl.credit_in_account_currency - gl.debit_in_account_currency) AS amount
			FROM `tab{doctype}` v
			JOIN `tabGL Entry` gl ON gl.voucher_type=%s AND gl.voucher_no=v.name
				AND gl.account=v.posa_shift_account AND gl.is_cancelled=0
			WHERE v.docstatus=1 AND v.posa_pos_opening_shift=%s
			GROUP BY v.name, v.mode_of_payment, v.posa_shift_account
			{lock}
		""",
				(doctype, doctype, opening_shift),
				as_dict=True,
			)
		)
	for row in rows:
		row.amount = flt(row.amount)
		if for_update:
			# Frappe's Dynamic Link validation must not re-read this voucher through the old snapshot.
			frappe.db.value_cache[row.voucher_type][row.voucher_no][("name", "docstatus")] = [
				frappe._dict(name=row.voucher_no, docstatus=1)
			]
	return rows


def shift_spend_by_mode(opening_shift) -> dict[str, float]:
	amounts = {}
	for row in shift_spend(opening_shift):
		amounts[row.mode_of_payment] = flt(amounts.get(row.mode_of_payment, 0) + row.amount)
	return amounts
