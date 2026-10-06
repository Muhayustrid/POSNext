# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, getdate

GROUP_KEY_PATTERN = re.compile(r"^[a-z0-9_]+$")


def generate_group_key(existing):
	"""Random id for a choice group — the key is plumbing, never user input."""
	while True:
		key = frappe.generate_hash()[:8]
		if key not in existing:
			return key


class POSPackage(Document):
	def validate(self):
		self.assign_group_keys()
		self.validate_dates()
		self.validate_price()
		self.validate_content()
		self.validate_groups()
		self.validate_options()
		self.validate_parent_item()
		self.validate_component_items()
		self.validate_allocation_carrier()
		self.validate_outlets()

	def assign_group_keys(self):
		"""Fill blank group keys with a random unique id and reject
		malformed/duplicate typed ones."""
		seen = set()
		for group in self.groups or []:
			if not group.group_key:
				group.group_key = generate_group_key(seen)

			group.group_key = group.group_key.strip().lower()

			if not GROUP_KEY_PATTERN.match(group.group_key):
				frappe.throw(
					_(
						"Row {0}: Group Key {1} may only contain lowercase letters, digits and underscores."
					).format(group.idx, frappe.bold(group.group_key))
				)

			if group.group_key in seen:
				frappe.throw(
					_("Row {0}: Group Key {1} is used more than once.").format(
						group.idx, frappe.bold(group.group_key)
					)
				)
			seen.add(group.group_key)

	def validate_dates(self):
		if cint(getattr(self, "is_lifetime", 0)):
			self.valid_from = None
			self.valid_upto = None
			return
		if self.valid_from and self.valid_upto and getdate(self.valid_from) > getdate(self.valid_upto):
			frappe.throw(_("Valid Upto cannot be earlier than Valid From."))

	def validate_price(self):
		if flt(self.base_price) < 0:
			frappe.throw(_("Base Price cannot be negative."))

	def validate_content(self):
		if not (self.items or self.groups):
			frappe.throw(_("A package needs at least one included item or one choice group."))

	def validate_groups(self):
		for group in self.groups or []:
			min_qty = cint(group.min_qty)
			max_qty = cint(group.max_qty)

			if max_qty < 1:
				frappe.throw(
					_("Row {0}: Max Qty must be at least 1 for group {1}.").format(
						group.idx, frappe.bold(group.label)
					)
				)

			if min_qty > max_qty:
				frappe.throw(
					_("Row {0}: Min Qty cannot exceed Max Qty for group {1}.").format(
						group.idx, frappe.bold(group.label)
					)
				)

			options = [o for o in (self.options or []) if o.group_key == group.group_key]
			if not options:
				frappe.throw(
					_("Group {0} has no options. Add at least one option or remove the group.").format(
						frappe.bold(group.label)
					)
				)

			# The group is unsatisfiable when every option's own cap sums below min_qty.
			capacity = sum(cint(o.max_qty) or max_qty for o in options)
			if capacity < min_qty:
				frappe.throw(
					_("Group {0} requires {1} unit(s) but its options allow at most {2}.").format(
						frappe.bold(group.label), min_qty, capacity
					)
				)

	def validate_options(self):
		group_keys = {g.group_key for g in self.groups or []}
		for option in self.options or []:
			option.group_key = (option.group_key or "").strip().lower()

			if option.group_key not in group_keys:
				frappe.throw(
					_("Row {0}: Group Key {1} does not match any choice group.").format(
						option.idx, frappe.bold(option.group_key)
					)
				)

			if flt(option.qty_per_unit) <= 0:
				frappe.throw(_("Row {0}: Qty Per Unit must be greater than zero.").format(option.idx))

	def validate_parent_item(self):
		item = frappe.db.get_value(
			"Item",
			self.parent_item,
			[
				"is_stock_item",
				"is_sales_item",
				"is_fixed_asset",
				"has_batch_no",
				"has_serial_no",
				"disabled",
				"item_name",
			],
			as_dict=True,
		)
		if not item:
			frappe.throw(_("Package Item {0} does not exist.").format(frappe.bold(self.parent_item)))

		if item.disabled:
			frappe.throw(_("Package Item {0} is disabled.").format(frappe.bold(self.parent_item)))

		if not item.is_sales_item:
			frappe.throw(_("Package Item {0} must be a sales item.").format(frappe.bold(self.parent_item)))

		if item.is_stock_item:
			frappe.throw(
				_(
					"Package Item {0} must be a non-stock item — stock moves on the included items, not on the package line."
				).format(frappe.bold(self.parent_item))
			)

		if item.is_fixed_asset:
			frappe.throw(_("Package Item {0} cannot be a fixed asset.").format(frappe.bold(self.parent_item)))

		if item.has_batch_no or item.has_serial_no:
			frappe.throw(
				_("Package Item {0} cannot be batch or serial tracked.").format(frappe.bold(self.parent_item))
			)

		if frappe.db.exists("Product Bundle", {"new_item_code": self.parent_item, "disabled": 0}):
			frappe.throw(
				_(
					"Item {0} is already used by a Product Bundle. Use a dedicated item for the package."
				).format(frappe.bold(self.parent_item))
			)

		# Two active catalog cards with the same item_name read as one product
		# to a cashier (the grid shows names, not codes), so a same-named leftover
		# item resurfaces next to the package parent as a plain, bundle-less card.
		if item.item_name:
			conflict = frappe.db.get_value(
				"Item",
				{
					"item_name": item.item_name,
					"name": ["!=", self.parent_item],
					"disabled": 0,
					"is_sales_item": 1,
				},
				"name",
			)
			if conflict:
				frappe.throw(
					_(
						"Another enabled sales item {0} already uses the same Item Name {1} as the Package Item {2}. Disable or rename it — two active items with the same name show up as duplicate cards in the POS."
					).format(
						frappe.bold(conflict), frappe.bold(item.item_name), frappe.bold(self.parent_item)
					)
				)

	def validate_component_items(self):
		"""Included items and options must be sellable items distinct from the package item."""
		rows = [("items", row) for row in (self.items or [])]
		rows += [("options", row) for row in (self.options or [])]

		for table, row in rows:
			if row.item_code == self.parent_item:
				frappe.throw(
					_("Row {0}: {1} cannot contain the Package Item itself.").format(
						row.idx, _("Included Items") if table == "items" else _("Choice Options")
					)
				)

			item = frappe.db.get_value("Item", row.item_code, ["is_sales_item", "disabled"], as_dict=True)
			if not item:
				frappe.throw(
					_("Row {0}: Item {1} does not exist.").format(row.idx, frappe.bold(row.item_code))
				)
			if item.disabled:
				frappe.throw(_("Row {0}: Item {1} is disabled.").format(row.idx, frappe.bold(row.item_code)))
			if not item.is_sales_item:
				frappe.throw(
					_("Row {0}: Item {1} is not a sales item.").format(row.idx, frappe.bold(row.item_code))
				)

			if table == "items" and flt(row.qty) <= 0:
				frappe.throw(_("Row {0}: Qty must be greater than zero.").format(row.idx))

	def validate_allocation_carrier(self):
		"""Allocation mode needs a component line of quantity 1.

		``allocate_package_rates`` carries the rounding remainder on the qty-1
		line with the largest weight above 0, so the component rates always sum
		to the package price exactly. A qty-1 line with no price-list rate
		cannot carry (its share is 0, so the leftover would book negative) —
		this static check cannot see prices, so it requires at least a qty-1
		line and the checkout guard still covers the rest. Without one the
		split can leave a remainder that does not divide any line quantity,
		and the invoice would fail closed at checkout with a message for the
		cashier that belongs on the package definition instead. An
		included item is fixed at qty 1 when it says so; an option counts as a
		possible carrier only when it can be picked exactly once (qty_per_unit
		1) — the checkout guard still covers picks that skip it.
		"""
		from pos_next.api.packages import _package_allocation_enabled

		if not _package_allocation_enabled():
			return

		if any(flt(row.qty) == 1 for row in self.items or []):
			return
		if any(flt(option.qty_per_unit) == 1 for option in self.options or []):
			return

		frappe.throw(
			_(
				"Package allocation splits the package price across the component lines and needs a line of quantity 1 to carry the rounding remainder. Set an included item's Qty to 1, or give a choice option Qty Per Unit 1."
			)
		)

	def validate_outlets(self):
		seen = set()
		for outlet in self.outlets or []:
			key = (outlet.company, outlet.warehouse)
			if key in seen:
				frappe.throw(
					_("Row {0}: Duplicate outlet {1} / {2}").format(
						outlet.idx, frappe.bold(outlet.company), frappe.bold(outlet.warehouse)
					)
				)
			seen.add(key)

			wh_company = frappe.db.get_value("Warehouse", outlet.warehouse, "company")
			if wh_company and wh_company != outlet.company:
				frappe.throw(
					_("Row {0}: Warehouse {1} belongs to company {2}, not {3}.").format(
						outlet.idx,
						frappe.bold(outlet.warehouse),
						frappe.bold(wh_company),
						frappe.bold(outlet.company),
					)
				)

			matching = frappe.get_all(
				"POS Profile",
				filters={"company": outlet.company, "warehouse": outlet.warehouse},
				pluck="name",
			)
			if not matching:
				outlet.status = "No POS Profile matches this warehouse"
				outlet.pos_profile = None
			else:
				outlet.status = "Available on all profiles for this warehouse"
				outlet.pos_profile = ", ".join(sorted(matching))
