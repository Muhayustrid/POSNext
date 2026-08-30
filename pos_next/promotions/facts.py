"""Promotion Selection Fact projection writer and reporting queries (design section 11).

The fact table is derived, rebuildable reporting state and never transaction
authority (invariant I14): no transaction path may read it — receipts, returns,
guards, and eligibility use only the selection snapshot and the invoice rows.
If facts and submitted transactions ever disagree, the transactions win and the
facts are rebuilt.

Measured rulings encoded here (Task 6 handoff record, 2026-08-20):

- Instance presence is derived from the document's promotion item rows, never
  from the ``POS Promotion Selection`` table. ``make_sales_return`` copies
  every selection row wholesale (the Custom Field carries ``no_copy = 0``), so
  a return that repays one instance out of two still carries both copied
  selections. Deriving return-side figures from the selection table — design
  section 11's literal join — over-counts returned units and refunds
  (measured: 2 counted versus 1 repaid, 43,000 versus 20,000). The document's
  own rows are what the return actually repays, so they are the presence
  authority here. The disagreement with the design's literal SQL is reported
  to the operator, not silently implemented.
- Per-line descriptors come from the selection snapshot because invoice rows
  cannot express the option/fixed split: one Item may legitimately appear as a
  fixed component AND as a chosen option of the same instance, producing two
  invoice lines with identical (instance, item_code) and no kind marker.
- ``qty`` and ``promotion_total`` are negated on returns: magnitude from the
  snapshot (exact match is guaranteed by the return guard for returns and by
  the materialization path for sales), sign from the document.
  ``price_adjustment`` is a unit-price attribute and is never negated.
- ``item_name`` is looked up live from the Item master for every line, so
  fixed components and chosen options follow one rule; the snapshot carries
  ``item_name`` only for chosen options.
- ``warehouse`` comes from the instance's component invoice rows, which the
  engine keeps uniform (I13).
- Identity is ``autoname: "hash"`` on purpose: a deterministic composite key
  cannot survive the fixed-and-option overlap above. Tests therefore compare
  facts by canonical semantic equality, never by name.
"""

import frappe
from frappe.utils import flt

from pos_next.promotions.engine import (
	COMPONENT_ROLE,
	INSTANCE_FIELD,
	ROLE_FIELD,
	SELECTIONS_FIELD,
)

FACT_DOCTYPE = "Promotion Selection Fact"

OPTION_KIND = "Option"
FIXED_COMPONENT_KIND = "Fixed Component"


def on_submit(doc, method=None):
	"""Doc-event: (re)write this invoice's facts. Runs for sales and returns."""
	_project(doc)


def on_cancel(doc, method=None):
	"""Doc-event: a cancelled transaction must never remain represented as active."""
	frappe.db.delete(FACT_DOCTYPE, {"pos_invoice": doc.name})


def rebuild():
	"""Rebuild the whole projection from submitted Sales Invoices.

	Drops every fact row, then re-projects each submitted (``docstatus = 1``)
	Sales Invoice that carries promotion selections. Cancelled sources contribute
	nothing: their child rows stay in the database with ``docstatus = 2`` after
	cancellation, so source selection filters on the selection table's
	``docstatus = 1``.

	Operator implications: full-table scope with no arguments, cost proportional
	to the number of submitted promotion-bearing invoices, and it rewrites rows
	for every such invoice on the site, not just recent ones. Run it via
	``bench --site <site> execute pos_next.promotions.facts.rebuild``
	after a projection logic change or a discovered drift; submitted
	transactions always win over the stored facts (I14).
	"""
	frappe.db.delete(FACT_DOCTYPE)

	parents = sorted(
		set(
			frappe.get_all(
				"POS Promotion Selection",
				filters={"docstatus": 1, "parenttype": "Sales Invoice"},
				pluck="parent",
			)
		)
	)
	for name in parents:
		_project(frappe.get_doc("Sales Invoice", name))


def _project(doc):
	"""Idempotent write: drop this invoice's facts, then derive them again.

	The delete-first is defense-in-depth, deliberately untestable: ``on_submit``
	fires exactly once per document lifecycle (no path submits the same name
	twice) and ``rebuild()`` clears the whole table before re-projecting, so no
	reachable sequence writes facts for one invoice twice. A mutation removing
	it survives the suite for exactly that reason.
	"""
	frappe.db.delete(FACT_DOCTYPE, {"pos_invoice": doc.name})
	for values in _fact_rows(doc):
		# No role holds create on the fact table (design section 18); the writer
		# is doc-event/system code, which is exactly the ignore_permissions case.
		frappe.get_doc({"doctype": FACT_DOCTYPE, **values}).insert(ignore_permissions=True)


