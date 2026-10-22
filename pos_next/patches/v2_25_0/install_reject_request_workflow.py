# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Install the POS Next Reject Request workflow and migrate legacy states."""

from pos_next.reject_request_setup import ensure_reject_request_workflow, migrate_legacy_states


def execute():
	ensure_reject_request_workflow(quiet=True)
	migrate_legacy_states()
