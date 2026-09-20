/**
 * @vitest-environment jsdom
 */
import { mount } from "@vue/test-utils"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { nextTick } from "vue"

vi.mock("frappe-ui", async () => {
	const { defineComponent } = await import("vue")
	const stub = defineComponent({ name: "FeatherIconStub", render: () => null })
	const Button = defineComponent({
		name: "Button",
		template: `<button v-bind="$attrs" @click="$emit('click')"><slot /></button>`,
	})
	return { FeatherIcon: stub, Button, call: () => Promise.resolve(null) }
})

// The app installs __() as a global property; some modules call it at
// module-evaluation time, so this must be hoisted above the imports.
vi.hoisted(() => {
	globalThis.__ = (message, replacements = []) => {
		if (!Array.isArray(replacements) || !replacements.length) return message
		let out = message
		for (const [i, v] of replacements.entries())
			out = out.split(`{${i}}`).join(String(v))
		return out
	}
})

import POSMenuDialog from "./POSMenuDialog.vue"
import { MANAGEMENT_MENU } from "./managementMenu"

const viewStubs = {
	PromotionManagement: { template: `<div data-testid="view-promotions" />` },
	POSSettings: { template: `<div data-testid="view-settings" />` },
	InvoiceManagement: { template: `<div data-testid="view-invoices" />` },
	SessionSummary: { template: `<div data-testid="view-sales-recap" />` },
	ShiftDashboard: { template: `<div data-testid="view-dashboard" />` },
	WarehouseAvailabilityDialog: { template: `<div data-testid="view-products" />` },
	ProductionDialog: { template: `<div data-testid="view-production" />` },
	PurchaseOrderDialog: { template: `<div data-testid="view-purchase-order" />` },
}

function mountShell(props = {}) {
	return mount(POSMenuDialog, {
		props: {
			open: false,
			isOffline: false,
			...props,
		},
		global: {
			config: { globalProperties: { __: globalThis.__ } },
			stubs: viewStubs,
		},
	})
}

function sidebarLabels(wrapper) {
	return wrapper
		.find('[data-testid="pos-menu-sidebar"]')
		.findAll("button")
		.map((b) => b.text())
}

function availableLabels(canProduction, canPurchaseOrder, isOffline = false) {
	return MANAGEMENT_MENU.filter(
		(i) =>
			(!i.requiresProduction || (canProduction && !isOffline)) &&
			(!i.requiresPurchaseOrder || (canPurchaseOrder && !isOffline)) &&
			(!i.requiresOnline || !isOffline),
	).map((i) => i.label)
}

beforeEach(() => {
	document.body.innerHTML = ""
})

describe("POSMenuDialog menu", () => {
	it("renders the same filtered items as MANAGEMENT_MENU rules", () => {
		const wrapper = mountShell({
			open: true,
			canProduction: true,
			canPurchaseOrder: true,
		})
		expect(sidebarLabels(wrapper)).toEqual(availableLabels(true, true))
		wrapper.unmount()
	})

	it("hides production when the user lacks the permission", () => {
		const wrapper = mountShell({ open: true, canProduction: false })
		const labels = sidebarLabels(wrapper)
		expect(labels).not.toContain("Production")
		expect(labels).toEqual(availableLabels(false, false))
		wrapper.unmount()
	})

	it("hides purchase order when the user lacks the permission", () => {
		const wrapper = mountShell({ open: true, canPurchaseOrder: false })
		const labels = sidebarLabels(wrapper)
		expect(labels).not.toContain("Purchase Order")
		wrapper.unmount()
	})

	it("hides both gated items while offline even with permission", () => {
		const wrapper = mountShell({
			open: true,
			canProduction: true,
			canPurchaseOrder: true,
			isOffline: true,
		})
		const labels = sidebarLabels(wrapper)
		expect(labels).not.toContain("Production")
		expect(labels).not.toContain("Purchase Order")
		expect(labels).toEqual(availableLabels(true, true, true))
		wrapper.unmount()
	})

	it("shows the dashboard online without an open shift", () => {
		const wrapper = mountShell({ open: true })
		const labels = sidebarLabels(wrapper)
		expect(labels).toContain("Dashboard")
		expect(labels).toEqual(availableLabels(false, false))
		// the burger landing still prefers Invoice Management
		expect(wrapper.find('[data-testid="view-invoices"]').exists()).toBe(true)
		wrapper.unmount()
	})

	it("hides the dashboard while offline, with or without an open shift", () => {
		const wrapper = mountShell({
			open: true,
			openingShift: "OS-1",
			isOffline: true,
		})
		expect(sidebarLabels(wrapper)).not.toContain("Dashboard")
		expect(sidebarLabels(wrapper)).toEqual(availableLabels(false, false, true))
		wrapper.unmount()
	})

	it("lists the dashboard first with an open shift but lands on invoices", async () => {
		const wrapper = mountShell({ open: true, openingShift: "OS-1" })
		const labels = sidebarLabels(wrapper)
		expect(labels[0]).toBe("Dashboard")
		expect(labels).toEqual(availableLabels(false, false))
		// sidebar order keeps Dashboard first, the burger landing stays Invoice Management
		expect(wrapper.find('[data-testid="view-invoices"]').exists()).toBe(true)
		expect(wrapper.find('[data-testid="view-dashboard"]').exists()).toBe(false)

		const sidebar = wrapper.find('[data-testid="pos-menu-sidebar"]')
		const buttons = sidebar.findAll("button")
		await buttons.find((b) => b.text() === "Sales Recap").trigger("click")
		await nextTick()
		expect(wrapper.find('[data-testid="view-sales-recap"]').exists()).toBe(true)
		expect(wrapper.find('[data-testid="view-invoices"]').exists()).toBe(false)

		await sidebar
			.findAll("button")
			.find((b) => b.text() === "Dashboard")
			.trigger("click")
		await nextTick()
		expect(wrapper.find('[data-testid="view-dashboard"]').exists()).toBe(true)
		wrapper.unmount()
	})

	it("mounts the dashboard when initialView is explicitly dashboard", async () => {
		const wrapper = mountShell({
			open: true,
			openingShift: "OS-1",
			initialView: "dashboard",
		})
		expect(wrapper.find('[data-testid="view-dashboard"]').exists()).toBe(true)
		wrapper.unmount()
	})
})

