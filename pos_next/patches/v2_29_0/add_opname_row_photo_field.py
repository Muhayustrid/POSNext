# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Add the pos_opname_photo Attach Image field to Stock Reconciliation Item."""

from pos_next.install import sync_custom_fields


def execute():
	sync_custom_fields(quiet=True)
