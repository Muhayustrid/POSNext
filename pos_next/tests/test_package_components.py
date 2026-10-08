import unittest

from pos_next.invoice_type import package_components


def _row(parent, code, qty, role="", instance=None):
	return {
		"parent": parent,
		"item_code": code,
		"item_name": code,
		"qty": qty,
		"pos_package_role": role,
		"pos_package_instance": instance,
	}


class TestPackageComponents(unittest.TestCase):
	def test_components_summed_per_package_and_return_split(self):
		rows = [
			_row("A", "PAKET", 1, "Package", "p1"),
			_row("A", "ROTI", 1, "Package Item", "p1"),
			_row("A", "TEH", 2, "Package Item", "p1"),
			_row("B", "PAKET", 1, "Package", "p1"),  # same instance id, other invoice
			_row("B", "TEH", 1, "Package Item", "p1"),
			_row("R", "PAKET", -1, "Package", "p9"),
			_row("R", "TEH", -2, "Package Item", "p9"),
			_row("A", "TEH", 5, "Package Item", "orphan"),  # no package line
		]
		out = package_components(rows)
		sold = {c["item_code"]: c["qty"] for c in out[("PAKET", False)]}
		self.assertEqual(sold, {"ROTI": 1, "TEH": 3})
		self.assertEqual([c["qty"] for c in out[("PAKET", True)]], [-2])
		self.assertEqual(set(out), {("PAKET", False), ("PAKET", True)})
