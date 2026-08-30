"""Promotion DocType controller (Task 2: master model and validations)."""

import uuid

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class Promotion(Document):
	# All master-save validations live here, in the parent controller: measured
	# framework behaviour (frappe/model/document.py) is that child rows persist
	# via db_insert/db_update without running child DocType hooks, so child
	# controllers cannot carry validation.

	def validate(self):
		self._ensure_group_keys()
		self._validate_header_fields()
		self._validate_structure()
		self._validate_physical_items()
		self._validate_adjusted_totals()
		self._validate_parent_item()
		self._validate_outlets()
		self._warn_on_tax_template_mismatch()

	def on_trash(self):
		# D15: a Promotion referenced by any submitted POS Promotion Selection is
		# part of transaction history and cannot be deleted; retirement is
		# enabled = 0. Child rows mirror the parent Sales Invoice docstatus
		# (Document.set_docstatus), so docstatus = 1 identifies submitted
		# selections directly. This runs before the generic link-integrity check
		# (frappe/model/delete_doc.py), so the named error surfaces first.
		if frappe.db.exists("POS Promotion Selection", {"promotion": self.name, "docstatus": 1}):
			frappe.throw(
				_(
					"Promotion {0} cannot be deleted because submitted POS Promotion Selection"
					" rows reference it. Disable the promotion instead."
				).format(self.name)
			)

	# --- group identity (D3) ------------------------------------------------

	def _ensure_group_keys(self):
		# Generated exactly once per row, only when missing; never regenerated,
		# so re-saves and label edits leave stored keys — and the selections and
		# snapshots keyed by them — untouched.
		for group in self.choice_groups or []:
			if not group.group_key:
				group.group_key = f"grp_{uuid.uuid4().hex[:8]}"

	# --- header fields --------------------------------------------------------

	def _validate_header_fields(self):
		if flt(self.base_price) <= 0:
			frappe.throw(_("Base price must be greater than zero"))
		# D19: 0 = unlimited is persisted master semantics; the validation reads
		# no transaction state.
		if flt(self.max_instances_per_invoice) < 0:
			frappe.throw(_("Max instances per invoice must not be negative"))
		if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
			frappe.throw(_("Valid From must not be after Valid To"))

	# --- structure --------------------------------------------------------------

	def _validate_structure(self):
		if not self.components and not self.choice_groups:
			frappe.throw(_("At least one component or choice group is required"))

		group_keys = set()
		for group in self.choice_groups or []:
			if group.group_key in group_keys:
				frappe.throw(_("Row {0}: Duplicate choice group key {1}").format(group.idx, group.group_key))
			group_keys.add(group.group_key)
			if (group.pick_count or 0) < 1:
				frappe.throw(_("Row {0}: Pick count must be at least one").format(group.idx))

		options_by_group: dict[str, list] = {}
		for option in self.options or []:
			if option.choice_group_key not in group_keys:
				frappe.throw(
					_(
						"Row {0}: Option references choice group key {1} which does not exist in"
						" this Promotion"
					).format(option.idx, option.choice_group_key)
				)
			options_by_group.setdefault(option.choice_group_key, []).append(option)

		for group in self.choice_groups or []:
			if len(options_by_group.get(group.group_key, [])) < 2:
				frappe.throw(
					_("Row {0}: Choice group {1} must have at least two options").format(
						group.idx, group.group_key
					)
				)

	# --- physical items (D13 / I12) ----------------------------------------------

	def _validate_physical_items(self):
		for row in self.components or []:
			self._assert_physical_item("Component", row.idx, row.item_code)
			if flt(row.qty) <= 0:
				frappe.throw(_("Row {0}: Component quantity must be greater than zero").format(row.idx))
			if flt(row.qty) != int(flt(row.qty)):
				frappe.throw(_("Row {0}: Component quantity must be a whole number").format(row.idx))
		for row in self.options or []:
			self._assert_physical_item("Option", row.idx, row.item_code)

	@staticmethod
	def _assert_physical_item(row_kind, idx, item_code):
		flags = frappe.db.get_value(
			"Item",
			item_code,
			["is_stock_item", "is_sales_item", "has_batch_no", "has_serial_no"],
			as_dict=True,
		)
		if not flags:
			frappe.throw(_("Row {0}: Item {1} does not exist").format(idx, item_code))
		if not flags.is_stock_item:
			frappe.throw(_("Row {0}: {1} item {2} must be a stock item").format(idx, row_kind, item_code))
		if not flags.is_sales_item:
			frappe.throw(_("Row {0}: {1} item {2} must be a sales item").format(idx, row_kind, item_code))
		if flags.has_batch_no:
			frappe.throw(_("Row {0}: {1} item {2} must not track batches").format(idx, row_kind, item_code))
		if flags.has_serial_no:
			frappe.throw(
				_("Row {0}: {1} item {2} must not track serial numbers").format(idx, row_kind, item_code)
			)

	# --- pricing -------------------------------------------------------------------

	def _validate_adjusted_totals(self):
		# Design section 6: instance total = base_price + sum of adjustments.
		# Master-side bound: each option, picked once, must keep the adjusted
		# total at or above zero; quantity-driven bounds belong to the quote
		# domain (Task 3).
		base_price = flt(self.base_price)
		for option in self.options or []:
			if base_price + flt(option.price_adjustment) < 0:
				frappe.throw(
					_("Row {0}: Option {1} adjusted total must not be negative").format(
						option.idx, option.item_code
					)
				)

	# --- parent item (D12 / I11) ------------------------------------------------------

	def _validate_parent_item(self):
		flags = frappe.db.get_value(
			"Item", self.parent_item, ["is_stock_item", "is_sales_item", "is_fixed_asset"], as_dict=True
		)
		if not flags:
			frappe.throw(_("Parent item {0} does not exist").format(self.parent_item))
		if flags.is_stock_item:
			frappe.throw(_("Parent item {0} must not be a stock item").format(self.parent_item))
		if not flags.is_sales_item:
			frappe.throw(_("Parent item {0} must be a sales item").format(self.parent_item))
		if flags.is_fixed_asset:
			frappe.throw(_("Parent item {0} must not be a fixed asset").format(self.parent_item))
		# D12: the promotion engine is the only writer of the parent row's rate.
		# Any selling Item Price row fails regardless of its validity window —
		# the conservative direction, matching the Price Group unmanaged-row
		# precheck: a future-dated row would otherwise become active under a
		# live promotion.
		if frappe.db.exists("Item Price", {"item_code": self.parent_item, "selling": 1}):
			frappe.throw(_("Parent item {0} must not have any selling Item Price").format(self.parent_item))
		# I11: at most one enabled Promotion per parent item.
		if self.enabled:
			other = frappe.db.exists(
				"Promotion",
				{"parent_item": self.parent_item, "enabled": 1, "name": ["!=", self.name]},
			)
			if other:
				frappe.throw(
					_("Parent item {0} is already used by enabled Promotion {1}").format(
						self.parent_item, other
					)
				)

	# --- outlets (D4 / D5) ---------------------------------------------------------------

	def _validate_outlets(self):
		# F11: Company is a nested-set tree; the root-company fence is a
		# save-time organization check only, never transaction eligibility.
		root_bounds = frappe.db.get_value("Company", self.root_company, ["lft", "rgt"], as_dict=True)
		seen = set()
		for row in self.outlets or []:
			key = (row.company, row.warehouse)
			if key in seen:
				frappe.throw(
					_("Row {0}: Duplicate outlet {1} / {2}").format(row.idx, row.company, row.warehouse)
				)
			seen.add(key)

			wh_company = frappe.db.get_value("Warehouse", row.warehouse, "company")
			if wh_company != row.company:
				frappe.throw(
					_("Row {0}: Warehouse {1} belongs to company {2}, not {3}").format(
						row.idx, row.warehouse, wh_company, row.company
					)
				)

			company = frappe.db.get_value(
				"Company", row.company, ["lft", "rgt", "default_currency"], as_dict=True
			)
			if (
				not root_bounds
				or not company
				or company.lft < root_bounds.lft
				or company.rgt > root_bounds.rgt
			):
				frappe.throw(
					_("Row {0}: Company {1} is not within root company {2}").format(
						row.idx, row.company, self.root_company
					)
				)

			if company.default_currency != self.currency:
				frappe.throw(
					_("Row {0}: Company {1} uses currency {2}, promotion currency is {3}").format(
						row.idx, row.company, company.default_currency, self.currency
					)
				)

	# --- tax advisory (frozen decision 1) -------------------------------------------------

	def _warn_on_tax_template_mismatch(self):
		# Model C taxes the parent row only, so an option item whose Item Tax
		# Template differs from the parent's is an operator advisory, never a
		# block. Comparison is the set of Item Tax row templates; validity
		# windows on those rows are deliberately ignored.
		parent_templates = self._item_tax_templates(self.parent_item)
		for option in self.options or []:
			if self._item_tax_templates(option.item_code) != parent_templates:
				frappe.msgprint(
					_(
						"Option item {0} uses a different Item Tax Template than parent item {1}."
						" Taxes are calculated from the parent item only."
					).format(option.item_code, self.parent_item)
				)

	@staticmethod
	def _item_tax_templates(item_code):
		return frozenset(
			frappe.get_all(
				"Item Tax", filters={"parent": item_code, "parenttype": "Item"}, pluck="item_tax_template"
			)
		)
