# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Re-seat DocField order after the settings layout overhaul (v2.17).

Both settings doctypes were regrouped into tabs (General, Sales, Display,
Printing, System, Purchasing & Production, Barcode) with new break fields;
sync_for appends those with the next free idx, so reindex to the JSON order
again. Same idempotent logic as v2_14_0.
"""

from pos_next.patches.v2_14_0.reorder_pos_settings_fields import execute  # noqa: F401
