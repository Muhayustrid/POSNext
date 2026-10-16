# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Add the pos_offline_price_mismatch Check to Sales Invoice / POS Invoice.

Custom fields ship via install.py's CUSTOM_FIELDS (upserted on migrate), but
sites already installed need the field created by a versioned patch too.
"""

from pos_next.install import sync_custom_fields


def execute():
	sync_custom_fields(quiet=True)
