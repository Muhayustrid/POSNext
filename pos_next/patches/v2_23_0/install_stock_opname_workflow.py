# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Install the POS Next Stock Opname workflow on already-migrated sites.

after_migrate also ensures it, but running it as a patch records it in the
Patch Log for existing sites. Idempotent.
"""

from pos_next.stock_opname_setup import ensure_stock_opname_workflow


def execute():
	ensure_stock_opname_workflow(quiet=True)
