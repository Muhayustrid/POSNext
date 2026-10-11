# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, today

from pos_next.api.test_reject_request import RejectRequestTest, _as_user


class TestPOSRejectReport(RejectRequestTest):
	def setUp(self):
		super().setUp()
		from pos_next.pos_next.report.pos_reject_report.pos_reject_report import execute

		self.execute = execute
		self.crew1 = self._user(["POSNext Cashier"], prefix="rrp-u1")
		self.crew2 = self._user(["POSNext Cashier"], prefix="rrp-u2")
		self.approver = self._user(["POSNext Reject Approver"], prefix="rrp-appr")

	def _decided(self, decision, liabilities=None):
		doc = self._submitted(1)
		if decision == "Approved":
			self._decide(doc, "Approved")
		else:
			self._decide(doc.reload(), decision, "crew liable", liabilities)
		return doc

	def _decide(self, doc, decision, notes=None, liabilities=None):
		with _as_user(self.approver):
			return reject_request_apply(doc, decision, notes, liabilities)

	def _liability_rows(self, filters=None):
		filters = {"from_date": "2020-01-01", "to_date": "2050-01-01", "view": "Per Liability", **(filters or {})}
		columns, data = self.execute(filters)[:2]
		return columns, data

	def test_liability_rows_sum_to_totals(self):
		approved = self._decided("Approved")
		total = flt(frappe.db.get_value("POS Reject Request", approved.name, "total_amount"))
		liab = self._decided("Rejected", [{"user": self.crew1, "amount": total * 0.6}, {"user": self.crew2, "amount": total * 0.4}])
		filters = {"purpose": "Unsold", "decision": ""}
		columns, data = self._liability_rows(filters)
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
		total = 50.0
		doc = self._submitted(5)  # rate 10 → total 50
		self._decide(doc.reload(), "Rejected", "split", [{"user": self.crew1, "amount": 20}, {"user": self.crew2, "amount": 30}])
		columns, data = self._liability_rows({"user": self.crew2})
		self.assertEqual(len(data), 1)
		self.assertEqual(data[0].bearer, self.crew2)
		self.assertEqual(flt(data[0].amount), 30)

	def test_per_request_liable_crew_column(self):
		doc = self._submitted(2)
		total = flt(doc.total_amount)
		self._decide(doc.reload(), "Rejected", "split", [{"user": self.crew1, "amount": total / 2}, {"user": self.crew2, "amount": total / 2}])
		filters = {"view": "Per Request", "from_date": "2020-01-01", "to_date": "2050-01-01"}
		data = self.execute(filters)[1]
		row = next(r for r in data if r.name == doc.name)
		self.assertEqual(row.liable_crew, f"{self.crew1}, {self.crew2}")


def reject_request_apply(doc, decision, notes=None, liabilities=None):
	from pos_next.services import reject_request

	return reject_request.apply_decision(doc, decision, notes, liabilities, system=False)
