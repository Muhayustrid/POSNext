# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""v2.8 adds po_receive_requires_delivery_note to the Purchase Order section —
sync_for appends it past barcode_tab again, so re-run the idempotent reindex
from v2.7 that re-seats DocField order to the doctype JSON."""

from pos_next.patches.v2_7_0.reorder_pos_settings_fields import execute