def _fact_rows(doc):
	"""Derive one fact dict per component line of each instance present on the document."""
	sign = -1.0 if doc.get("is_return") else 1.0
	selections = {selection.instance_id: selection for selection in doc.get(SELECTIONS_FIELD) or []}

	# Presence and warehouse come from the document's own component rows. The
	# copied selection table is deliberately not consulted for presence (module
	# docstring): a mapped return may carry selections for instances it does not
	# repay.
	warehouses = {}
	for row in doc.get("items") or []:
		if row.get(ROLE_FIELD) == COMPONENT_ROLE and row.get(INSTANCE_FIELD):
			warehouses.setdefault(row.get(INSTANCE_FIELD), row.get("warehouse"))

	rows = []
	for instance_id in sorted(warehouses):
		selection = selections.get(instance_id)
		if selection is None:
			# Unreachable behind the engine's row-integrity guard (a component
			# row without a backing selection cannot submit); skip rather than
			# crash the reporting write.
			continue
		snapshot = frappe.parse_json(selection.snapshot) or {}
		common = {
			"pos_invoice": doc.name,
			"instance_id": instance_id,
			"promotion": selection.promotion,
			"posting_date": doc.get("posting_date"),
			"company": doc.get("company"),
			"warehouse": warehouses[instance_id],
			"pos_profile": doc.get("pos_profile"),
			"is_return": 1 if doc.get("is_return") else 0,
			"return_against": doc.get("return_against"),
			"promotion_total": flt(selection.total_amount) * sign,
		}
		for component in snapshot.get("fixed_components") or []:
			rows.append(
				{
					**common,
					"kind": FIXED_COMPONENT_KIND,
					"group_key": None,
					"group_label": None,
					"option": None,
					"item_code": component.get("item_code"),
					"item_name": _item_name(component.get("item_code")),
					"qty": flt(component.get("qty")) * sign,
					"price_adjustment": 0.0,
				}
			)
		for pick in snapshot.get("chosen_options") or []:
			rows.append(
				{
					**common,
					"kind": OPTION_KIND,
					"group_key": pick.get("group_key"),
					"group_label": pick.get("group_label"),
					"option": pick.get("option_row"),
					"item_code": pick.get("item_code"),
					"item_name": _item_name(pick.get("item_code")),
					"qty": flt(pick.get("qty")) * sign,
					"price_adjustment": flt(pick.get("price_adjustment")),
				}
			)
	return rows


def _item_name(item_code):
	"""Live master lookup keeps one uniform rule for fixed components and options."""
	if not item_code:
		return None
	return frappe.get_cached_value("Item", item_code, "item_name")


# --- reporting queries (one helper per design section 11 bullet) ---------------
#
# Return-side rows are stored negated (the sign of their source document, per
# the module docstring). Every helper below reports the return side as a
# positive magnitude and derives net as gross - returned, matching section 11's
# "net = gross - returned" wording.


def promotion_units():
	"""Gross / returned / net promotion units per promotion (section 11 bullets 1-3).

	Units are distinct (pos_invoice, instance_id) pairs split by ``is_return`` —
	never the return's copied selection rows (module docstring).
	"""
	rows = frappe.db.sql(
		f"""
		SELECT promotion,
		       COALESCE(SUM(CASE WHEN is_return = 0 THEN 1 ELSE 0 END), 0) AS gross,
		       COALESCE(SUM(CASE WHEN is_return = 1 THEN 1 ELSE 0 END), 0) AS returned
		FROM (SELECT DISTINCT pos_invoice, instance_id, promotion, is_return
		      FROM `tab{FACT_DOCTYPE}`) AS instances
		GROUP BY promotion
		""",
		as_dict=True,
	)
	return {
		row.promotion: {
			"gross": int(row.gross),
			"returned": int(row.returned),
			"net": int(row.gross) - int(row.returned),
		}
		for row in rows
	}


def promotion_revenue():
	"""Gross / refunded / net promotion revenue per promotion (section 11 bullet 4).

	``promotion_total`` is denormalized onto every component line of its
	instance, so the sum runs over distinct (pos_invoice, instance_id) pairs
	with the instance total appearing exactly once.
	"""
	rows = frappe.db.sql(
		f"""
		SELECT promotion,
		       COALESCE(SUM(CASE WHEN is_return = 0 THEN promotion_total ELSE 0 END), 0) AS gross,
		       COALESCE(SUM(CASE WHEN is_return = 1 THEN promotion_total ELSE 0 END), 0) AS returned
		FROM (SELECT DISTINCT pos_invoice, instance_id, promotion, is_return, promotion_total
		      FROM `tab{FACT_DOCTYPE}`) AS instances
		GROUP BY promotion
		""",
		as_dict=True,
	)
	return {
		row.promotion: {
			"gross": flt(row.gross),
			"returned": -flt(row.returned),
			"net": flt(row.gross) + flt(row.returned),
		}
		for row in rows
	}


