"""System-stock access must require existing HQ and native document scope."""
import uuid
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from pos_next.api.test_stock_opname import _make_user
from pos_next.overrides import stock_reconciliation as access


class TestSystemStockAccess(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user('Administrator')
        cls.cashier = _make_user('POSNext Cashier')
        cls.manager = _make_user('Stock Manager')
        cls.hq_without_company_read = _make_user('Stock Manager')
        frappe.get_doc('User', cls.manager).add_roles('Stock User')
        cls.outlet_stock_user = _make_user('POSNext Manager')
        frappe.get_doc('User', cls.outlet_stock_user).add_roles('Stock User')
        cls.warehouse = frappe.get_doc({
            'doctype': 'Warehouse', 'warehouse_name': 'Access-' + uuid.uuid4().hex[:8],
            'company': '_Test Company',
        }).insert().name

    def tearDown(self):
        frappe.set_user('Administrator')
        super().tearDown()

    def test_system_access_role_matrix(self):
        for user in ('Guest', 'missing-sr-user@example.invalid', self.cashier, self.outlet_stock_user):
            with self.subTest(user=user), self.assertRaises(frappe.PermissionError):
                access.assert_system_stock_access(user=user)
        access.assert_system_stock_access(user=self.manager)
        access.assert_system_stock_access(user='Administrator')

    def test_contextual_access_accepts_hq_with_native_scope(self):
        access.assert_system_stock_access(self.warehouse, '_Test Company', user=self.manager)

    def test_hq_membership_does_not_grant_native_company_read(self):
        with self.assertRaises(frappe.PermissionError):
            access.assert_system_stock_access(self.warehouse, user=self.hq_without_company_read)

    def test_outlet_cannot_access_document_without_parent_warehouse(self):
        doc = frappe.get_doc({'doctype': 'Stock Reconciliation', 'items': [
            {'warehouse': self.warehouse, 'qty': 2}]})
        self.assertFalse(access.has_permission(doc, user=self.cashier))
        self.assertTrue(access.has_permission(doc, user=self.manager))

    def test_company_only_context_rejected(self):
        with self.assertRaises(frappe.PermissionError):
            access.assert_system_stock_access(company='_Test Company', user=self.manager)

    def test_mismatched_or_unknown_warehouse_rejected(self):
        for warehouse, company in ((self.warehouse, '_Test Company 1'), ('missing-warehouse', None)):
            with self.subTest(warehouse=warehouse), self.assertRaises(frappe.PermissionError):
                access.assert_system_stock_access(warehouse, company, user=self.manager)

    def test_native_stock_balance_override_hides_only_valuation(self):
        path = 'erpnext.stock.utils.get_stock_balance'
        target = frappe.override_whitelisted_method(path)
        self.assertNotEqual(target, path)
        delegate_path = 'erpnext.stock.utils.get_stock_balance'
        for user in (self.cashier, self.outlet_stock_user, self.manager):
            frappe.set_user(user)
            for serial in (False, True):
                native = (7, 123, 'SER-1\nSER-2') if serial else (7, 123)
                with patch(delegate_path, return_value=native) as delegate:
                    result = frappe.get_attr(target)(
                        'item', self.warehouse, '2026-11-03', '00:00:00', True, serial)
                    expected = native if user == self.manager else ((7, None, 'SER-1\nSER-2') if serial else (7, None))
                    self.assertEqual(result, expected)
                    delegate.assert_called_once_with('item', self.warehouse, '2026-11-03',
                                                     '00:00:00', True, serial, None)
            with patch(delegate_path, return_value=7):
                self.assertEqual(frappe.get_attr(target)('item', self.warehouse), 7)
        frappe.set_user(self.cashier)
        with patch(delegate_path, side_effect=frappe.PermissionError):
            with self.assertRaises(frappe.PermissionError):
                frappe.get_attr(target)('item', self.warehouse)

    def test_get_next_rejects_inaccessible_sr_sort_before_native_lookup(self):
        path = 'frappe.desk.form.utils.get_next'
        target = frappe.override_whitelisted_method(path)
        self.assertNotEqual(target, path)
        frappe.set_user(self.cashier)
        with patch(path, side_effect=AssertionError('unpermitted sort reached native')):
            for field in ('difference_amount', 'items.valuation_rate', 'unknown_sort_field'):
                with self.subTest(field=field), self.assertRaises(frappe.PermissionError):
                    frappe.get_attr(target)('Stock Reconciliation', 'missing', 0, sort_field=field)
        for user, doctype, field in ((self.cashier, 'Stock Reconciliation', 'creation'),
                                     (self.cashier, 'Stock Reconciliation', 'posting_date'),
                                     (self.manager, 'Stock Reconciliation', 'difference_amount'),
                                     (self.cashier, 'Item', 'valuation_rate')):
            frappe.set_user(user)
            with patch(path, return_value='next-name') as delegate:
                self.assertEqual(frappe.get_attr(target)(doctype, 'current', 1,
                    {'name': 'next-name'}, 'asc', field), 'next-name')
                delegate.assert_called_once_with(doctype, 'current', 1, {'name': 'next-name'}, 'asc', field)

    def test_rpc_aliases_route_by_function_identity_without_global_hook_mutation(self):
        from werkzeug.test import EnvironBuilder
        from werkzeug.wrappers import Request
        aliases = {
            'pos_next.tests.test_pos_reserved_qty.get_stock_balance': 'get_stock_balance',
            'warehouse_app.warehouse_app.stock_reconciliation.native_get_items': 'get_items',
            'frappe.desk.form.test_form.get_next': 'get_next',
            'erpnext.stock.utils.get_incoming_rate': 'get_incoming_rate',
        }
        before = dict(frappe.get_hooks('override_whitelisted_methods'))
        for prefix in ('/api/method/', '/api/v1/method/', '/api/v2/method/'):
            for alias, wrapper in aliases.items():
                request = Request(EnvironBuilder(path=prefix + alias).get_environ())
                with self.subTest(prefix=prefix, alias=alias), patch.object(frappe.local, 'request', request, create=True):
                    access.route_stock_valuation_rpc()
                    self.assertEqual(request.environ['PATH_INFO'], prefix + 'pos_next.overrides.stock_reconciliation.' + wrapper)
        self.assertEqual(dict(frappe.get_hooks('override_whitelisted_methods')), before)

    def test_rpc_router_matches_native_suffix_doctype_and_legacy_cmd_dispatch(self):
        from werkzeug.test import EnvironBuilder
        from werkzeug.wrappers import Request
        alias = 'pos_next.tests.test_pos_reserved_qty.get_stock_balance'
        cases = (
            ('/api/method/' + alias + '/compat', {}, '/api/method/pos_next.overrides.stock_reconciliation.get_stock_balance'),
            ('/api/v2/method/Stock Entry/get_warehouse_details', {}, '/api/v2/method/pos_next.overrides.stock_reconciliation.get_warehouse_details'),
            ('/', {'cmd': alias}, '/'),
            ('/api/method/ping', {'cmd': alias}, '/api/method/ping'),
            ('/api/method/ping', {}, '/api/method/ping'),
        )
        for path, form, expected in cases:
            request = Request(EnvironBuilder(path=path).get_environ())
            with self.subTest(path=path), patch.object(frappe.local, 'request', request, create=True), \
                 patch.object(frappe.local, 'form_dict', frappe._dict(form)):
                access.route_stock_valuation_rpc()
                self.assertEqual(request.environ['PATH_INFO'], expected)
                if form:
                    self.assertEqual(frappe.form_dict.cmd, 'pos_next.overrides.stock_reconciliation.get_stock_balance')

    def test_valuation_only_rpcs_deny_pure_outlet_but_keep_native_mixed_role_access(self):
        for wrapper in ('get_incoming_rate', 'get_valuation_rate', 'get_stock_value_by_item_group'):
            for user in (self.cashier, self.outlet_stock_user, self.manager):
                frappe.set_user(user)
                native = {
                    'get_incoming_rate': 'erpnext.stock.utils.get_incoming_rate',
                    'get_valuation_rate': 'erpnext.stock.get_item_details.get_valuation_rate',
                    'get_stock_value_by_item_group': 'erpnext.stock.dashboard_chart_source.stock_value_by_item_group.stock_value_by_item_group.get',
                }[wrapper]
                with self.subTest(wrapper=wrapper, user=user), patch(native, return_value=37) as delegate:
                    if user == self.cashier:
                        with self.assertRaises(frappe.PermissionError):
                            getattr(access, wrapper)()
                        delegate.assert_not_called()
                    else:
                        self.assertEqual(getattr(access, wrapper)(), 37)

    def test_mixed_quantity_endpoints_keep_qty_without_valuation(self):
        cases = (
            ('get_item_dashboard_data', 'erpnext.stock.dashboard.item_dashboard.get_data', [{'actual_qty': 9, 'valuation_rate': 37}]),
            ('get_quick_stock_balance', 'erpnext.stock.doctype.quick_stock_balance.quick_stock_balance.get_stock_item_details', {'qty': 9, 'value': 333}),
            ('get_warehouse_details', 'erpnext.stock.doctype.stock_entry.stock_entry.get_warehouse_details', {'actual_qty': 9, 'basic_rate': 37}),
        )
        for wrapper, native, result in cases:
            for user in (self.cashier, self.outlet_stock_user, self.manager):
                frappe.set_user(user)
                with self.subTest(wrapper=wrapper, user=user), patch(native, autospec=True, return_value=result):
                    params = {'get_item_dashboard_data': {}, 'get_quick_stock_balance': {'warehouse': self.warehouse, 'date': '2026-11-03'}, 'get_warehouse_details': {'args': {}}}[wrapper]
                    actual = getattr(access, wrapper)(cmd='rpc-route', **params)
                    rows = actual if isinstance(actual, list) else [actual]
                    self.assertEqual(rows[0].get('qty', rows[0].get('actual_qty')), 9)
                    if user == self.cashier:
                        self.assertFalse({'valuation_rate', 'value', 'basic_rate'} & rows[0].keys())
                    else:
                        self.assertEqual(actual, result)

    def test_native_original_dotted_path_denies_outlet(self):
        frappe.set_user(self.cashier)
        for method, args in (
            ('get_stock_balance_for', {'item_code': 'missing-item', 'warehouse': self.warehouse,
             'posting_date': '2026-11-03', 'posting_time': '00:00:00'}),
            ('get_items', {'warehouse': self.warehouse, 'posting_date': '2026-11-03',
             'posting_time': '00:00:00', 'company': '_Test Company'}),
        ):
            path = 'erpnext.stock.doctype.stock_reconciliation.stock_reconciliation.' + method
            target = frappe.override_whitelisted_method(path)
            self.assertNotEqual(target, path)
            with self.assertRaises(frappe.PermissionError):
                frappe.get_attr(target)(**args)

    def test_role_only_precheck_does_not_read_stock(self):
        with patch('erpnext.stock.utils.get_stock_balance', side_effect=AssertionError('stock read')):
            access.assert_system_stock_access(user=self.manager)

    def test_get_items_override_preserves_warehouse_enrichment(self):
        frappe.set_user(self.manager)
        with patch('warehouse_app.warehouse_app.stock_reconciliation.get_items',
                   return_value=[{'custom_uom': 'Pack', 'custom_qty_before': 2}]) as delegate:
            self.assertEqual(access.get_items(self.warehouse, '2026-11-03', '00:00:00',
                                             '_Test Company'),
                             [{'custom_uom': 'Pack', 'custom_qty_before': 2}])
            delegate.assert_called_once_with(self.warehouse, '2026-11-03', '00:00:00',
                                             '_Test Company', None, False)

    def test_native_get_items_without_warehouse_app_is_guarded(self):
        frappe.set_user(self.cashier)
        with patch('frappe.get_installed_apps', return_value=['frappe', 'erpnext', 'pos_next']), \
             patch('erpnext.stock.doctype.stock_reconciliation.stock_reconciliation.get_items',
                   side_effect=AssertionError('unauthorized stock read')):
            with self.assertRaises(frappe.PermissionError):
                access.get_items(self.warehouse, '2026-11-03', '00:00:00', '_Test Company')

    def test_user_permission_restricts_hq_warehouse(self):
        frappe.get_doc({
            'doctype': 'User Permission', 'user': self.manager,
            'allow': 'Company', 'for_value': '_Test Company 1',
            'apply_to_all_doctypes': 1,
        }).insert(ignore_permissions=True)
        frappe.clear_cache(user=self.manager)
        with self.assertRaises(frappe.PermissionError):
            access.assert_system_stock_access(self.warehouse, user=self.manager)

    def test_public_stock_routes_reject_empty_warehouse(self):
        frappe.set_user(self.manager)
        with self.assertRaises(frappe.PermissionError):
            access.get_stock_balance_for('missing-item', '', '2026-11-03', '00:00:00')
        with self.assertRaises(frappe.PermissionError):
            access.get_items('', '2026-11-03', '00:00:00', '_Test Company')
