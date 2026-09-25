"""
Uninstallation hooks for POS Next

REL-03: uninstall must clean up EVERYTHING the app installs, not a stale
hand-written subset. The custom-field cleanup is derived from the same
constants install.py uses (single source of truth, verified by
pos_next/tests/test_uninstall_coverage.py). Roles, Custom DocPerms, the
workspace and its sidebar entry are listed in the module constants below.
Every step is idempotent and guarded so it cannot crash on an already-clean
site. This module is never executed by tests; tests only inspect its
structures and mock the deletion calls.
"""

import logging

import frappe

from pos_next.install import (
	CUSTOM_FIELDS as INSTALL_CUSTOM_FIELDS,
	MIRROR_NAME_PREFIX,
	PRICE_GROUP_CUSTOM_FIELDS,
)
from pos_next.price_group_ownership import (
	ITEM_PRICE_OWNER_FIELD,
	PRICE_LIST_OWNER_FIELD,
	PROFILE_OWNER_FIELD,
	PROFILE_PREVIOUS_PRICE_LIST_FIELD,
)

# Configure logger
logger = logging.getLogger(__name__)

# Roles shipped by the app (hooks.py:fixtures) and their Custom DocPerms
UNINSTALL_ROLES = ("POSNext Cashier", "Nexus POS Manager")
UNINSTALL_WORKSPACES = ("POSNext",)
UNINSTALL_WORKSPACE_SIDEBARS = ("POSNext",)

# Custom fields created by earlier pos_next versions outside the install
# constants (pre-fixture era). Kept in the cleanup so old sites uninstall
# cleanly too.
_LEGACY_CUSTOM_FIELDS = {
	"Sales Invoice": ["posa_pos_opening_shift", "posa_is_printed"],
	"Sales Invoice Item": [
		"pos_package",
		"pos_package_instance",
		"pos_package_role",
		"pos_package_snapshot",
	],
	"Price List": [PRICE_LIST_OWNER_FIELD],
	"Item Price": [ITEM_PRICE_OWNER_FIELD],
	"POS Profile": [PROFILE_OWNER_FIELD, PROFILE_PREVIOUS_PRICE_LIST_FIELD],
}


def _build_cleanup_custom_fields():
	"""Merge every field source into one doctype -> [fieldnames] structure."""
	merged = {}
	for source in (INSTALL_CUSTOM_FIELDS, PRICE_GROUP_CUSTOM_FIELDS, _LEGACY_CUSTOM_FIELDS):
		for doctype, fields in source.items():
			names = merged.setdefault(doctype, [])
			for df in fields:
				fieldname = df["fieldname"] if isinstance(df, dict) else df
				if fieldname not in names:
					names.append(fieldname)
	return merged


# The single test-inspectable source remove_custom_fields consumes.
CLEANUP_CUSTOM_FIELDS = _build_cleanup_custom_fields()


def before_uninstall():
	"""
	Hook that runs before app uninstallation
	Cleans up custom fields, roles, permissions, workspace and configurations
	"""
	try:
		log_message("Starting POS Next uninstallation", level="info")

		# Remove custom fields
		remove_custom_fields()

		# Remove custom doc perms (before the roles they link to)
		remove_custom_docperms()

		# Remove the app's roles
		remove_roles()

		# Remove the workspace and its sidebar entry
		remove_workspaces()

		# Remove print formats
		remove_print_formats()

		# Reset POS Profile configurations
		reset_pos_profiles()

		# Commit all changes
		frappe.db.commit()

		log_message("POS Next uninstalled successfully", level="success")
		log_message("All custom fields and configurations have been removed", level="info")

	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="POS Next Uninstallation Error", message=frappe.get_traceback())
		log_message(f"Error during POS Next uninstallation: {e!s}", level="error")
		raise


