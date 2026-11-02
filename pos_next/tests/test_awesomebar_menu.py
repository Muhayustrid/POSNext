import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pos_next import awesomebar_menu


class TestAwesomebarMenu(unittest.TestCase):
	def setUp(self):
		self.session = patch.object(awesomebar_menu.frappe, "session", SimpleNamespace(user="test@example.com"))
		self.session.start()
		self.addCleanup(self.session.stop)

	def test_searches_sidebar_alias_and_opens_doctype(self):
		sidebar = SimpleNamespace(items=[{"type": "Link", "label": "Shift Group", "link_type": "DocType", "link_to": "POS Profile Group"}])
		with patch.object(awesomebar_menu, "resolve_sidebar", return_value=sidebar), patch.object(
			awesomebar_menu, "doctype_landing_route", return_value="/desk/pos-profile-group"
		):
			results = awesomebar_menu.search("shift group")
		self.assertEqual(results[0]["label"], "Shift Group")
		self.assertEqual(results[0]["route"], "/desk/pos-profile-group")

	def test_routes_page_report_and_pos(self):
		items = [
			{"type": "Link", "label": "Start POS", "link_type": "URL", "url": "/pos/"},
			{"type": "Link", "label": "Outlet Targets", "link_type": "Page", "link_to": "outlet-targets"},
			{"type": "Link", "label": "Reject Report", "link_type": "Report", "link_to": "POS Reject Report", "report": {"report_type": "Script Report"}},
		]
		with patch.object(awesomebar_menu, "resolve_sidebar", return_value=SimpleNamespace(items=items)):
			self.assertEqual(awesomebar_menu.search("start pos")[0]["route"], "/pos/")
			self.assertEqual(awesomebar_menu.search("outlet targets")[0]["route"], "/desk/outlet-targets")
			self.assertEqual(awesomebar_menu.search("reject report")[0]["route"], "/desk/query-report/POS Reject Report")

	def test_only_returns_visible_matching_links(self):
		sidebar = SimpleNamespace(items=[{"type": "Link", "label": "Shift Group", "link_type": "DocType", "link_to": "POS Profile Group"}])
		with patch.object(awesomebar_menu, "resolve_sidebar", return_value=sidebar):
			self.assertEqual(awesomebar_menu.search("discount"), [])
		with patch.object(awesomebar_menu, "resolve_sidebar", return_value=None):
			self.assertEqual(awesomebar_menu.search("shift"), [])
