# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import os

import frappe
from frappe import _
from frappe.utils import cint, flt

DISALLOWED_BRANDS = (b"heic", b"heix", b"mif1", b"msf1", b"qt  ")


def sniff_media(head: bytes) -> str | None:
	"""Identify media type ('jpeg', 'mp4', 'webm') or None from file header."""
	if not head:
		return None

	# JPEG magic bytes: FF D8 FF
	if head.startswith(b"\xff\xd8\xff"):
		return "jpeg"

	# WebM magic bytes: 1A 45 DF A3
	if head.startswith(b"\x1a\x45\xdf\xa3"):
		return "webm"

	# MP4: offset 4:8 == 'ftyp'
	if len(head) >= 12 and head[4:8] == b"ftyp":
		brand = head[8:12]
		if brand not in DISALLOWED_BRANDS:
			return "mp4"

	return None


def validate_media_file(file_doc, report_name: str, expected: str, settings) -> str:
	"""Validate that file matches expected media type and size constraints."""
	if file_doc.attached_to_doctype != "POS Monitoring Report" or file_doc.attached_to_name != report_name:
		frappe.throw(_("File is not attached to this report"), frappe.ValidationError)

	if not file_doc.is_private:
		frappe.throw(_("Media file must be private"), frappe.ValidationError)

	# Read header bytes efficiently and binary-safe without decoding
	head = b""
	try:
		if hasattr(file_doc, "get_full_path"):
			full_path = file_doc.get_full_path()
			if full_path and os.path.exists(full_path):
				with open(full_path, "rb") as fp:
					head = fp.read(16)
		if not head and hasattr(file_doc, "get_content"):
			content = file_doc.get_content(encodings=[])
			if isinstance(content, (bytes, bytearray)):
				head = bytes(content[:16])
			elif isinstance(content, str):
				head = content.encode("latin1", errors="ignore")[:16]
	except Exception:
		pass

	detected = sniff_media(head)
	if detected == "jpeg":
		media_type = "Photo"
	elif detected == "mp4":
		media_type = "Video"
	elif detected == "webm":
		if not getattr(settings, "allow_webm", False):
			frappe.throw(_("WebM video format is not allowed"), frappe.ValidationError)
		media_type = "Video"
	else:
		frappe.throw(_("Unsupported file format"), frappe.ValidationError)

	if expected == "Photo" and media_type != "Photo":
		frappe.throw(_("Expected photo but received video"), frappe.ValidationError)
	elif expected == "Video" and media_type != "Video":
		frappe.throw(_("Expected video but received photo"), frappe.ValidationError)

	# File size checks
	size_bytes = file_doc.file_size or len(content or b"")
	size_mb = size_bytes / (1024 * 1024)
	if media_type == "Photo" and size_mb > flt(settings.photo_max_mb):
		frappe.throw(
			_("Photo size ({0:.2f} MB) exceeds maximum allowed ({1} MB)").format(size_mb, settings.photo_max_mb),
			frappe.ValidationError,
		)
	elif media_type == "Video" and size_mb > flt(settings.video_max_mb):
		frappe.throw(
			_("Video size ({0:.2f} MB) exceeds maximum allowed ({1} MB)").format(size_mb, settings.video_max_mb),
			frappe.ValidationError,
		)

	return media_type


INTERNAL_MONITORING_DELETE_FLAG = "internal_monitoring_delete"


def is_monitoring_evidence_file(file_name: str, stored: dict | None = None) -> tuple[bool, dict | None]:
	"""Check if file is monitoring evidence based on stored File row and Media table references."""
	if not stored:
		stored = frappe.db.get_value(
			"File",
			file_name,
			["name", "attached_to_doctype", "attached_to_name", "is_private", "file_url"],
			as_dict=True,
		)
	if not stored:
		return False, None

	if stored.attached_to_doctype == "POS Monitoring Report":
		return True, stored

	# Evidence is always private, so public files cannot be monitoring media;
	# skip the Media lookups for them instead of scanning on every File save.
	if not cint(stored.is_private):
		return False, stored

	is_evidence = bool(
		frappe.db.exists("POS Monitoring Report Media", {"file_url": stored.file_url})
		or frappe.db.exists("POS Monitoring Report Media", {"thumbnail_url": stored.file_url})
	)
	return is_evidence, stored


def _internal_monitoring_delete() -> bool:
	return bool(
		getattr(frappe.flags, INTERNAL_MONITORING_DELETE_FLAG, False)
		or getattr(frappe.flags, "in_uninstall", False)
	)


def validate_file_lifecycle(doc):
	"""Guard File updates: monitoring evidence cannot change attachment linkage or privacy.

	Called from the File controller override, before File.validate() relocates the
	blob, and on new inserts it is a no-op because there is no stored row yet.
	Administrator/System Manager bypass is NOT allowed for detach/de-privatize of
	registered evidence unless via internal feature paths.
	"""
	if doc.is_new() or _internal_monitoring_delete():
		return

	is_evidence, stored = is_monitoring_evidence_file(doc.name)
	if not is_evidence or not stored:
		return

	if (
		doc.attached_to_doctype != stored.attached_to_doctype
		or doc.attached_to_name != stored.attached_to_name
		or cint(doc.is_private) != cint(stored.is_private)
		or doc.file_url != stored.file_url
	):
		frappe.throw(
			_("Cannot modify attachment linkage or privacy of monitoring evidence"),
			frappe.ValidationError,
		)


def validate_file_trash(doc):
	"""Guard File deletion: evidence can only be deleted via internal Draft paths.

	Called from the File controller override, before File.on_trash() unlinks the blob.
	"""
	if _internal_monitoring_delete():
		return

	is_evidence, stored = is_monitoring_evidence_file(doc.name)
	if not is_evidence or not stored:
		return

	# If referenced by a media row, it cannot be deleted directly (must use remove_media)
	is_referenced_by_media = (
		frappe.db.exists("POS Monitoring Report Media", {"file_url": stored.file_url})
		or frappe.db.exists("POS Monitoring Report Media", {"thumbnail_url": stored.file_url})
	)
	if is_referenced_by_media:
		frappe.throw(
			_("Cannot delete file referenced by monitoring report media"),
			frappe.ValidationError,
		)

	# If attached to POS Monitoring Report, allow delete only if report is Draft and user has write permission
	report_name = stored.attached_to_name
	if report_name and frappe.db.exists("POS Monitoring Report", report_name):
		rep = frappe.get_doc("POS Monitoring Report", report_name)
		if rep.docstatus != 0 or rep.status != "Draft":
			frappe.throw(
				_("Cannot delete evidence file of non-draft monitoring report"),
				frappe.ValidationError,
			)
		if not frappe.has_permission("POS Monitoring Report", doc=rep, ptype="write"):
			frappe.throw(
				_("No permission to delete evidence file"),
				frappe.PermissionError,
			)
