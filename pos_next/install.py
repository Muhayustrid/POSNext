"""
Installation and Migration hooks for POS Next

Responsibilities are split explicitly:

- Frappe fixtures (hooks.py) carry ONLY `Role` and `Custom DocPerm`. They do
  NOT apply Custom Fields or Print Formats.
- Custom Fields are applied by `setup_custom_fields()`, called from
  `after_install` and `after_migrate`, which creates each field from the
  Python-side `CUSTOM_FIELDS` list below. `pos_next/pos_next/custom/*.json`
  is a review mirror of Desk state, not an apply mechanism. The upsert is
  insert-if-absent and never overwrites an existing field, so relabelling a
  field in the Desk survives subsequent migrations.
- The default Print Format assignment on POS Profiles is applied by
  `setup_default_print_format()`.

This module also reclaims the `POS Settings` DocType from ERPNext after each
migrate (`reclaim_pos_settings_doctype`).
"""

import logging

import frappe
from frappe.utils import cint

# Configure logger
logger = logging.getLogger(__name__)


# Server-managed structural attributes that must be reconciled from the spec
# even when the Custom Field row already exists. These are not operator-facing
# text (unlike `label`/`description`, whose Desk edits must survive via the
# insert-if-absent rule), so a field that shipped with a stale value self-heals
# on the next migrate. Only these attrs are ever written back.
_RECONCILE_ATTRS = ("read_only", "unique")


# Declarative source of truth for Custom Fields this app owns. Created by
# `setup_custom_fields()` from `after_install`/`after_migrate`, insert-if-absent:
# an admin's Desk edits (e.g. a relabelled field) are never overwritten.
# `pos_next/pos_next/custom/*.json` mirrors the resulting Desk state for review
# only; nothing reads it.
CUSTOM_FIELDS = [
	{
		"dt": "Sales Invoice",
		"fieldname": "buyer_name",
		"label": "Buyer Name",
		"fieldtype": "Data",
		"length": 60,
		"insert_after": "customer",
		"description": (
			"Queue-facing free-text buyer name for walk-in sales. Only valid when the "
			"customer is the POS Profile's default walk-in customer."
		),
		"no_copy": 1,
		"search_index": 1,
	},
	{
		"dt": "Sales Invoice",
		"fieldname": "queue_number",
		"label": "Queue Number",
		"fieldtype": "Int",
		"insert_after": "buyer_name",
		"description": "Server-allocated per-shift queue number, published for reporting and display.",
		"read_only": 1,
		"no_copy": 1,
		"print_hide": 1,
	},
	{
		"dt": "POS Opening Shift",
		"fieldname": "current_queue_number",
		"label": "Current Queue Number",
		"fieldtype": "Int",
		"default": "0",
		"insert_after": "pos_closing_shift",
		"description": (
			"Operational counter incremented on each invoice submitted against "
			"this shift. Not financial data."
		),
		"read_only": 1,
		"no_copy": 1,
		"print_hide": 1,
		"allow_on_submit": 1,
	},
	# --- Dynamic Promotion (ported, retargeted to Sales Invoice) ---
	{
		"dt": "Sales Invoice",
		"fieldname": "pos_pending_promotions",
		"label": "Pending Promotions",
		"fieldtype": "Long Text",
		"insert_after": "buyer_name",
		"no_copy": 1,
		"allow_on_submit": 0,
	},
	{
		"dt": "Sales Invoice",
		"fieldname": "pos_promotion_selections",
		"label": "Promotion Selections",
		"fieldtype": "Table",
		"options": "POS Promotion Selection",
		"insert_after": "pos_pending_promotions",
		"no_copy": 0,
		"allow_on_submit": 0,
		"read_only": 1,
	},
	{
		"dt": "Sales Invoice Item",
		"fieldname": "pos_promotion_instance",
		"label": "Promotion Instance",
		"fieldtype": "Data",
		"insert_after": "item_name",
		"no_copy": 0,
		"allow_on_submit": 0,
		"read_only": 1,
	},
	{
		"dt": "Sales Invoice Item",
		"fieldname": "pos_promotion_role",
		"label": "Promotion Role",
		"fieldtype": "Select",
		# Leading empty option is load-bearing: Frappe defaults a Select field to
		# its first option on new child rows. The source fixture carried the same
		# leading newline so plain (non-promotion) invoice rows stay empty.
		"options": "\nPromotion Parent\nPromotion Component",
		"insert_after": "pos_promotion_instance",
		"no_copy": 0,
		"allow_on_submit": 0,
		"read_only": 1,
	},
	{
		"dt": "Price List",
		"fieldname": "pos_price_group",
		"label": "Price Group",
		"fieldtype": "Link",
		"options": "Price Group",
		"insert_after": "price_list_name",
		"no_copy": 1,
		"allow_on_submit": 0,
		# Server-managed ownership link: read_only matches the source fixture so
		# Desk users cannot hand-edit it. Uniqueness is deliberately NOT flagged
		# here (source fixtures carry unique=0, and the fresh-insert CustomField
		# controller would turn unique=1 into a real UNIQUE index — see
		# _reconcile_structural_attrs). Uniqueness is enforced at construction.
		"read_only": 1,
		"unique": 0,
	},
	{
		"dt": "Item Price",
		"fieldname": "pos_price_group",
		"label": "Price Group",
		"fieldtype": "Link",
		"options": "Price Group",
		"insert_after": "price_list",
		"no_copy": 1,
		"allow_on_submit": 0,
		"read_only": 1,
		"unique": 0,
	},
	{
		"dt": "POS Profile",
		"fieldname": "pos_price_group",
		"label": "Price Group",
		"fieldtype": "Link",
		"options": "Price Group",
		"insert_after": "selling_price_list",
		"no_copy": 1,
		"allow_on_submit": 0,
		"read_only": 1,
	},
	{
		"dt": "POS Profile",
		"fieldname": "pos_previous_price_list",
		"label": "Previous Price List",
		"fieldtype": "Link",
		"options": "Price List",
		"insert_after": "pos_price_group",
		"no_copy": 1,
		"allow_on_submit": 0,
		"read_only": 1,
	},
	{
		"dt": "Promotion Component",
		"fieldname": "custom_item_name",
		"label": "Item Name",
		"fieldtype": "Data",
		"insert_after": "item_code",
		"description": "Nama item ditampilkan untuk kemudahan input",
		"no_copy": 0,
		"allow_on_submit": 0,
		"read_only": 1,
		"in_list_view": 1,
		"fetch_from": "item_code.item_name",
		"fetch_if_empty": 1,
	},
	{
		"dt": "Promotion Option",
		"fieldname": "custom_item_name",
		"label": "Item Name",
		"fieldtype": "Data",
		"insert_after": "item_code",
		"description": "Nama item ditampilkan untuk kemudahan input",
		"no_copy": 0,
		"allow_on_submit": 0,
		"read_only": 1,
		"in_list_view": 1,
		"fetch_from": "item_code.item_name",
		"fetch_if_empty": 1,
	},
]


