"""POSNext app tile landing: HQ roles -> HQ dashboard, cashiers -> POS.

Run via pos_next/_pn_run_tests.py pos_next.test_posnext_home
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from pos_next.www.posnext_home import get_context


class TestPosnextHome(FrappeTestCase):
    def tearDown(self):
        frappe.set_user("Administrator")

    def _landing(self):
        try:
            get_context(frappe._dict())
        except frappe.Redirect:
            return frappe.local.flags.redirect_location

    def test_hq_role_lands_on_hq_dashboard(self):
        self.assertEqual(self._landing(), "/desk/hq-sales-monitoring")

    def test_cashier_lands_on_pos(self):
        email = "home-cashier@example.com"
        if not frappe.db.exists("User", email):
            frappe.get_doc(
                {"doctype": "User", "email": email, "first_name": "Home", "roles": [{"role": "POSNext Cashier"}]}
            ).insert(ignore_permissions=True)
        frappe.set_user(email)
        # cashiers have no Desk (utils/desk_guard)
        self.assertEqual(self._landing(), "/pos")
