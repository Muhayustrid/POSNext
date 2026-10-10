from __future__ import annotations

from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal

import frappe
from frappe.query_builder import DocType
from frappe.utils import flt
from pypika.functions import Sum

from pos_next.invoice_type import package_components, package_sold_amount, package_sold_row_filter

_TAX_KEYWORDS = ("PPN", "TAX", "VAT", "PAJAK")
_SERVICE_KEYWORDS = ("SERVICE", "JASA", "CHARGE")


def format_rupiah(value) -> str:
	amount = flt(value)
	whole = int(Decimal(str(amount)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
	return f"{'-' if whole < 0 else ''}Rp{abs(whole):,}".replace(",", ".")


def _can_read_closing(closing) -> bool:
	"""SEC-16: recap helpers must not leak another shift's takings. Read
	permission passes; otherwise the shift's own cashier may still print the
	recap of their own shift."""
	if frappe.has_permission("POS Closing Shift", "read", doc=closing):
		return True
	return closing.get("owner") == frappe.session.user


def _as_closing_doc(doc):
	if isinstance(doc, str):
		closing = frappe.get_doc("POS Closing Shift", doc)
	else:
		closing = doc
	# Deny by returning an empty structure, never by throwing: these are
	# jinja print-format helpers and must render a blank recap instead of
	# aborting the whole print.
	if closing is None or not _can_read_closing(closing):
		return frappe.get_doc({"doctype": "POS Closing Shift"})
	return closing


def _collect_parent_targets(pos_transactions: Iterable) -> set[tuple[str, str]]:
	sales_invoice_targets: set[tuple[str, str]] = set()
	pos_invoices: set[str] = set()

	for row in pos_transactions or []:
		sales_invoice = row.get("sales_invoice")
		pos_invoice = row.get("pos_invoice")

		if sales_invoice:
			sales_invoice_targets.add((sales_invoice, "Sales Invoice"))
			continue

		if pos_invoice:
			pos_invoices.add(pos_invoice)

	return sales_invoice_targets | _get_pos_invoice_parent_targets(pos_invoices)


def _get_pos_invoice_parent_targets(pos_invoices: set[str]) -> set[tuple[str, str]]:
	if not pos_invoices:
		return set()

	targets: set[tuple[str, str]] = set()
	rows = frappe.get_all(
		"POS Invoice",
		filters={"name": ["in", list(pos_invoices)]},
		fields=["name", "consolidated_invoice"],
		limit_page_length=0,
	)

	for row in rows:
		consolidated_invoice = row.get("consolidated_invoice")
		if consolidated_invoice:
			targets.add((consolidated_invoice, "Sales Invoice"))
		else:
			targets.add((row.get("name"), "POS Invoice"))

	return targets


def _sold_item_rows(doctype: str, parents: list[str], group_by: str) -> list[dict]:
	"""Sold qty/amount from ``doctype``'s own item table, grouped by
	``group_by``. Packages count as one line with their instance's money and
	their components are skipped (same rule as HQ and the sales recap, see
	pos_next.invoice_type.package_sold_amount)."""
	return frappe.db.sql(
		f"""
		SELECT {group_by}, SUM(sii.qty) AS qty,
			SUM({package_sold_amount("amount", dt=doctype)}) AS amount
		FROM `tab{doctype} Item` sii
		JOIN `tab{doctype}` si ON si.name = sii.parent
		WHERE sii.parent IN %(parents)s AND {package_sold_row_filter("amount")}
		GROUP BY {group_by}
		""",
		{"parents": parents},
		as_dict=True,
	)


def _fetch_items_for_targets(parent_targets: set[tuple[str, str]]) -> list[dict]:
	"""Items sold, read from each target's OWN child table.

	POS Invoice rows live in `POS Invoice Item` and Sales Invoice rows in
	`Sales Invoice Item`; querying one table for both silently returns nothing
	for the other doctype (the EOD "items sold" section would come out empty).
	"""
	if not parent_targets:
		return []

	items: list[dict] = []
	for doctype in ("Sales Invoice", "POS Invoice"):
		parents = sorted(parent for parent, parenttype in parent_targets if parenttype == doctype)
		if parents:
			items.extend(_sold_item_rows(doctype, parents, "sii.item_code, sii.item_name"))

	# one row per item across both doctypes
	merged: dict[tuple[str, str], dict] = {}
	for row in items:
		key = (row.get("item_code"), row.get("item_name"))
		bucket = merged.setdefault(key, {"item_code": key[0], "item_name": key[1], "qty": 0.0, "amount": 0.0})
		bucket["qty"] += flt(row.get("qty"))
		bucket["amount"] += flt(row.get("amount"))

	return sorted(merged.values(), key=lambda r: r["amount"], reverse=True)


def get_items_sold(doc) -> list[dict]:
	closing_doc = _as_closing_doc(doc)
	parent_targets = _collect_parent_targets(closing_doc.get("pos_transactions"))
	if not parent_targets:
		return []

	items = _fetch_items_for_targets(parent_targets)

	return [
		{
			"item_code": row.get("item_code"),
			"item_name": row.get("item_name"),
			"qty": flt(row.get("qty")),
			"amount": flt(row.get("amount")),
		}
		for row in items
	]


def _is_cash_mode(mode_of_payment) -> bool:
	if not mode_of_payment:
		return False
	return frappe.db.get_value("Mode of Payment", mode_of_payment, "type") == "Cash"


def _build_condition(query, parent_targets: set[tuple[str, str]]):
	condition = None
	for parent, parenttype in sorted(parent_targets):
		current = (query.parent == parent) & (query.parenttype == parenttype)
		condition = current if condition is None else (condition | current)
	return condition


def _fetch_discount_for_targets(parent_targets: set[tuple[str, str]]) -> float:
	if not parent_targets:
		return 0.0

	# item-level discount, summed over each target's own child table
	item_discount = 0.0
	for doctype in ("Sales Invoice", "POS Invoice"):
		targets = {t for t in parent_targets if t[1] == doctype}
		if not targets:
			continue
		child = DocType(f"{doctype} Item")
		discount_sum = Sum((child.price_list_rate - child.rate) * child.qty)
		row = (
			frappe.qb.from_(child)
			.select(discount_sum.as_("discount"))
			.where(_build_condition(child, targets))
			.run(as_dict=True)
		)
		item_discount += flt(row[0].get("discount")) if row else 0.0

	# invoice-level discount
	invoice_discount = 0.0
	for doctype in ("Sales Invoice", "POS Invoice"):
		invoices = [parent for parent, parenttype in parent_targets if parenttype == doctype]
		if not invoices:
			continue
		rows = frappe.get_all(
			doctype,
			filters={"name": ["in", invoices]},
			fields=["discount_amount"],
			limit_page_length=0,
		)
		invoice_discount += sum(flt(row.get("discount_amount")) for row in rows)

	return flt(item_discount + invoice_discount, 2)


def _fetch_grouped_items_for_targets(parent_targets: set[tuple[str, str]]) -> list[dict]:
	if not parent_targets:
		return []

	rows: list[dict] = []
	for doctype in ("Sales Invoice", "POS Invoice"):
		parents = sorted(parent for parent, parenttype in parent_targets if parenttype == doctype)
		if parents:
			# is_return keeps a return on its own line: netted into the sale of
			# the same item it printed as a confusing "0x Item Rp0"
			sold = _sold_item_rows(
				doctype, parents, "sii.item_group, sii.item_code, sii.item_name, si.is_return"
			)
			components = package_components(
				frappe.db.sql(
					f"""
					SELECT sii.parent, sii.item_code, sii.item_name, sii.qty,
						sii.pos_package_role, sii.pos_package_instance
					FROM `tab{doctype} Item` sii
					WHERE sii.parent IN %(parents)s AND sii.pos_package_role IN ('Package', 'Package Item')
					""",
					{"parents": parents},
					as_dict=True,
				)
			)
			for row in sold:
				row["components"] = components.get((row.item_code, bool(row.is_return)), [])
			rows.extend(sold)

	return rows


def _collect_categories(rows: list[dict]) -> list[dict]:
	grouped: dict[str, dict] = {}

	for row in rows:
		category = row.get("item_group") or ""
		bucket = grouped.setdefault(category, {"category": category, "total": 0.0, "items": []})
		amount = flt(row.get("amount"), 2)
		bucket["total"] = flt(bucket["total"] + amount, 2)
		bucket["items"].append(
			{
				"qty": flt(row.get("qty"), 2),
				"item_name": row.get("item_name"),
				"amount": amount,
				"is_return": bool(row.get("is_return")),
				"components": row.get("components") or [],
			}
		)

	for bucket in grouped.values():
		bucket["items"].sort(key=lambda item: item["amount"], reverse=True)

	return sorted(grouped.values(), key=lambda bucket: bucket["total"], reverse=True)


def _classify_charge(account_head) -> str:
	head = (account_head or "").upper()
	if any(keyword in head for keyword in _TAX_KEYWORDS):
		return "tax"
	if any(keyword in head for keyword in _SERVICE_KEYWORDS):
		return "service"
	return "tax"


def _configured_payment_methods(pos_profile):
	"""Every Mode of Payment on the shift's POS Profile, in profile order —
	the same configured-method list the sales recap service reports, so a
	method with no transactions still prints (at 0) on the EOD sheet."""
	if not pos_profile or not frappe.db.exists("POS Profile", pos_profile):
		return []
	return frappe.get_all(
		"POS Payment Method",
		filters={"parent": pos_profile, "parenttype": "POS Profile"},
		fields=["mode_of_payment"],
		order_by="idx asc",
		pluck="mode_of_payment",
	)


def _merge_payment_methods(methods, configured):
	"""Configured methods first (profile order, zero-filled); methods already
	kept keep their amount and is_cash. Methods used on the shift but no
	longer on the profile follow, so no taking disappears from the sheet."""
	by_mode = {}
	for method in methods:
		by_mode.setdefault(method["mode_of_payment"], method)

	merged = []
	seen = set()
	for mode in configured:
		seen.add(mode)
		if mode in by_mode:
			merged.append(by_mode[mode])
		else:
			merged.append(
				{
					"mode_of_payment": mode,
					"amount": 0.0,
					"is_cash": _is_cash_mode(mode),
				}
			)
	for mode, method in by_mode.items():
		if mode not in seen:
			merged.append(method)
	return merged


def get_sales_recap(doc) -> dict:
	closing_doc = _as_closing_doc(doc)
	transactions = closing_doc.get("pos_transactions") or []
	parent_targets = _collect_parent_targets(transactions)

	payment_methods: list[dict] = []
	total_cash = total_non_cash = 0.0
	opening_balance = cash_payment = cash_in_hand = 0.0

	for row in closing_doc.get("payment_reconciliation") or []:
		mode = row.get("mode_of_payment")
		opening = flt(row.get("opening_amount"))
		amount = flt(flt(row.get("expected_amount")) - opening, 2)
		is_cash = _is_cash_mode(mode)

		payment_methods.append({"mode_of_payment": mode, "amount": amount, "is_cash": is_cash})
		if is_cash:
			total_cash = flt(total_cash + amount, 2)
			opening_balance = flt(opening_balance + opening, 2)
			cash_payment = flt(cash_payment + amount, 2)
			cash_in_hand = flt(cash_in_hand + flt(row.get("closing_amount")), 2)
		else:
			total_non_cash = flt(total_non_cash + amount, 2)

	payment_methods = _merge_payment_methods(
		payment_methods, _configured_payment_methods(closing_doc.get("pos_profile"))
	)

	service_charge = tax_total = 0.0
	for row in closing_doc.get("taxes") or []:
		amount = flt(row.get("amount"), 2)
		if _classify_charge(row.get("account_head")) == "service":
			service_charge = flt(service_charge + amount, 2)
		else:
			tax_total = flt(tax_total + amount, 2)

	total_sales = flt(closing_doc.get("grand_total"), 2)
	total_order = len(transactions)

	return {
		"total_sales": total_sales,
		"total_order": total_order,
		"average_per_order": flt(total_sales / total_order, 2) if total_order else 0.0,
		"cash": {
			"opening_balance": opening_balance,
			"cash_payment": cash_payment,
			"total_expense": 0.0,
			"cash_in_hand": cash_in_hand,
		},
		"payment_methods": payment_methods,
		"total_cash": total_cash,
		"total_non_cash": total_non_cash,
		"methods_grand_total": flt(total_cash + total_non_cash, 2),
		"service_charge": service_charge,
		"tax_total": tax_total,
		"product_discount": _fetch_discount_for_targets(parent_targets),
		"payment_discount": 0.0,
		"refund_total": flt(
			sum(abs(flt(row.get("grand_total"))) for row in transactions if flt(row.get("grand_total")) < 0),
			2,
		),
		"categories": _collect_categories(_fetch_grouped_items_for_targets(parent_targets)),
	}
