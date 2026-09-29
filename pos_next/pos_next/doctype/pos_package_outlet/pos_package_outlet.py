# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class POSPackageOutlet(Document):
	"""Outlet scope of a POS Package: Company + Warehouse.

	An outlet applies to every POS Profile sharing that pair; ``pos_profile``
	is a read-only listing of those profiles, never the scoping key.
	"""

	pass
