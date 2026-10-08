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
		id: "dashboard",
		icon: "bar-chart-2",
		label: __("Dashboard"),
		group: "sales",
		requiresOnline: true,
	},
	{
		id: "invoices",
		icon: "file-text",
		label: __("Invoice Management"),
		group: "sales",
	},
	{
		id: "sales-recap",
		icon: "clipboard",
		label: __("Sales Recap"),
		group: "sales",
	},
	{
		id: "products",
		icon: "package",
		label: __("Products"),
		group: "stock",
	},
	{
		id: "purchase-order",
		icon: "truck",
		label: __("Purchase Order"),
		group: "stock",
		requiresPurchaseOrder: true,
	},
	{
		id: "purchase-receipt",
		icon: "archive",
		label: __("Purchase Receipt"),
		group: "stock",
		requiresPurchaseOrder: true,
	},
	{
		id: "production",
		icon: "tool",
		label: __("Production"),
		group: "stock",
		requiresProduction: true,
	},
	{
		id: "promotions",
		icon: "tag",
		label: __("Promotions"),
		group: "marketing",
	},
	{
		id: "settings",
		icon: "settings",
		label: __("Settings"),
		group: "system",
	},
]
