// Single source for the management menu — consumed by the application shell
// (POSMenuDialog.vue, which hosts every management module as an embedded view)
// so all surfaces always show the same items under the same permission rules.
// Items are grouped and ordered by workflow: selling, stock & supply,
// marketing, then system.
import { __ } from "@/utils/translation"

export const MENU_GROUPS = [
	{ id: "sales", label: __("Sales") },
	{ id: "stock", label: __("Stock & Supply") },
	{ id: "marketing", label: __("Marketing") },
	{ id: "system", label: __("System") },
]

export const MANAGEMENT_MENU = [
	{
		id: "invoices",
		icon: "file-text",
		label: __("Invoice Management"),
		activeClass: "bg-indigo-100 text-indigo-600",
		group: "sales",
	},
	{
		id: "sales-recap",
		icon: "clipboard",
		label: __("Sales Recap"),
		activeClass: "bg-teal-100 text-teal-600",
		group: "sales",
	},
	{
		id: "products",
		icon: "package",
		label: __("Products"),
		activeClass: "bg-purple-100 text-purple-600",
		group: "stock",
	},
	{
		id: "purchase-order",
		icon: "truck",
		label: __("Purchase Order"),
		activeClass: "bg-cyan-100 text-cyan-600",
		group: "stock",
		requiresPurchaseOrder: true,
	},
	{
		id: "purchase-receipt",
		icon: "archive",
		label: __("Purchase Receipt"),
		activeClass: "bg-blue-100 text-blue-600",
		group: "stock",
		requiresPurchaseOrder: true,
	},
	{
		id: "production",
		icon: "tool",
		label: __("Production"),
		activeClass: "bg-amber-100 text-amber-600",
		group: "stock",
		requiresProduction: true,
	},
	{
		id: "promotions",
		icon: "tag",
		label: __("Promotions"),
		activeClass: "bg-green-100 text-green-600",
		group: "marketing",
	},
	{
		id: "settings",
		icon: "settings",
		label: __("Settings"),
		activeClass: "bg-gray-100 text-gray-900",
		group: "system",
	},
]