def remove_custom_fields():
	"""
	Remove all custom fields created by POS Next.

	Routes through frappe's delete_custom_fields (exists-guarded per field,
	drops the column via the Custom Field doc's own on_trash). Idempotent.
	"""
	from frappe.custom.doctype.custom_field.custom_field import delete_custom_fields

	try:
		log_message("Removing custom fields", level="info")
		delete_custom_fields(CLEANUP_CUSTOM_FIELDS)
		log_message(f"Cleaned {sum(len(v) for v in CLEANUP_CUSTOM_FIELDS.values())} custom field definition(s)", level="success")
	except Exception as e:
		log_message(f"Error removing custom fields: {e!s}", level="error")
		frappe.log_error(title="Custom Fields Removal Error", message=frappe.get_traceback())


def remove_custom_docperms():
	"""
	Remove Custom DocPerm rows created for the app's roles (the
	custom_docperm fixture) and the posnext-mirror:: rows the installer
	mirrors in on every migrate. Raw deletes are idempotent.
	"""
	try:
		log_message("Removing Custom DocPerms", level="info")
		frappe.db.delete("Custom DocPerm", {"role": ("in", list(UNINSTALL_ROLES))})
		frappe.db.delete("Custom DocPerm", {"name": ("like", f"{MIRROR_NAME_PREFIX}%")})
	except Exception as e:
		log_message(f"Error removing Custom DocPerms: {e!s}", level="error")
		frappe.log_error(title="Custom DocPerm Removal Error", message=frappe.get_traceback())


def remove_roles():
	"""
	Remove the app's roles. Any DocPerm/Custom DocPerm/Has Role rows still
	pointing at them are cleared first so delete_doc cannot fail on links.
	"""
	try:
		log_message("Removing roles", level="info")
		for role in UNINSTALL_ROLES:
			frappe.db.delete("DocPerm", {"role": role})
			frappe.db.delete("Has Role", {"role": role})
			if frappe.db.exists("Role", role):
				frappe.delete_doc("Role", role, force=True, ignore_permissions=True)
				log_message(f"Removed Role: {role}", level="info", indent=1)
	except Exception as e:
		log_message(f"Error removing roles: {e!s}", level="error")
		frappe.log_error(title="Role Removal Error", message=frappe.get_traceback())


def remove_workspaces():
	"""
	Remove the POSNext workspace and its sidebar entry (Workspace Sidebar is
	a separate doc in Frappe v16 and holds the boot sidebar items).
	"""
	try:
		log_message("Removing workspace", level="info")
		for workspace in UNINSTALL_WORKSPACES:
			if frappe.db.exists("Workspace", workspace):
				frappe.delete_doc("Workspace", workspace, force=True, ignore_permissions=True)
				log_message(f"Removed Workspace: {workspace}", level="info", indent=1)
		for sidebar in UNINSTALL_WORKSPACE_SIDEBARS:
			if frappe.db.exists("Workspace Sidebar", sidebar):
				frappe.delete_doc("Workspace Sidebar", sidebar, force=True, ignore_permissions=True)
				log_message(f"Removed Workspace Sidebar: {sidebar}", level="info", indent=1)
	except Exception as e:
		log_message(f"Error removing workspace: {e!s}", level="error")
		frappe.log_error(title="Workspace Removal Error", message=frappe.get_traceback())


