"""Native SR metadata must hide stock and valuation without granting actions."""
import frappe
from frappe.tests import IntegrationTestCase

from pos_next import stock_opname_setup as setup
from pos_next.install import mirror_standard_perms_for_custom_doctypes


class TestStockOpnameDataPermissions(IntegrationTestCase):
    def setUp(self):
        super().setUp()
        frappe.set_user('Administrator')

    def tearDown(self):
        frappe.clear_cache(doctype='Stock Reconciliation')
        frappe.clear_cache(doctype='Stock Reconciliation Item')
        super().tearDown()

    def test_bin_valuation_is_private_but_quantities_and_native_roles_are_preserved(self):
        from pos_next.api.test_stock_opname import _make_user
        setup.ensure_stock_opname_data_permissions(quiet=True)
        mirror_standard_perms_for_custom_doctypes(quiet=True)
        meta = frappe.get_meta('Bin')
        for field in ('valuation_rate', 'stock_value'):
            self.assertEqual(meta.get_field(field).permlevel, 1)
        for field in ('actual_qty', 'reserved_qty', 'projected_qty', 'ordered_qty'):
            self.assertEqual(meta.get_field(field).permlevel, 0)
        for role in setup.OUTLET_ROLES:
            user = _make_user(role)
            permitted = meta.get_permitted_fieldnames(user=user)
            self.assertIn('actual_qty', permitted)
            self.assertNotIn('valuation_rate', permitted)
            self.assertNotIn('stock_value', permitted)
        readers = frappe.get_all('Custom DocPerm', filters={
            'parent': 'Bin', 'permlevel': 0, 'read': 1,
            'role': ['not in', setup.OUTLET_ROLES]}, fields=['role', 'if_owner'])
        self.assertTrue(readers)
        for row in readers:
            self.assertTrue(frappe.db.exists('Custom DocPerm', {
                'parent': 'Bin', 'role': row.role, 'if_owner': row.if_owner,
                'permlevel': 1, 'read': 1}))

    def test_existing_non_pos_level_one_row_keeps_valuation_read(self):
        setup.ensure_stock_opname_data_permissions(quiet=True)
        frappe.db.set_value('Custom DocPerm', {
            'parent': 'Bin', 'role': 'Sales User', 'permlevel': 1,
        }, 'read', 0)
        frappe.clear_cache(doctype='Bin')
        setup.ensure_stock_opname_data_permissions(quiet=True)
        row = frappe.db.get_value('Custom DocPerm', {
            'parent': 'Bin', 'role': 'Sales User', 'permlevel': 1,
        }, ['read', 'write'], as_dict=True)
        self.assertEqual((row.read, row.write), (1, 0))

    def test_bundle_valuation_is_private_including_entry_stock_queue(self):
        from pos_next.api.test_stock_opname import _make_user
        setup.ensure_stock_opname_data_permissions(quiet=True)
        mirror_standard_perms_for_custom_doctypes(quiet=True)
        parent = 'Serial and Batch Bundle'
        child = 'Serial and Batch Entry'
        for doctype, fields in ((parent, ('avg_rate', 'total_amount')),
                                (child, ('incoming_rate', 'outgoing_rate', 'stock_value_difference', 'stock_queue'))):
            for field in fields:
                self.assertEqual(frappe.get_meta(doctype).get_field(field).permlevel, 1)
        for doctype, fields in ((parent, ('total_qty', 'entries', 'item_code')),
                                (child, ('qty', 'serial_no', 'batch_no', 'delivered_qty'))):
            for field in fields:
                self.assertEqual(frappe.get_meta(doctype).get_field(field).permlevel, 0)
        for role in setup.OUTLET_ROLES:
            user = _make_user(role)
            fields = frappe.get_meta(child).get_permitted_fieldnames(parenttype=parent, user=user)
            self.assertIn('qty', fields)
            self.assertIn('serial_no', fields)
            self.assertNotIn('incoming_rate', fields)
            self.assertNotIn('outgoing_rate', fields)
            self.assertNotIn('stock_queue', fields)
        for row in frappe.get_all('Custom DocPerm', filters={
            'parent': parent, 'permlevel': 0, 'read': 1,
            'role': ['not in', setup.OUTLET_ROLES]}, fields=['role', 'if_owner', 'write']):
            permissions = frappe.db.get_value('Custom DocPerm', {
                'parent': parent, 'role': row.role, 'if_owner': row.if_owner,
                'permlevel': 1}, ['read', 'write'], as_dict=True)
            self.assertIsNotNone(permissions)
            self.assertEqual((permissions.read, permissions.write), (1, row.write))

    def test_sensitive_fields_are_level_one_and_counts_remain_public(self):
        setup.ensure_stock_opname_data_permissions(quiet=True)
        self.assertEqual(frappe.get_meta('Stock Reconciliation').get_field('difference_amount').permlevel, 1)
        meta = frappe.get_meta('Stock Reconciliation Item')
        for field in ('valuation_rate', 'amount', 'current_qty', 'current_valuation_rate',
                      'current_amount', 'quantity_difference', 'amount_difference',
                      'custom_qty_before', 'custom_qty_difference_per_uom',
                      'custom_valuation_rate_per_uom'):
            with self.subTest(field=field):
                self.assertEqual(meta.get_field(field).permlevel, 1)
        for field in ('custom_uom', 'custom_qty_after', 'custom_conversion_factor', 'qty'):
            self.assertEqual(meta.get_field(field).permlevel, 0)

    def test_native_serialization_omits_sensitive_fields_for_outlet(self):
        from pos_next.api.test_stock_opname import _make_user
        setup.ensure_stock_opname_data_permissions(quiet=True)
        user = _make_user('POSNext Cashier')
        doc = frappe.get_doc({
            'doctype': 'Stock Reconciliation', 'company': '_Test Company',
            'difference_amount': 8765,
            'items': [{'item_code': '_Test Item', 'qty': 24, 'valuation_rate': 321,
                       'current_qty': 98, 'amount': 7704, 'custom_qty_before': 49,
                       'custom_qty_after': 12, 'custom_uom': 'Pack',
                       'custom_qty_difference_per_uom': -37}],
        })
        frappe.set_user(user)
        try:
            doc.apply_fieldlevel_read_permissions()
            result = doc.as_dict()
            self.assertNotIn('difference_amount', result)
            row = result['items'][0]
            for field in ('valuation_rate', 'amount', 'current_qty', 'custom_qty_before',
                          'custom_qty_difference_per_uom'):
                self.assertNotIn(field, row)
            self.assertEqual(row['qty'], 24)
            self.assertEqual(row['custom_qty_after'], 12)
        finally:
            frappe.set_user('Administrator')

    def test_outlet_custom_print_is_denied_before_rendering(self):
        from pos_next.api.test_stock_opname import _make_user
        doc = frappe.get_doc({'doctype': 'Stock Reconciliation', 'company': '_Test Company'})
        frappe.set_user(_make_user('POSNext Cashier'))
        try:
            with self.assertRaises(frappe.PermissionError):
                doc.run_method('before_print', frappe._dict(compact_item_print=0))
        finally:
            frappe.set_user('Administrator')

    def test_new_native_document_cannot_inject_sensitive_values(self):
        from pos_next.api.test_stock_opname import _make_user
        doc = frappe.get_doc({'doctype': 'Stock Reconciliation', 'company': '_Test Company',
                              'items': [{'item_code': '_Test Item', 'qty': 1,
                                         'valuation_rate': 999, 'custom_valuation_rate_per_uom': 999}]})
        frappe.set_user(_make_user('POSNext Cashier'))
        try:
            with self.assertRaises(frappe.PermissionError):
                doc.run_method('before_validate')
        finally:
            frappe.set_user('Administrator')

    def test_existing_native_document_rejects_changed_rate_and_restores_omitted_rate(self):
        from pos_next.api.test_stock_opname import _make_user
        from unittest.mock import patch
        before = frappe.get_doc({'doctype': 'Stock Reconciliation', 'items': [
            {'name': 'rate-row', 'item_code': '_Test Item', 'valuation_rate': 123.456789,
             'custom_valuation_rate_per_uom': None}]})
        doc = frappe.get_doc({'doctype': 'Stock Reconciliation', 'items': [
            {'name': 'rate-row', 'item_code': '_Test Item', 'valuation_rate': 999}]})
        frappe.set_user(_make_user('POSNext Cashier'))
        try:
            with patch.object(doc, 'get_doc_before_save', return_value=before):
                with self.assertRaises(frappe.PermissionError):
                    doc.run_method('before_validate')
                doc.set('__islocal', 1)
                with self.assertRaises(frappe.PermissionError):
                    doc.validate_higher_perm_levels()
                doc.items[0].valuation_rate = 0
                doc.run_method('before_validate')
                self.assertEqual(doc.items[0].valuation_rate, 123.456789)
                self.assertIsNone(doc.items[0].custom_valuation_rate_per_uom)
        finally:
            frappe.set_user('Administrator')

    def test_http_response_filter_only_removes_sr_sensitive_fields(self):
        from werkzeug.wrappers import Request, Response
        from pos_next.api.test_stock_opname import _make_user
        from pos_next.overrides.stock_reconciliation_response import filter_stock_reconciliation_response
        response = Response(frappe.as_json({'data': {
            'doctype': 'Stock Reconciliation', 'difference_amount': 99,
            'items': [{'valuation_rate': 123, 'qty': 2}],
        }, 'other': {'doctype': 'Sales Invoice', 'amount': 777}}), mimetype='application/json')
        frappe.set_user(_make_user('POSNext Cashier'))
        try:
            request = Request.from_values(path='/api/v2/document/Stock Reconciliation', method='POST')
            filter_stock_reconciliation_response(response, request)
            rpc_response = Response(frappe.as_json({'message': {
                'doctype': 'Stock Reconciliation', 'difference_amount': 99,
                'items': [{'valuation_rate': 123, 'qty': 2}],
            }}), mimetype='application/json')
            for method in ('save', 'insert', 'submit', 'set_value', 'cancel', 'save/compat',
                           'frappe.model.workflow.apply_workflow'):
                for prefix, envelope in (('/api/method/', 'message'), ('/api/v1/method/', 'message'),
                                         ('/api/v2/method/', 'data')):
                    if '/' in method and prefix == '/api/v2/method/':
                        continue
                    rpc_response = Response(frappe.as_json({envelope: {
                        'doctype': 'Stock Reconciliation', 'difference_amount': 99,
                        'items': [{'valuation_rate': 123, 'qty': 2}],
                    }}), mimetype='application/json')
                    filter_stock_reconciliation_response(rpc_response, Request.from_values(
                        path=prefix + (method if method.startswith('frappe.') else 'frappe.client.' + method), method='POST'))
                    self.assertNotIn('difference_amount', rpc_response.get_json()[envelope])
                    self.assertNotIn('valuation_rate', rpc_response.get_json()[envelope]['items'][0])
            data = response.get_json()
            self.assertNotIn('difference_amount', data['data'])
            self.assertNotIn('valuation_rate', data['data']['items'][0])
            self.assertEqual(data['data']['items'][0]['qty'], 2)
            self.assertEqual(data['other']['amount'], 777)
        finally:
            frappe.set_user('Administrator')

    def test_response_boundary_does_not_scrub_unrelated_routes_or_nested_documents(self):
        from werkzeug.wrappers import Request, Response
        from pos_next.api.test_stock_opname import _make_user
        from pos_next.overrides.stock_reconciliation_response import filter_stock_reconciliation_response
        payload = {'data': {'doctype': 'Sales Invoice', 'amount': 777,
                            'nested': {'doctype': 'Stock Reconciliation', 'difference_amount': 99}}}
        frappe.set_user(_make_user('POSNext Cashier'))
        try:
            for path, method in (
                ('/api/method/unrelated.report', 'POST'),
                ('/api/resource/Sales Invoice', 'POST'),
                ('/api/v2/document/Stock Reconciliation', 'GET'),
                ('/api/v2/document/Stock Reconciliation', 'POST'),
            ):
                with self.subTest(path=path, method=method):
                    response = Response(frappe.as_json(payload), mimetype='application/json')
                    original = response.get_data()
                    filter_stock_reconciliation_response(
                        response, Request.from_values(path=path, method=method))
                    self.assertEqual(response.get_data(), original)
        finally:
            frappe.set_user('Administrator')

    def test_sensitive_write_response_fails_closed_when_role_lookup_fails(self):
        from unittest.mock import patch
        from werkzeug.wrappers import Request, Response
        from pos_next.overrides.stock_reconciliation_response import filter_stock_reconciliation_response
        response = Response(frappe.as_json({'data': {'doctype': 'Stock Reconciliation',
            'difference_amount': 987, 'items': [{'valuation_rate': 654}]}}), mimetype='application/json')
        with patch('pos_next.overrides.stock_reconciliation_response.is_hq_user', side_effect=RuntimeError('unavailable')):
            filter_stock_reconciliation_response(response, Request.from_values(
                path='/api/v2/document/Stock Reconciliation', method='POST'))
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('difference_amount', response.get_data(as_text=True))
        self.assertNotIn('valuation_rate', response.get_data(as_text=True))

    def test_system_serial_references_are_not_outlet_readable(self):
        setup.ensure_stock_opname_data_permissions(quiet=True)
        meta = frappe.get_meta('Stock Reconciliation Item')
        for field in ('current_serial_and_batch_bundle', 'current_serial_no'):
            self.assertEqual(meta.get_field(field).permlevel, 1)
        self.assertEqual(meta.get_field('serial_no').permlevel, 0)
        self.assertEqual(meta.get_field('serial_and_batch_bundle').permlevel, 0)

    def test_warehouse_field_reapply_preserves_pos_sensitive_levels_in_both_orders(self):
        from warehouse_app.upgrade import ensure_sr_fields
        for operations in (
            (ensure_sr_fields, setup.ensure_stock_opname_data_permissions),
            (setup.ensure_stock_opname_data_permissions, ensure_sr_fields),
        ):
            for operation in operations:
                operation()
            meta = frappe.get_meta('Stock Reconciliation Item')
            for field in ('custom_qty_before', 'custom_qty_difference_per_uom',
                          'custom_valuation_rate_per_uom', 'valuation_rate'):
                self.assertEqual(meta.get_field(field).permlevel, 1)
            for field in ('custom_uom', 'custom_qty_after', 'custom_conversion_factor'):
                self.assertEqual(meta.get_field(field).permlevel, 0)

    def test_repeat_setup_preserves_native_actions_and_restricts_outlet_flags(self):
        setup.ensure_stock_opname_data_permissions(quiet=True)
        count = frappe.db.count('Property Setter', {'doc_type': ['in', ['Stock Reconciliation', 'Stock Reconciliation Item']]})
        setup.ensure_stock_opname_data_permissions(quiet=True)
        self.assertEqual(frappe.db.count('Property Setter', {'doc_type': ['in', ['Stock Reconciliation', 'Stock Reconciliation Item']]}), count)
        mirror_standard_perms_for_custom_doctypes(quiet=True)
        for role in ('Stock Manager', 'Accounts Manager', 'System Manager'):
            row = frappe.db.get_value('Custom DocPerm', {'parent': 'Stock Reconciliation', 'role': role, 'permlevel': 1}, ['read', 'write'], as_dict=True)
            self.assertIsNotNone(row)
            self.assertEqual((row.read, row.write), (1, 1))
        self.assertTrue(frappe.db.exists('Custom DocPerm', {'parent': 'Stock Reconciliation', 'role': 'Stock Manager', 'permlevel': 0, 'read': 1}))
        for role in ('POSNext Cashier', 'POSNext Manager', 'Stock User'):
            self.assertFalse(frappe.db.exists('Custom DocPerm', {'parent': 'Stock Reconciliation', 'role': role, 'permlevel': 1, 'read': 1}))
        for role in ('POSNext Cashier', 'POSNext Manager'):
            row = frappe.db.get_value('Custom DocPerm', {'parent': 'Stock Reconciliation', 'role': role, 'permlevel': 0}, ['print', 'export', 'report'], as_dict=True)
            self.assertEqual((row['print'], row.export, row.report), (0, 0, 0))
