# Copyright (c) 2025, POS Next and contributors
# For license information, please see license.txt

"""Scheduled tasks for POS Next."""

import frappe
from frappe.utils import getdate, nowdate, now


def disable_expired_pricing_rules():
	"""
	Automatically disable Pricing Rules that have passed their valid_upto date.
	Runs daily to clean up expired pricing rules.
	"""
	try:
		today = nowdate()

		# Find all active pricing rules with expired valid_upto dates
		expired_rules = frappe.db.sql(
			"""
			SELECT name, title, valid_upto
			FROM `tabPricing Rule`
			WHERE disable = 0
				AND valid_upto IS NOT NULL
				AND valid_upto < %s
			""",
			(today,),
			as_dict=1,
		)

		if not expired_rules:
			frappe.logger().info("No expired pricing rules found")
			return {"success": True, "disabled_count": 0, "message": "No expired pricing rules to disable"}

		errors = []

		# One bulk UPDATE carries the same predicate the SELECT above applied;
		# per-rule set_value round trips (and per-rule error handling) collapsed
		# into a single statement. Non-empty: the early return above guarantees it.
		try:
			frappe.db.sql(
				"""
				UPDATE `tabPricing Rule`
				SET disable = 1, modified = %(now)s
				WHERE name IN %(names)s
				""",
				{"names": [rule.name for rule in expired_rules], "now": now()},
			)
			disabled_count = len(expired_rules)
		except Exception as e:
			error_msg = f"Failed to disable expired pricing rules: {e!s}"
			frappe.logger().error(error_msg)
			errors.append(error_msg)
			disabled_count = 0

		# Commit all changes
		frappe.db.commit()

		# Log summary
		summary = f"Disabled {disabled_count} expired pricing rule(s)"
		if errors:
			summary += f" with {len(errors)} error(s)"

		frappe.logger().info(summary)

		return {"success": True, "disabled_count": disabled_count, "errors": errors, "message": summary}

	except Exception as e:
		frappe.log_error(title="Disable Expired Pricing Rules Error", message=frappe.get_traceback())
		return {"success": False, "error": str(e)}


def disable_expired_promotional_schemes():
	"""
	Automatically disable Promotional Schemes that have passed their valid_upto date.
	Runs daily to clean up expired promotional schemes.
	"""
	try:
		# Check if Promotional Scheme doctype exists
		if not frappe.db.table_exists("Promotional Scheme"):
			frappe.logger().info("Promotional Scheme doctype does not exist, skipping...")
			return {
				"success": True,
				"disabled_count": 0,
				"message": "Promotional Scheme doctype not available",
			}

		today = nowdate()

		# Find all active promotional schemes with expired valid_upto dates
		expired_schemes = frappe.db.sql(
			"""
			SELECT name, selling_or_buying, valid_upto
			FROM `tabPromotional Scheme`
			WHERE disable = 0
				AND valid_upto IS NOT NULL
				AND valid_upto < %s
			""",
			(today,),
			as_dict=1,
		)

		if not expired_schemes:
			frappe.logger().info("No expired promotional schemes found")
			return {
				"success": True,
				"disabled_count": 0,
				"message": "No expired promotional schemes to disable",
			}

		errors = []

		# One bulk UPDATE, same predicate as the SELECT above; see the pricing
		# rules loop for the rationale. Non-empty: the early return guarantees it.
		try:
			frappe.db.sql(
				"""
				UPDATE `tabPromotional Scheme`
				SET disable = 1, modified = %(now)s
				WHERE name IN %(names)s
				""",
				{"names": [scheme.name for scheme in expired_schemes], "now": now()},
			)
			disabled_count = len(expired_schemes)
		except Exception as e:
			error_msg = f"Failed to disable expired promotional schemes: {e!s}"
			frappe.logger().error(error_msg)
			errors.append(error_msg)
			disabled_count = 0

		# Commit all changes
		frappe.db.commit()

		# Log summary
		summary = f"Disabled {disabled_count} expired promotional scheme(s)"
		if errors:
			summary += f" with {len(errors)} error(s)"

		frappe.logger().info(summary)

		return {"success": True, "disabled_count": disabled_count, "errors": errors, "message": summary}

	except Exception as e:
		frappe.log_error(title="Disable Expired Promotional Schemes Error", message=frappe.get_traceback())
		return {"success": False, "error": str(e)}


def cleanup_expired_promotions():
	"""
	Master function that disables both expired pricing rules and promotional schemes.
	This is the main scheduled task that runs daily.
	"""
	frappe.logger().info("Starting cleanup of expired promotions...")

	# Disable expired pricing rules
	pricing_result = disable_expired_pricing_rules()

	# Disable expired promotional schemes
	schemes_result = disable_expired_promotional_schemes()

	# Summary log
	total_disabled = pricing_result.get("disabled_count", 0) + schemes_result.get("disabled_count", 0)

	frappe.logger().info(f"Cleanup completed: {total_disabled} expired promotion(s) disabled")

	return {
		"success": True,
		"pricing_rules": pricing_result,
		"promotional_schemes": schemes_result,
		"total_disabled": total_disabled,
	}