def after_install():
	"""Hook that runs after app installation"""
	try:
		log_message("POS Next: Running post-install setup", level="info")

		# Create app-owned Custom Fields if absent
		setup_custom_fields()

		# Setup default print format for POS Profiles
		setup_default_print_format()

		# Clear cache to ensure changes take effect
		frappe.clear_cache()
		frappe.db.commit()

		log_message("POS Next: Installation completed successfully", level="success")
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="POS Next Installation Error", message=frappe.get_traceback())
		log_message(f"POS Next: Installation error - {e!s}", level="error")
		raise


def after_migrate():
	"""Hook that runs after bench migrate"""
	try:
		# Reclaim POS Settings if ERPNext re-imported its Single on top of ours.
		# Must run in after_migrate (not as a one-shot patch) because ERPNext's
		# doctype sync runs after pos_next's and would overwrite anything we did
		# during pre/post-model-sync.
		reclaim_pos_settings_doctype(quiet=True)

		# Create app-owned Custom Fields if absent (insert-if-absent, never
		# overwrites — Desk relabelling survives re-migrate)
		setup_custom_fields(quiet=True)

		# Setup default print format
		setup_default_print_format(quiet=True)

		# Clear cache
		frappe.clear_cache()
		frappe.db.commit()

		log_message("POS Next: Migration completed successfully", level="success")
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="POS Next Migration Error", message=frappe.get_traceback())
		log_message(f"POS Next: Migration error - {str(e)}", level="error")
		raise


