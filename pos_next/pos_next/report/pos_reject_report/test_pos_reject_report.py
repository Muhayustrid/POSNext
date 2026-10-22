# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import flt

from pos_next.api.test_reject_request import RejectRequestTest, _as_user


class TestPOSRejectReport(RejectRequestTest):
	def setUp(self):
		super().setUp()
		from pos_next.pos_next.report.pos_reject_report.pos_reject_report import execute

		self.execute = execute
		self.crew1 = self._user(["POSNext Cashier"], prefix="rrp-u1")
		self.crew2 = self._user(["POSNext Cashier"], prefix="rrp-u2")
		self.approver = self._user(["POSNext Reject Approver"], prefix="rrp-appr")

	def _decided(self, borne_by, liabilities=None):
		doc = self._submitted(1)
		with _as_user(self.approver):
			from frappe.model.workflow import apply_workflow

			from pos_next.services import reject_request

			reject_request.set_liability(doc, borne_by, liabilities)
			return apply_workflow(doc, "Approve")

	def _liability_rows(self, filters=None):
		filters = {"from_date": "2020-01-01", "to_date": "2050-01-01", "view": "Per Liability", **(filters or {})}
		columns, data = self.execute(filters)[:2]
		return columns, data

	def test_liability_rows_sum_to_totals(self):
		approved = self._decided("Company")
		total = flt(frappe.db.get_value("POS Reject Request", approved.name, "total_amount"))
		self._decided("Crew", [{"user": self.crew1, "amount": total * 0.6}, {"user": self.crew2, "amount": total * 0.4}])
		filters = {"purpose": "Unsold", "borne_by": ""}
		_columns, data = self._liability_rows(filters)
		bearers = {r.bearer: r.amount for r in data}
		self.assertEqual(flt(bearers.get("Company")), total)
		self.assertEqual(flt(bearers.get(self.crew1)), total * 0.6)
		self.assertEqual(flt(bearers.get(self.crew2)), total * 0.4)
		summary = self.execute(filters)[4]
		company = sum(s["value"] for s in summary if s["label"] == "Borne by Company")
		crew = sum(s["value"] for s in summary if s["label"] == "Borne by Crew")
		self.assertEqual(company, total)
		self.assertEqual(crew, total)
		# two requests, not two rows per request
		self.assertEqual(next(s["value"] for s in summary if s["label"] == "Requests"), 2)

	def test_user_filter_per_liability(self):
		doc = self._submitted(5)  # rate 10 → total 50
		with _as_user(self.approver):
			from frappe.model.workflow import apply_workflow

			from pos_next.services import reject_request

			reject_request.set_liability(doc, "Crew", [{"user": self.crew1, "amount": 20}, {"user": self.crew2, "amount": 30}])
			apply_workflow(doc, "Approve")
		_columns, data = self._liability_rows({"user": self.crew2})
		self.assertEqual(len(data), 1)
		self.assertEqual(data[0].bearer, self.crew2)
		self.assertEqual(flt(data[0].amount), 30)

	def test_per_request_liable_crew_column(self):
		doc = self._submitted(2)
		total = flt(doc.total_amount)
		with _as_user(self.approver):
			from frappe.model.workflow import apply_workflow

			from pos_next.services import reject_request

			reject_request.set_liability(doc, "Crew", [{"user": self.crew1, "amount": total / 2}, {"user": self.crew2, "amount": total / 2}])
			apply_workflow(doc, "Approve")
		filters = {"view": "Per Request", "from_date": "2020-01-01", "to_date": "2050-01-01"}
		cols, data = self.execute(filters)[:2]
		row = next(r for r in data if r.name == doc.name)
		self.assertEqual(row.liable_crew, f"{self.crew1}, {self.crew2}")
		self.assertEqual(row.workflow_state, "Awaiting Destruction")
		self.assertEqual(row.borne_by, "Crew")
		col_fields = [c["fieldname"] for c in cols]
		self.assertIn("workflow_state", col_fields)
		self.assertIn("borne_by", col_fields)
		self.assertNotIn("status", col_fields)
		self.assertNotIn("decision", col_fields)

	def test_only_approved_workflow_states_in_report(self):
		draft = self._doc(1)
		pending = self._submitted(1)
		approved = self._decided("Company")
		filters = {"from_date": "2020-01-01", "to_date": "2050-01-01", "view": "Per Request"}
		data = self.execute(filters)[1]
		names = {r.name for r in data}
		self.assertNotIn(draft.name, names)
		self.assertNotIn(pending.name, names)
		self.assertIn(approved.name, names)
