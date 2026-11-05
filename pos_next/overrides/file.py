# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""File override: run the monitoring evidence guards before File touches the disk.

Frappe's own File.validate() moves the blob between the public and private
folders when is_private changes, and File.on_trash() unlinks the blob, both
before any doc_events handler runs. Rejecting in doc_events would leave the
database row intact but the file already moved or deleted, so the guard has to
wrap the controller methods instead.
"""

from frappe.core.doctype.file.file import File

from pos_next.services.outlet_monitoring_media import (
	validate_file_lifecycle,
	validate_file_trash,
)


class CustomFile(File):
	def validate(self):
		validate_file_lifecycle(self)
		super().validate()

	def on_trash(self):
		validate_file_trash(self)
		super().on_trash()