def setup_custom_fields(quiet=False):
	"""Create the app-owned Custom Fields listed in `CUSTOM_FIELDS`.

	Insert-if-absent, never overwrite: if a field already exists (by its
	`<Dt>-<fieldname>` name), it is left completely untouched, so a label an
	admin changed in the Desk survives every subsequent migrate. Idempotent —
	callable multiple times in one process and from both `after_install` and
	`after_migrate`.

	Validation is NOT suppressed: `CustomField.validate` computes `idx` from
	`insert_after` and checks for fieldname conflicts against the target
	DocType. Every `insert_after` target in `CUSTOM_FIELDS` is a real field.

	Args:
		quiet (bool): If True, suppress per-field logs

	Returns:
		int: Number of Custom Fields created by this call.
	"""
	created_count = 0
	reconciled_count = 0
	try:
		for spec in CUSTOM_FIELDS:
			cf_name = f"{spec['dt']}-{spec['fieldname']}"
			if frappe.db.exists("Custom Field", cf_name):
				# Insert-if-absent protects operator-facing text, but server-managed
				# structural attrs (read_only/unique) must still match the spec.
				# Self-heal only those, never label/description.
				if _reconcile_structural_attrs(cf_name, spec):
					reconciled_count += 1
				continue

			doc = frappe.get_doc(
				{
					"doctype": "Custom Field",
					"module": "POS Next",
					"permlevel": 0,
					"hidden": 0,
					"is_system_generated": 0,
					**spec,
				}
			)
			doc.insert(ignore_permissions=True)
			created_count += 1
			if not quiet:
				log_message(f"Created Custom Field: {cf_name}", level="info", indent=1)

		if created_count or reconciled_count:
			frappe.db.commit()
			if not quiet:
				if created_count:
					log_message(
						f"Created {created_count} Custom Field(s)", level="success", indent=1
					)
				if reconciled_count:
					log_message(
						f"Reconciled structural attrs on {reconciled_count} Custom Field(s)",
						level="success",
						indent=1,
					)
		elif not quiet:
			log_message("Custom Fields already present, nothing to create", level="info")

	except Exception as e:
		frappe.db.rollback()
		log_message(f"Error setting up custom fields: {str(e)}", level="error")
		frappe.log_error(title="Custom Fields Setup Error", message=frappe.get_traceback())

	return created_count


def _reconcile_structural_attrs(cf_name, spec):
	"""Self-heal server-managed structural attrs on an existing Custom Field.

	Returns True if any attr was written back, False if the live row already
	matched the spec (or the spec declares none of the reconciled attrs). Only
	the attrs in `_RECONCILE_ATTRS` are compared and written — never
	`label`/`description`/`fieldtype`/`insert_after` — so an admin's Desk
	relabel still survives every migrate (see the relabel-survival test).

	The write is a raw `frappe.db.set_value`, deliberately bypassing the
	CustomField controller: a Link field carrying `unique=1` would otherwise
	make `on_update` run `db.updatedb` and add a real UNIQUE index, which is a
	DL change the reconciliation must not trigger. The flag is metadata only;
	uniqueness itself is enforced at construction time in the app, not here.
	"""
	updated = {}
	for attr in _RECONCILE_ATTRS:
		if attr not in spec:
			continue
		live = frappe.db.get_value("Custom Field", cf_name, attr)
		if cint(live) != cint(spec[attr]):
			updated[attr] = spec[attr]
	if not updated:
		return False
	frappe.db.set_value("Custom Field", cf_name, updated, update_modified=False)
	return True


def setup_default_print_format(quiet=False):
	"""
	Set POS Next Receipt as default print format for POS Profiles if not already set.

	Args:
		quiet (bool): If True, suppress detailed logs
	"""
	try:
		# Check if the print format exists
		if not frappe.db.exists("Print Format", "POS Next Receipt"):
			if not quiet:
				log_message(
					"POS Next Receipt print format not found, skipping default setup", level="warning"
				)
			return

		# Get all POS Profiles without a print format
		pos_profiles = frappe.get_all(
			"POS Profile", filters={"print_format": ["in", ["", None]]}, fields=["name"]
		)

		if pos_profiles:
			updated_count = 0
			for profile in pos_profiles:
				try:
					frappe.db.set_value(
						"POS Profile", profile.name, "print_format", "POS Next Receipt", update_modified=False
					)
					if not quiet:
						log_message(f"Set default print format for: {profile.name}", level="info", indent=1)
					updated_count += 1
				except Exception as e:
					log_message(
						f"Error updating POS Profile {profile.name}: {str(e)}", level="error", indent=1
					)

			if updated_count > 0 and not quiet:
				log_message(
					f"Updated {updated_count} POS Profile(s) with default print format", level="success"
				)

	except Exception as e:
		log_message(f"Error setting up default print format: {str(e)}", level="error")
		frappe.log_error(title="Default Print Format Setup Error", message=frappe.get_traceback())


