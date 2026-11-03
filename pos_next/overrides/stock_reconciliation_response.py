"""Protect direct SR REST write responses that bypass native field filtering."""
import json

import frappe
from werkzeug.exceptions import HTTPException

from pos_next.overrides.stock_reconciliation import is_hq_user


SENSITIVE_ITEM_FIELDS = (
    'valuation_rate', 'amount', 'current_qty', 'current_valuation_rate',
    'current_amount', 'quantity_difference', 'amount_difference',
    'custom_qty_before', 'custom_qty_difference_per_uom', 'custom_valuation_rate_per_uom',
    'current_serial_and_batch_bundle', 'current_serial_no',
)


def filter_stock_reconciliation_response(response, request=None):
    from frappe.api import API_URL_MAP, v1, v2

    request = request if request is not None else frappe.request
    if request is None or not response.is_json:
        return
    try:
        endpoint, arguments = API_URL_MAP.bind_to_environ(request.environ).match()
    except HTTPException:
        return
    envelope = 'data'
    method = arguments.get('method', '')
    if endpoint == v1.handle_rpc_call:
        method = method.split('/')[0]
    if endpoint in (v1.handle_rpc_call, v2.handle_rpc_call) and method in (
        'frappe.client.save', 'frappe.client.insert', 'frappe.client.submit',
        'frappe.client.set_value', 'frappe.client.cancel',
        'frappe.model.workflow.apply_workflow',
    ):
        if request.method not in ('POST', 'PUT'):
            return
        envelope = 'message' if endpoint == v1.handle_rpc_call else 'data'
    elif arguments.get('doctype') != 'Stock Reconciliation' or endpoint not in (
        v1.create_doc, v1.update_doc, v2.create_doc, v2.update_doc,
    ):
        return
    data = response.get_json(silent=True)
    doc = data.get(envelope) if isinstance(data, dict) else None
    if not isinstance(doc, dict) or doc.get('doctype') != 'Stock Reconciliation':
        return
    try:
        if is_hq_user():
            return
        doc.pop('difference_amount', None)
        for row in doc.get('items') or []:
            if isinstance(row, dict):
                for field in SENSITIVE_ITEM_FIELDS:
                    row.pop(field, None)
        response.set_data(json.dumps(data))
    except Exception:
        # Frappe swallows after_request errors; never leave the original SR body.
        response.status_code = 500
        response.set_data('{"error":"Stock reconciliation response unavailable"}')
