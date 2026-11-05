# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, get_datetime, getdate

from pos_next.services.outlet_monitoring_access import is_reviewer

IMMUTABLE_REPORT_FIELDS = (
	"company",
	"pos_profile",
	"cadence",
	"template",
	"opening_shift",
	"operational_date",
	"obligation_key",
	"revision",
	"correction_of",
	"due_at",
)

SUBMISSION_FIELDS = (
	"submitted_at",
	"submitted_by",
	"timeliness",
)

REVIEW_HEADER_FIELDS = (
	"review_result",
	"correction_requested",
	"review_note",
	"reviewed_by",
	"reviewed_at",
	"waive_reason",
)


def _same_value(doctype, fieldname, value, stored):
	fieldtype = frappe.get_meta(doctype).get_field(fieldname).fieldtype
	if fieldtype in ("Int", "Check"):
		convert = cint
	elif fieldtype in ("Float", "Currency", "Percent"):
		convert = flt
	elif fieldtype in ("Date", "Datetime"):
		convert = getdate if fieldtype == "Date" else get_datetime
		return (convert(value) if value else None) == (convert(stored) if stored else None)
	else:
		return (value or "") == (stored or "")
	return convert(value) == convert(stored)


class POSMonitoringReport(Document):
	def _validate_snapshot(self, table, mutable_fields=()):
		rows = self.get(table) or []
		doctype = self.meta.get_field(table).options
		fields = [df.fieldname for df in frappe.get_meta(doctype).fields if df.fieldtype not in ("Section Break", "Column Break") and df.fieldname not in mutable_fields]
		stored = {row.name: row for row in frappe.get_all(doctype, filters={"parent": self.name}, fields=["name", *fields])}
		if len(rows) != len(stored) or {row.name for row in rows} != set(stored):
			frappe.throw(_("Cannot add, remove or duplicate report {0}").format(table), frappe.PermissionError)
		for row in rows:
			if any(not _same_value(doctype, field, row.get(field), stored[row.name].get(field)) for field in fields):
				frappe.throw(_("Cannot modify report {0} snapshot").format(table), frappe.PermissionError)

	def _validate_checkpoint_snapshot(self):
		if not self.flags.internal_checkpoint_change:
			mutable = ["assessment", "assessment_note"]
			if self.flags.internal_media_change:
				mutable.append("evidence_complete")
			self._validate_snapshot("checkpoints", mutable)

	def validate(self):
		if not self.status:
			self.status = "Draft"
		if not self.revision:
			self.revision = 1

		user = frappe.session.user
		reviewer = is_reviewer(user)
		ignore_perm = bool(self.flags.ignore_permissions or user == "Administrator")

		# Protect review fields and checkpoint assessment on insert or update
		if not reviewer and not ignore_perm:
			for child in self.checkpoints:
				if child.assessment and child.assessment != "Pending":
					frappe.throw(_("Only reviewers can set checkpoint assessment"), frappe.PermissionError)
				if child.assessment_note:
					frappe.throw(_("Only reviewers can set checkpoint assessment note"), frappe.PermissionError)

			for field in REVIEW_HEADER_FIELDS:
				if self.get(field):
					frappe.throw(_("Only reviewers can set review fields"), frappe.PermissionError)

		# Protect new documents from client-controlled server fields
		if self.is_new() and not ignore_perm:
			if self.get("due_at"):
				frappe.throw(_("due_at cannot be set on report creation"), frappe.PermissionError)
			for f in SUBMISSION_FIELDS:
				if self.get(f):
					frappe.throw(_("Submission metadata cannot be set on report creation"), frappe.PermissionError)
			if self.status and self.status != "Draft":
				frappe.throw(_("New report must have status Draft"), frappe.PermissionError)
			if self.revision and cint(self.revision) != 1:
				frappe.throw(_("New report revision must be 1"), frappe.PermissionError)

		# Protect child table media against unauthorized manipulation
		self._validate_media_rows(ignore_perm)

		# Validations on existing documents
		if not self.is_new():
			stored = frappe.db.get_value(
				"POS Monitoring Report",
				self.name,
				[*IMMUTABLE_REPORT_FIELDS, *SUBMISSION_FIELDS, "docstatus", "status"],
				as_dict=True,
			)
			if stored:
				if not ignore_perm:
					for field in IMMUTABLE_REPORT_FIELDS:
						if not _same_value(self.doctype, field, self.get(field), stored.get(field)):
							frappe.throw(
								_("Cannot modify {0} of an existing report").format(field),
								frappe.PermissionError,
							)

					for field in SUBMISSION_FIELDS:
						if not _same_value(self.doctype, field, self.get(field), stored.get(field)):
							frappe.throw(
								_("Cannot modify {0} of an existing report").format(field),
								frappe.PermissionError,
							)

					if stored.docstatus == 0 and self.status != stored.status:
						if not getattr(self.flags, "internal_status_change", False):
							frappe.throw(
								_("Draft report status cannot be changed directly"),
								frappe.PermissionError,
							)

					self._validate_checkpoint_snapshot()

				# Status transition validation after submit
				if stored.docstatus == 1 and self.status != stored.status:
					if not reviewer and not ignore_perm:
						frappe.throw(_("Only reviewers can update report status after submit"), frappe.PermissionError)
					allowed_transitions = {
						"Submitted": {"Reviewed", "Waived"},
						"Missed": {"Waived"},
					}
					if self.status not in allowed_transitions.get(stored.status, set()):
						frappe.throw(
							_("Invalid status transition from {0} to {1}").format(stored.status, self.status),
							frappe.ValidationError,
						)
					if self.status == "Waived" and not (self.waive_reason and self.waive_reason.strip()):
						frappe.throw(_("Waive reason is required"), frappe.ValidationError)

	def _validate_media_rows(self, ignore_perm: bool):
		if not getattr(self.flags, "internal_media_change", False) and not ignore_perm:
			if self.is_new():
				if getattr(self, "media", None):
					frappe.throw(_("Media can only be added via register_media API"), frappe.PermissionError)
			else:
				self._validate_snapshot("media")

		if not getattr(self, "media", None):
			return

		user = frappe.session.user
		for row in self.media:
			if cint(row.purged):
				continue
			if not row.file_url:
				frappe.throw(_("Media file_url is required"), frappe.ValidationError)

			file_data = frappe.db.get_value(
				"File",
				{"file_url": row.file_url},
				["name", "is_private", "file_size", "attached_to_doctype", "attached_to_name", "owner"],
				as_dict=True,
			)
			if not file_data:
				frappe.throw(_("Attached file does not exist: {0}").format(row.file_url), frappe.ValidationError)
			if not file_data.is_private:
				frappe.throw(_("Media file must be private"), frappe.ValidationError)
			if not ignore_perm and (file_data.attached_to_doctype != "POS Monitoring Report" or file_data.attached_to_name != self.name):
				frappe.throw(_("File is not attached to this report"), frappe.ValidationError)
			if not ignore_perm and user != "Administrator" and file_data.owner != user:
				frappe.throw(_("Cannot register file owned by another user"), frappe.PermissionError)

			matching_cp = [cp for cp in self.checkpoints if cp.name == row.checkpoint_row or cp.checkpoint == row.checkpoint_row]
			if not matching_cp:
				frappe.throw(_("Checkpoint row not found for media"), frappe.ValidationError)
			cp = matching_cp[0]
			if cp.media_type not in ("Both", "Photo or Video") and row.media_type != cp.media_type:
				frappe.throw(
					_("Media type {0} does not match checkpoint media type {1}").format(row.media_type, cp.media_type),
					frappe.ValidationError,
				)

	def before_update_after_submit(self):
		user = frappe.session.user
		reviewer = is_reviewer(user)
		ignore_perm = bool(self.flags.ignore_permissions or user == "Administrator")

		stored = frappe.db.get_value(
			"POS Monitoring Report",
			self.name,
			[
				*IMMUTABLE_REPORT_FIELDS,
				*SUBMISSION_FIELDS,
				"docstatus",
				"status",
				"submitted_by",
				"review_result",
				"waive_reason",
				"correction_requested",
				"review_note",
				"reviewed_by",
				"reviewed_at",
			],
			as_dict=True,
		)
		if not stored:
			return

		if not ignore_perm:
			self._validate_checkpoint_snapshot()
			if not self.flags.internal_media_change:
				self._validate_snapshot("media")
			for field in (*IMMUTABLE_REPORT_FIELDS, *SUBMISSION_FIELDS):
				if not _same_value(self.doctype, field, self.get(field), stored.get(field)):
					frappe.throw(
						_("Cannot modify {0} of an existing report").format(field),
						frappe.PermissionError,
					)

			# Once finalized (Reviewed or Waived), locked against edits
			if stored.status in ("Reviewed", "Waived"):
				if self.status != stored.status:
					frappe.throw(_("Finalized report status cannot be changed"), frappe.PermissionError)
				for field in REVIEW_HEADER_FIELDS:
					if not _same_value(self.doctype, field, self.get(field), stored.get(field)):
						frappe.throw(_("Finalized report review fields cannot be modified"), frappe.PermissionError)
				self._validate_snapshot("checkpoints", ("evidence_complete",))

		# Media cannot be modified after submit
		stored_media = set(frappe.get_all("POS Monitoring Report Media", filters={"parent": self.name}, pluck="name"))
		current_media = {m.name for m in getattr(self, "media", []) if m.name}
		if not ignore_perm and (stored_media != current_media or len(getattr(self, "media", [])) != len(stored_media)):
			frappe.throw(_("Cannot add or remove media from submitted report"), frappe.PermissionError)

		# Status transitions
		if self.status != stored.status:
			if not reviewer and not ignore_perm:
				frappe.throw(_("Only reviewers can update report status after submit"), frappe.PermissionError)
			allowed_transitions = {
				"Submitted": {"Reviewed", "Waived"},
				"Missed": {"Waived"},
			}
			if self.status not in allowed_transitions.get(stored.status, set()):
				frappe.throw(
					_("Invalid status transition from {0} to {1}").format(stored.status, self.status),
					frappe.ValidationError,
				)

			if self.status == "Reviewed":
				s = frappe.get_cached_doc("POS Monitoring Settings")
				if s.prevent_self_review and stored.submitted_by == user and user != "Administrator":
					frappe.throw(_("Self-review is not allowed"), frappe.PermissionError)

				for cp in self.checkpoints:
					if not cp.assessment or cp.assessment == "Pending":
						frappe.throw(
							_("All checkpoints must be assessed before finalizing"),
							frappe.PermissionError if not getattr(self.flags, "internal_review_finalize", False) else frappe.ValidationError,
						)
					if cp.assessment == "N/A" and not s.allow_na:
						frappe.throw(_("N/A assessment is not allowed"), frappe.ValidationError)
					if cp.assessment == "Fail" and s.require_note_on_fail and not (cp.assessment_note or "").strip():
						frappe.throw(_("Note is required for Failed checkpoints"), frappe.ValidationError)
					if cp.assessment == "N/A" and s.require_note_on_na and not (cp.assessment_note or "").strip():
						frappe.throw(_("Note is required for N/A checkpoints"), frappe.ValidationError)

				has_fail = any(cp.assessment == "Fail" for cp in self.checkpoints)
				self.review_result = "Fail" if has_fail else "Pass"
				if self.correction_requested and not s.allow_correction:
					frappe.throw(_("Correction is not allowed by settings"), frappe.ValidationError)
			elif self.status == "Waived":
				if not (self.waive_reason and self.waive_reason.strip()):
					frappe.throw(_("Waive reason is required"), frappe.ValidationError)
			# Attribution is server-owned; never trust client-supplied values.
			self.reviewed_by = frappe.session.user
			self.reviewed_at = frappe.utils.now_datetime()
		elif self.status == "Submitted":
			if not reviewer and not ignore_perm:
				frappe.throw(_("Only reviewers can update assessments on submitted reports"), frappe.PermissionError)

	def before_cancel(self):
		frappe.throw(_("POS Monitoring Report cannot be cancelled"), frappe.ValidationError)

	@staticmethod
	def on_doctype_update():
		frappe.db.add_unique(
			"POS Monitoring Report",
			["obligation_key", "revision"],
			constraint_name="unique_obligation_revision",
		)


def on_doctype_update():
	POSMonitoringReport.on_doctype_update()