def log_message(message, level="info", indent=0):
	"""
	Standardized logging function with consistent formatting.

	Args:
		message (str): The message to log
		level (str): Log level - info, success, warning, error
		indent (int): Indentation level (0, 1, 2, etc.)
	"""
	indent_str = "  " * indent

	prefixes = {
		"info": "[INFO]",
		"success": "[SUCCESS]",
		"warning": "[WARNING]",
		"error": "[ERROR]",
	}

	prefix = prefixes.get(level, "[INFO]")
	formatted_message = f"{indent_str}{prefix} {message}"

	# Print to console
	print(formatted_message)

	# Also log to frappe logger
	if level == "error":
		logger.error(message)
	elif level == "warning":
		logger.warning(message)
	else:
		logger.info(message)


def reclaim_pos_settings_doctype(quiet=False):
	"""Reclaim the `POS Settings` DocType from ERPNext.

	ERPNext ships a Single `POS Settings` (module Accounts) with only
	`invoice_fields` and `pos_search_fields`. POS Next ships its own
	non-Single `POS Settings` (module POS Next) with per-profile config
	and a `barcode_rules` child table. Because ERPNext is in our
	`required_apps` its doctype sync runs after ours during `bench
	migrate`, so its JSON wins on disk unless we re-install our version
	after both apps have finished syncing.

	Runs from `after_migrate`. Idempotent: if the live doctype already
	belongs to POS Next (module == 'POS Next' and not Single), exits
	without touching anything.
	"""
	if not frappe.db.exists("DocType", "POS Settings"):
		if not quiet:
			log_message("POS Settings DocType missing, skipping reclaim", level="warning")
		return

	row = frappe.db.get_value("DocType", "POS Settings", ["module", "issingle"], as_dict=True)
	if row and row.module == "POS Next" and not row.issingle:
		if not quiet:
			log_message("POS Settings already owned by POS Next, nothing to reclaim", level="info")
		return

	if not quiet:
		log_message(
			f"Reclaiming POS Settings DocType (was module={row.module if row else '?'}, "
			f"issingle={row.issingle if row else '?'})",
			level="warning",
		)

	try:
		# Commit any open transaction first — DROP TABLE is DDL and would
		# otherwise trigger ImplicitCommitError under Frappe's safety check.
		frappe.db.commit()
		frappe.db.sql("DROP TABLE IF EXISTS `tabPOS Settings`")
		frappe.db.commit()
		frappe.db.sql("DELETE FROM `tabSingles` WHERE doctype = 'POS Settings'")
		frappe.db.sql("DELETE FROM `tabDocField` WHERE parent = 'POS Settings'")
		frappe.db.sql("DELETE FROM `tabDocPerm` WHERE parent = 'POS Settings'")
		frappe.db.sql("DELETE FROM `tabDocType` WHERE name = 'POS Settings'")
		frappe.db.commit()
		log_message("Dropped legacy POS Settings meta + table", level="info", indent=1)
	except Exception:
		frappe.log_error(
			title="POS Settings Reclaim Error",
			message="Failed to drop legacy POS Settings\n\n" + frappe.get_traceback(),
		)
		raise

	try:
		frappe.reload_doc("pos_next", "doctype", "pos_settings", force=True)
		frappe.reload_doc("pos_next", "doctype", "pos_barcode_rules", force=True)
		frappe.reload_doc("pos_next", "doctype", "pos_allowed_locale", force=True)
		frappe.db.commit()
	except Exception:
		frappe.log_error(
			title="POS Settings Reclaim Error",
			message="Failed to reload pos_next doctypes\n\n" + frappe.get_traceback(),
		)
		raise

	after = frappe.db.get_value("DocType", "POS Settings", ["module", "issingle"], as_dict=True)
	if not after or after.module != "POS Next" or after.issingle:
		frappe.log_error(
			title="POS Settings Reclaim Error",
			message=(
				f"Reclaim ran but doctype still wrong: {after}. "
				"ERPNext may be re-importing POS Settings later in the migration."
			),
		)
		log_message(f"Reclaim verification FAILED — doctype is now {after}", level="error")
		return

	if not quiet:
		log_message(
			f"POS Settings reclaimed (module={after.module}, issingle={after.issingle})",
			level="success",
		)
