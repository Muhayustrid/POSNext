# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Effective cash Mode of Payment of a POS Profile.

Outlet cash drawers each get their own Mode of Payment ("Cash PKU DELANGGU"),
so the generic "Cash" is only a last-resort fallback — subtracting change from
it while takings land on the outlet's own row minted a phantom second cash row
with a negative expected amount in the closing reconciliation. The outlet model
is heading per-company, but a company keeps several POS Profiles, so this stays
a per-profile resolution reading that profile's own payment rows.
"""

import frappe


def get_cash_mode_of_payment(pos_profile):
	"""Cash mode for drawer math (change netting, cash classification).

	Chain: the profile's explicit ``posa_cash_mode_of_payment`` field, then
	the profile's own Cash-type payment row (the default row first, then
	profile order), then the generic "Cash".
	"""
	if pos_profile and frappe.db.has_column("POS Profile", "posa_cash_mode_of_payment"):
		# legacy posawesome field: absent on self-standing sites, where the
		# payment rows below are the real chain
		configured = frappe.db.get_value("POS Profile", pos_profile, "posa_cash_mode_of_payment")
		if configured:
			return configured

		rows = frappe.get_all(
			"POS Payment Method",
			filters={"parent": pos_profile, "parenttype": "POS Profile"},
			fields=["mode_of_payment", "default"],
			order_by="idx asc",
		)
		if rows:
			cash_rows = set(
				frappe.get_all(
					"Mode of Payment",
					filters={
						"name": ["in", [row.mode_of_payment for row in rows]],
						"type": "Cash",
					},
					pluck="name",
				)
			)
			for row in rows:
				if row.default and row.mode_of_payment in cash_rows:
					return row.mode_of_payment
			for row in rows:
				if row.mode_of_payment in cash_rows:
					return row.mode_of_payment

	return "Cash"
