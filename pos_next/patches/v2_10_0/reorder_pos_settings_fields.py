# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""v2.10 adds monthly_target_basis and overall_target_basis next to
invoice_type - sync_for appends them past barcode_tab again, so re-run the
idempotent reindex from v2.7 that re-seats DocField order to the doctype
JSON."""

from pos_next.patches.v2_7_0.reorder_pos_settings_fields import execute
