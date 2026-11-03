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