def remove_print_formats():
	"""
	Remove all print formats created by POS Next
	"""
	try:
		log_message("Removing print formats", level="info")

		# List of print formats to remove
		print_formats = [
			"POS Next Receipt",
			"POS Next EOD Report",
		]

		removed_count = 0
		skipped_count = 0

		for format_name in print_formats:
			try:
				if frappe.db.exists("Print Format", format_name):
					# Check if it's being used by any POS Profile
					pos_profiles_using = frappe.get_all(
						"POS Profile", filters={"print_format": format_name}, fields=["name"]
					)

					if pos_profiles_using:
						# Reset those POS Profiles first
						for profile in pos_profiles_using:
							try:
								doc = frappe.get_doc("POS Profile", profile.name)
								doc.print_format = ""
								doc.flags.ignore_permissions = True
								doc.save()
								log_message(
									f"Reset print format for POS Profile: {profile.name}",
									level="info",
									indent=2,
								)
							except Exception as e:
								log_message(
									f"Error resetting POS Profile {profile.name}: {e!s}",
									level="error",
									indent=2,
								)

					# Now delete the print format
					frappe.delete_doc("Print Format", format_name, force=True, ignore_permissions=True)
					log_message(f"Removed Print Format: {format_name}", level="info", indent=1)
					removed_count += 1
				else:
					log_message(f"Print Format not found: {format_name}", level="info", indent=1)
					skipped_count += 1
			except Exception as e:
				log_message(f"Error removing print format {format_name}: {e!s}", level="error", indent=1)

		if removed_count > 0:
			log_message(f"Removed {removed_count} print format(s)", level="success")
		if skipped_count > 0:
			log_message(f"Skipped {skipped_count} format(s) (already removed or not found)", level="info")

	except Exception as e:
		log_message(f"Error removing print formats: {e!s}", level="error")
		frappe.log_error(title="Print Formats Removal Error", message=frappe.get_traceback())


def reset_pos_profiles():
	"""
	Reset POS Profile configurations set by POS Next
	"""
	try:
		log_message("Resetting POS Profile configurations", level="info")

		# Find POS Profiles using POS Next print format
		pos_profiles = frappe.get_all(
			"POS Profile", filters={"print_format": "POS Next Receipt"}, fields=["name"]
		)

		if not pos_profiles:
			log_message("No POS Profiles using POS Next configurations", level="info", indent=1)
			return

		reset_count = 0
		for profile in pos_profiles:
			try:
				doc = frappe.get_doc("POS Profile", profile.name)
				doc.print_format = ""
				doc.flags.ignore_permissions = True
				doc.flags.ignore_mandatory = True
				doc.save()
				log_message(f"Reset POS Profile: {profile.name}", level="info", indent=1)
				reset_count += 1
			except Exception as e:
				log_message(f"Error resetting POS Profile {profile.name}: {e!s}", level="error", indent=1)

		if reset_count > 0:
			log_message(f"Reset {reset_count} POS Profile(s)", level="success")

	except Exception as e:
		log_message(f"Error resetting POS Profiles: {e!s}", level="error")
		frappe.log_error(title="POS Profile Reset Error", message=frappe.get_traceback())


def log_message(message, level="info", indent=0):
	"""
	Standardized logging function with consistent formatting

	Args:
		message (str): The message to log
		level (str): Log level - info, success, warning, error
		indent (int): Indentation level (0, 1, 2, etc.)
	"""
	indent_str = "  " * indent

	# Log level prefixes
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

	# Also log to frappe logger with appropriate level
	if level == "error":
		logger.error(message)
	elif level == "warning":
		logger.warning(message)
	elif level == "success":
		logger.info(f"SUCCESS: {message}")
	else:
		logger.info(message)


def get_custom_fields_for_cleanup():
	"""
	Get list of custom fields that can be safely removed
	Returns list of "doctype-fieldname" names, derived from the same
	CLEANUP_CUSTOM_FIELDS structure remove_custom_fields consumes.
	"""
	return [
		f"{doctype}-{fieldname}"
		for doctype, fieldnames in CLEANUP_CUSTOM_FIELDS.items()
		for fieldname in fieldnames
	]


def validate_uninstall():
	"""
	Validate that uninstall can proceed safely
	Returns True if safe to uninstall, False otherwise with reason
	"""
	try:
		# Check if there are any active POS sessions
		# This is just an example - you can add more checks

		# For now, always return True
		return True, "Safe to uninstall"

	except Exception as e:
		return False, f"Validation error: {e!s}"