def item_quantities():
	"""Gross / returned / net quantities per item, split by kind (section 11 bullet 5).

	The net column is the fact table's signed ``SUM(qty) GROUP BY item_code``,
	which also serves the physical component consumption query (section 11).
	"""
	rows = frappe.db.sql(
		f"""
		SELECT item_code, kind,
		       COALESCE(SUM(CASE WHEN is_return = 0 THEN qty ELSE 0 END), 0) AS gross,
		       COALESCE(SUM(CASE WHEN is_return = 1 THEN qty ELSE 0 END), 0) AS returned
		FROM `tab{FACT_DOCTYPE}`
		GROUP BY item_code, kind
		""",
		as_dict=True,
	)
	out = {}
	for row in rows:
		out.setdefault(row.item_code, {})[row.kind] = {
			"gross": flt(row.gross),
			"returned": -flt(row.returned),
			"net": flt(row.gross) + flt(row.returned),
		}
	return out


def outlet_totals():
	"""Promotion units and revenue per outlet (section 11 bullet 6).

	Grouped by the fact row's company, POS Profile, and warehouse — the outlet
	context of the source transaction. Units count distinct instances; revenue
	sums the per-instance total once per instance.
	"""
	rows = frappe.db.sql(
		f"""
		SELECT company, pos_profile, warehouse,
		       COALESCE(SUM(CASE WHEN is_return = 0 THEN 1 ELSE 0 END), 0) AS gross_units,
		       COALESCE(SUM(CASE WHEN is_return = 1 THEN 1 ELSE 0 END), 0) AS returned_units,
		       COALESCE(SUM(CASE WHEN is_return = 0 THEN promotion_total ELSE 0 END), 0) AS gross_revenue,
		       COALESCE(SUM(CASE WHEN is_return = 1 THEN promotion_total ELSE 0 END), 0) AS returned_revenue
		FROM (SELECT DISTINCT pos_invoice, instance_id, company, pos_profile, warehouse,
		              is_return, promotion_total
		      FROM `tab{FACT_DOCTYPE}`) AS instances
		GROUP BY company, pos_profile, warehouse
		ORDER BY company, pos_profile, warehouse
		""",
		as_dict=True,
	)
	return [
		{
			"company": row.company,
			"pos_profile": row.pos_profile,
			"warehouse": row.warehouse,
			"gross_units": int(row.gross_units),
			"returned_units": int(row.returned_units),
			"net_units": int(row.gross_units) - int(row.returned_units),
			"gross_revenue": flt(row.gross_revenue),
			"returned_revenue": -flt(row.returned_revenue),
			"net_revenue": flt(row.gross_revenue) + flt(row.returned_revenue),
		}
		for row in rows
	]


def option_frequency():
	"""Selected option frequency per item for chosen options (section 11 bullet 7).

	Section 11's literal query is ``SUM(qty) WHERE kind = 'Option' GROUP BY
	item_code``; that signed sum is the ``net`` column here, with gross and
	returned magnitudes alongside for the same split as every other helper.
	"""
	rows = frappe.db.sql(
		f"""
		SELECT item_code,
		       COALESCE(SUM(CASE WHEN is_return = 0 THEN qty ELSE 0 END), 0) AS gross,
		       COALESCE(SUM(CASE WHEN is_return = 1 THEN qty ELSE 0 END), 0) AS returned
		FROM `tab{FACT_DOCTYPE}`
		WHERE kind = %s
		GROUP BY item_code
		""",
		OPTION_KIND,
		as_dict=True,
	)
	return {
		row.item_code: {
			"gross": flt(row.gross),
			"returned": -flt(row.returned),
			"net": flt(row.gross) + flt(row.returned),
		}
		for row in rows
	}


def standalone_split():
	"""Standalone versus in-promotion consumption per item (section 11, consumption split).

	Reads ``tabSales Invoice Item`` directly — not the fact table — because the
	split is a property of the invoice rows themselves: rows with no promotion
	instance are standalone; rows with role ``Promotion Component`` are consumed
	inside a promotion. Quantities keep each row's sign, so returns reduce both
	buckets. Promotion parent rows fall in neither bucket: Model C keeps them
	non-stock revenue carriers, so items whose sums are zero in both buckets
	are omitted.
	"""
	rows = frappe.db.sql(
		f"""
		SELECT item_code,
		       COALESCE(SUM(CASE WHEN ({INSTANCE_FIELD} IS NULL OR {INSTANCE_FIELD} = '')
		                         THEN qty ELSE 0 END), 0) AS standalone_qty,
		       COALESCE(SUM(CASE WHEN {ROLE_FIELD} = %s THEN qty ELSE 0 END), 0) AS in_promotion_qty
		FROM `tabSales Invoice Item`
		WHERE docstatus = 1
		GROUP BY item_code
		""",
		COMPONENT_ROLE,
		as_dict=True,
	)
	return {
		row.item_code: {
			"standalone": flt(row.standalone_qty),
			"in_promotion": flt(row.in_promotion_qty),
		}
		for row in rows
		if flt(row.standalone_qty) or flt(row.in_promotion_qty)
	}