describe("POSMenuDialog views", () => {
	it("opens on the first available item by default", async () => {
		const wrapper = mountShell({
			open: true,
			canProduction: false,
			canPurchaseOrder: false,
		})
		// First item of the Sales group is Invoice Management
		expect(wrapper.find('[data-testid="view-invoices"]').exists()).toBe(true)
		wrapper.unmount()
	})

	it("switches the mounted view and emits menu-selected on item click", async () => {
		const wrapper = mountShell({ open: true })
		const sidebar = wrapper.find('[data-testid="pos-menu-sidebar"]')
		const settingsBtn = sidebar
			.findAll("button")
			.find((b) => b.text() === "Settings")
		await settingsBtn.trigger("click")
		await nextTick()

		expect(wrapper.find('[data-testid="view-settings"]').exists()).toBe(true)
		expect(wrapper.find('[data-testid="view-promotions"]').exists()).toBe(false)
		expect(wrapper.emitted("menu-selected")).toEqual([["settings"]])
		wrapper.unmount()
	})

	it("selects initialView on open and stays one-shot per open", async () => {
		const wrapper = mountShell({ open: false, initialView: "settings" })
		expect(wrapper.find('[data-testid="view-settings"]').exists()).toBe(false)

		await wrapper.setProps({ open: true })
		expect(wrapper.find('[data-testid="view-settings"]').exists()).toBe(true)

		// One-shot: closing and reopening without initialView falls back to default
		await wrapper.setProps({ open: false, initialView: null })
		await wrapper.setProps({ open: true })
		expect(wrapper.find('[data-testid="view-settings"]').exists()).toBe(false)
		expect(wrapper.find('[data-testid="view-invoices"]').exists()).toBe(true)
		wrapper.unmount()
	})

	it("falls back to the first available item when initialView is unavailable", async () => {
		const wrapper = mountShell({
			open: false,
			initialView: "production",
			canProduction: false,
		})
		await wrapper.setProps({ open: true })
		expect(wrapper.find('[data-testid="view-production"]').exists()).toBe(false)
		expect(wrapper.find('[data-testid="view-invoices"]').exists()).toBe(true)
		wrapper.unmount()
	})

	it("forwards listeners from $attrs onto the active view", async () => {
		const onRefreshHistory = vi.fn()
		const wrapper = mount(
			{
				components: { POSMenuDialog },
				template: `<POSMenuDialog open :history-invoices="[]" @refresh-history="onRefreshHistory" />`,
				setup() {
					return { onRefreshHistory }
				},
			},
			{
				global: {
					config: { globalProperties: { __: globalThis.__ } },
					stubs: {
						...viewStubs,
						InvoiceManagement: {
							emits: ["refresh-history"],
							template: `<button data-testid="emit-refresh" @click="$emit('refresh-history')" />`,
						},
					},
				},
			},
		)
		// invoices is the third item; select it so InvoiceManagement mounts
		const sidebar = wrapper.find('[data-testid="pos-menu-sidebar"]')
		const invoicesBtn = sidebar
			.findAll("button")
			.find((b) => b.text() === "Invoice Management")
		await invoicesBtn.trigger("click")

		await wrapper.find('[data-testid="emit-refresh"]').trigger("click")
		expect(onRefreshHistory).toHaveBeenCalledTimes(1)
		wrapper.unmount()
	})

	it("closes the shell when an embedded dialog view closes itself", async () => {
		const onClose = vi.fn()
		const wrapper = mount(
			{
				components: { POSMenuDialog },
				template: `<POSMenuDialog open initial-view="purchase-order" :can-purchase-order="true" @update:open="onClose" />`,
				setup() {
					return { onClose }
				},
			},
			{
				global: {
					config: { globalProperties: { __: globalThis.__ } },
					stubs: {
						...viewStubs,
						PurchaseOrderDialog: {
							emits: ["update:modelValue"],
							template: `<button data-testid="emit-close" @click="$emit('update:modelValue', false)" />`,
						},
					},
				},
			},
		)
		expect(wrapper.find('[data-testid="emit-close"]').exists()).toBe(true)
		await wrapper.find('[data-testid="emit-close"]').trigger("click")
		expect(onClose).toHaveBeenCalledWith(false)
		wrapper.unmount()
	})
})

describe("POSMenuDialog chrome", () => {
	it("is a modal dialog with aria semantics", () => {
		const wrapper = mountShell({ open: true })
		const panel = wrapper.find('[role="dialog"]')
		expect(panel.exists()).toBe(true)
		expect(panel.attributes("aria-modal")).toBe("true")
		expect(panel.attributes("aria-label")).toBe("Invoice Management")
		wrapper.unmount()
	})

	it("emits update:open false from the header close button", async () => {
		const wrapper = mountShell({ open: true })
		await wrapper.find('button[aria-label="Close menu"]').trigger("click")
		expect(wrapper.emitted("update:open")).toEqual([[false]])
		wrapper.unmount()
	})
})
