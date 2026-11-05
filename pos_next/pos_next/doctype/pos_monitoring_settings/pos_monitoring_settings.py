# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.core.api.file import get_max_file_size
from frappe.model.document import Document
from frappe.utils import cint, flt


class POSMonitoringSettings(Document):
	def validate(self):
		max_bytes = get_max_file_size()
		if flt(self.video_max_mb) * 1024 * 1024 > max_bytes:
			limit_mb = round(max_bytes / (1024 * 1024), 2)
			frappe.throw(
				_("Video max MB cannot exceed site limit ({0} MB)").format(limit_mb),
				frappe.ValidationError,
			)

		if cint(self.thumbnail_max_edge) >= cint(self.photo_max_edge):
			frappe.throw(
				_("Thumbnail max edge must be less than photo max edge"),
				frappe.ValidationError,
			)

		if not (10 <= cint(self.gallery_page_size) <= 200):
			frappe.throw(
				_("Gallery page size must be between 10 and 200"),
				frappe.ValidationError,
			)

		if not (1 <= cint(self.photo_quality) <= 100):
			frappe.throw(
				_("Photo quality must be between 1 and 100"),
				frappe.ValidationError,
			)

		for f in (
			"max_revisions",
			"shift_missed_grace_hours",
			"photo_max_edge",
			"thumbnail_max_edge",
			"photo_max_mb",
			"video_max_mb",
			"video_max_seconds",
		):
			if flt(getattr(self, f, 0)) <= 0:
				frappe.throw(_("{0} must be greater than zero").format(f), frappe.ValidationError)

		if flt(getattr(self, "media_retention_days", 0)) < 0:
			frappe.throw(_("media_retention_days cannot be negative"), frappe.ValidationError)
