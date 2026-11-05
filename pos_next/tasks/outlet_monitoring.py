# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

from pos_next.services.outlet_monitoring import (
	ensure_obligations,
	get_settings,
	mark_missed,
	purge_expired_media,
)


def run_cron():
	"""Periodic cron task: ensure obligation drafts and mark expired drafts as Missed."""
	if not get_settings().enabled:
		return
	ensure_obligations()
	mark_missed()


def run_daily():
	"""Daily maintenance task: purge media past retention window."""
	if not get_settings().enabled:
		return
	purge_expired_media()
