// Single source for the management menu — consumed by the application shell
// (POSMenuDialog.vue, which hosts every management module as an embedded view)
// so all surfaces always show the same items under the same permission rules.
// Items are grouped and ordered by workflow: selling, stock & supply,
// marketing, then system.
export const MENU_GROUPS = [
	{ id: "sales", label: "Sales" },
	{ id: "stock", label: "Stock & Supply" },
	{ id: "marketing", label: "Marketing" },
	{ id: "system", label: "System" },
]

export const MANAGEMENT_MENU = [
	{
		id: "invoices",
		icon: "file-text",
		label: "Invoice Management",
		activeClass: "bg-indigo-100 text-indigo-600",
		group: "sales",
	},
	{
		id: "sales-recap",
		icon: "clipboard",
		label: "Sales Recap",
		activeClass: "bg-teal-100 text-teal-600",
		group: "sales",
	},
	{
		id: "products",
		icon: "package",
		label: "Products",
		activeClass: "bg-purple-100 text-purple-600",
		group: "stock",
	},
	{
		id: "purchase-order",
		icon: "truck",
		label: "Purchase Order",
		activeClass: "bg-cyan-100 text-cyan-600",
		group: "stock",
		requiresPurchaseOrder: true,
	},
	{
		id: "production",
		icon: "tool",
		label: "Production",
		activeClass: "bg-amber-100 text-amber-600",
		group: "stock",
		requiresProduction: true,
	},
	{
		id: "promotions",
		icon: "tag",
		label: "Promotions",
		activeClass: "bg-green-100 text-green-600",
		group: "marketing",
	},
	{
		id: "settings",
		icon: "settings",
		label: "Settings",
		activeClass: "bg-gray-100 text-gray-900",
		group: "system",
	},
]
