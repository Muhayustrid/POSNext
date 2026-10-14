# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Shared controller logic for the confirmation-code doctypes (discount /
refund pools). Both controllers delegate here; the pools never validate each
other's codes — uniqueness is always scoped to `doc.doctype`."""

import secrets
import string

import frappe
from frappe import _
from frappe.utils import cint, getdate

# Unambiguous alphabet: no 0/O, 1/I/L — codes are dictated over the phone.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8
# Bounded retries when a freshly generated code collides with an existing row.
CODE_GENERATION_ATTEMPTS = 10
# Manual codes: A-Z0-9, 6-20 chars (min length is a floor against guessable values).
MANUAL_CODE_MIN_LENGTH = 6
MANUAL_CODE_MAX_LENGTH = 20
MANUAL_CODE_CHARSET = string.ascii_uppercase + string.digits


def random_code() -> str:
	return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def prepare_code(doc) -> None:
	"""Validate mixin for confirmation-code controllers. Normalizes `doc.code`
	(strip + upper, auto-generating when empty), enforces the manual format,
	per-doctype uniqueness, used-code immutability, date order and outlet scope.
	Raises frappe.ValidationError."""
	doc.code = (doc.code or "").strip().upper()
	if not doc.code:
		doc.code = _generate_unique_code(doc.doctype)

	# Manual-format check applies to new codes and to changed values only.
	# Legacy rows (existing doc, value unchanged) keep working as-is so an
	# old short code can still be disabled or edited in other fields.
	name = getattr(doc, "name", None)
	stored_code = frappe.db.get_value(doc.doctype, name, "code") if (name and not doc.is_new()) else None
	value_unchanged = stored_code is not None and doc.code == stored_code

	if not value_unchanged:
		if not MANUAL_CODE_MIN_LENGTH <= len(doc.code) <= MANUAL_CODE_MAX_LENGTH:
			frappe.throw(
				_("Code must be between {0} and {1} characters long").format(
					MANUAL_CODE_MIN_LENGTH, MANUAL_CODE_MAX_LENGTH
				)
			)

		invalid = set(doc.code) - set(MANUAL_CODE_CHARSET)
		if invalid:
			frappe.throw(_("Code may only contain letters and digits (without spaces or symbols)"))

	# Uniqueness per doctype with an explicit message instead of a raw DB
	# duplicate error.
	if frappe.db.exists(doc.doctype, {"code": doc.code, "name": ["!=", name or ""]}):
		frappe.throw(_("Code {0} is already in use").format(doc.code))

	# A code that already recorded usages is an audit anchor — only its
	# status may change from here on, never its value.
	if not doc.is_new() and cint(doc.used_count) > 0:
		previous = stored_code or ""
		if doc.code != previous:
			frappe.throw(_("A code that has already been used cannot be changed"))

	if doc.valid_from and doc.valid_upto and getdate(doc.valid_upto) < getdate(doc.valid_from):
		frappe.throw(_("Valid Upto cannot be before Valid From"))

	if (doc.company_scope or "All Outlets") != "All Outlets" and not doc.companies:
		frappe.throw(_("Select at least one outlet for the chosen scope"))


def _generate_unique_code(doctype: str) -> str:
	"""Random code that does not collide with an existing row (the insert
	would raise DuplicateEntryError; probe uniqueness here instead so the
	retry can happen before the save)."""
	for _ in range(CODE_GENERATION_ATTEMPTS):
		code = random_code()
		if not frappe.db.exists(doctype, {"code": code}):
			return code
	frappe.throw(_("Could not generate a unique code, please try again"))


def generate_codes_for(doctype: str, count: int, company: str | None = None, notes: str | None = None) -> dict:
	"""Generate multi-use confirmation codes for `doctype`.

	Head office only — requires create permission on the doctype (System
	Manager). Codes stay Active until disabled. When `company` is given, the
	code is scoped to that outlet only (Selected Outlets).
	"""
	frappe.has_permission(doctype, "create", throw=True)

	count = cint(count)
	if not 1 <= count <= 500:
		frappe.throw(_("Number of codes must be between 1 and 500"))

	company = (company or "").strip() or None
	notes = (notes or "").strip() or None

	if company and not frappe.db.exists("Company", company):
		frappe.throw(_("Company {0} does not exist").format(company))

	payload = {"doctype": doctype, "notes": notes, "status": "Active"}
	if company:
		payload["company_scope"] = "Selected Outlets"
		payload["companies"] = [{"company": company}]

	codes = []
	attempts = 0
	while len(codes) < count:
		attempts += 1
		if attempts > count * 5 + 10:
			frappe.throw(_("Could not generate unique codes, please try again"))
		code = random_code()
		try:
			frappe.get_doc({**payload, "code": code}).insert(ignore_permissions=True)
		except frappe.DuplicateEntryError:
			continue
		codes.append(code)

	return {"codes": codes}
