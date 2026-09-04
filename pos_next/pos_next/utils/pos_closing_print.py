from __future__ import annotations

from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal

import frappe
from frappe.query_builder import DocType
from frappe.utils import flt
from pypika import Order
from pypika.functions import Sum

_TAX_KEYWORDS = ("PPN", "TAX", "VAT", "PAJAK")
_SERVICE_KEYWORDS = ("SERVICE", "JASA", "CHARGE")


def format_rupiah(value) -> str:
	amount = flt(value)
	whole = int(Decimal(str(amount)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
	return f"{'-' if whole < 0 else ''}Rp{abs(whole):,}".replace(",", ".")


def _as_closing_doc(doc):
	if isinstance(doc, str):
		return frappe.get_doc("POS Closing Shift", doc)
	return doc


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


def _fetch_items_for_targets(parent_targets: set[tuple[str, str]]) -> list[dict]:
	if not parent_targets:
		return []

	sales_invoice_item = DocType("Sales Invoice Item")
	amount_sum = Sum(sales_invoice_item.amount)
	qty_sum = Sum(sales_invoice_item.qty)

	condition = None
	for parent, parenttype in sorted(parent_targets):
		current = (sales_invoice_item.parent == parent) & (sales_invoice_item.parenttype == parenttype)
		condition = current if condition is None else (condition | current)

	query = (
		frappe.qb.from_(sales_invoice_item)
		.select(
			sales_invoice_item.item_code,
			sales_invoice_item.item_name,
			qty_sum.as_("qty"),
			amount_sum.as_("amount"),
		)
		.where(condition)
		.groupby(sales_invoice_item.item_code, sales_invoice_item.item_name)
		.orderby(amount_sum, order=Order.desc)
	)

	return query.run(as_dict=True)


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

	sales_invoice_item = DocType("Sales Invoice Item")
	discount_sum = Sum((sales_invoice_item.price_list_rate - sales_invoice_item.rate) * sales_invoice_item.qty)
	row = (
		frappe.qb.from_(sales_invoice_item)
		.select(discount_sum.as_("discount"))
		.where(_build_condition(sales_invoice_item, parent_targets))
		.run(as_dict=True)
	)
	item_discount = flt(row[0].get("discount")) if row else 0.0

	invoices = [parent for parent, parenttype in parent_targets if parenttype == "Sales Invoice"]
	invoice_discount = 0.0
	if invoices:
		rows = frappe.get_all(
			"Sales Invoice",
			filters={"name": ["in", invoices]},
			fields=["discount_amount"],
			limit_page_length=0,
		)
		invoice_discount = sum(flt(row.get("discount_amount")) for row in rows)

	return flt(item_discount + invoice_discount, 2)


def _fetch_grouped_items_for_targets(parent_targets: set[tuple[str, str]]) -> list[dict]:
	if not parent_targets:
		return []

	sales_invoice_item = DocType("Sales Invoice Item")
	amount_sum = Sum(sales_invoice_item.amount)
	qty_sum = Sum(sales_invoice_item.qty)

	return (
		frappe.qb.from_(sales_invoice_item)
		.select(
			sales_invoice_item.item_group,
			sales_invoice_item.item_code,
			sales_invoice_item.item_name,
			qty_sum.as_("qty"),
			amount_sum.as_("amount"),
		)
		.where(_build_condition(sales_invoice_item, parent_targets))
		.groupby(sales_invoice_item.item_group, sales_invoice_item.item_code, sales_invoice_item.item_name)
		.run(as_dict=True)
	)


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
