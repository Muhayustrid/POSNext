// Single source for the management menu — consumed by the desktop icon rail
// (ManagementSlider.vue) and the mobile/tablet drawer (ManagementDrawer.vue)
// so both always show the same items under the same permission rules.
export const MANAGEMENT_MENU = [
	{
		id: "promotions",
		icon: "tag",
		label: "Promotions",
		activeClass: "bg-green-100 text-green-600",
	},
	{
		id: "products",
		icon: "package",
		label: "Products",
		activeClass: "bg-purple-100 text-purple-600",
	},
	{
		id: "invoices",
		icon: "file-text",
		label: "Invoice Management",
		activeClass: "bg-indigo-100 text-indigo-600",
	},
	{
		id: "production",
		icon: "tool",
		label: "Production",
		activeClass: "bg-amber-100 text-amber-600",
		requiresProduction: true,
	},
	{
		id: "settings",
		icon: "settings",
		label: "Settings",
		activeClass: "bg-gray-100 text-gray-900",
		divider: true,
	},
]
