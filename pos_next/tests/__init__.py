"""Ported selling_additional test suite (OpenSpec task 1.3).

These modules port the corresponding ``selling_additional`` tests onto the
``pos_next`` module paths, DocTypes, and Custom Fields. Behavioural assertions
are preserved verbatim; only imports, fixture wiring, field names, and the
POS Invoice -> Sales Invoice retarget change (design decisions R1/D3/D9).
"""
