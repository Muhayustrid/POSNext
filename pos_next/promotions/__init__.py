"""Dynamic Promotion domain package for pos_next.

CRITICAL INVARIANT: ZERO @frappe.whitelist() decorators anywhere in this package.
All HTTP surface lives in pos_next/overrides/pos_promo_api.py (AST-enforced contract).
"""
