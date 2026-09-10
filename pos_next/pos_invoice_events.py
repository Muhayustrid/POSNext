"""POS Invoice doc_events.

ERPNext's built-in POS also creates POS Invoices on this site, so every
pos_next-specific effect is gated on ownership (posa_pos_opening_shift set).
The stock realtime broadcast is deliberately NOT gated: POS Next terminals
must see built-in-POS sales too.
"""

from pos_next import realtime_events
from pos_next.api import packages, sales_invoice_hooks, wallet
from pos_next.invoice_type import is_pos_next_owned
from pos_next.overrides import discount_code, pos_offer_usage, queue_counter
from pos_next.overrides.pricing_rule import apply_min_max_price_discounts
from pos_next.shift_schedule import validate_invoice as validate_shift_schedule


def validate(doc, method=None):
	apply_min_max_price_discounts(doc, method)  # ungated: pricing applies to all
	if not is_pos_next_owned(doc):
		return
	sales_invoice_hooks.validate(doc, method)
	wallet.validate_wallet_payment(doc, method)
	packages.validate_invoice_packages(doc, method)
	discount_code.validate_invoice_discounts(doc, method)
	pos_offer_usage.validate_invoice_offers(doc, method)
	validate_shift_schedule(doc, method)


def before_cancel(doc, method=None):
	if not is_pos_next_owned(doc):
		return
	sales_invoice_hooks.before_cancel(doc, method)


def on_submit(doc, method=None):
	realtime_events.emit_stock_update_event(doc, method)  # ungated
	if not is_pos_next_owned(doc):
		return
	wallet.process_loyalty_to_wallet(doc, method)
	sales_invoice_hooks.record_one_time_offer_usage(doc, method)
	discount_code.record_code_usage_on_submit(doc, method)
	pos_offer_usage.record_offer_usage_on_submit(doc, method)
	queue_counter.bump_queue_counter(doc, method)


def on_cancel(doc, method=None):
	realtime_events.emit_stock_update_event(doc, method)  # ungated
	if not is_pos_next_owned(doc):
		return
	sales_invoice_hooks.release_one_time_offer_usage(doc, method)
	pos_offer_usage.release_offer_usage_on_cancel(doc, method)


def after_insert(doc, method=None):
	if not is_pos_next_owned(doc):
		return
	realtime_events.emit_invoice_created_event(doc, method)
