/**
 * @vitest-environment jsdom
 */
import { describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"
import { defineComponent, reactive } from "vue"

const resources = []

vi.mock("frappe-ui", async () => {
	const { defineComponent, reactive } = await import("vue")
	const Button = defineComponent({
		name: "Button",
		emits: ["click"],
		template: `<button v-bind="$attrs" @click="$emit('click')"><slot /></button>`,
	})
	const Dialog = defineComponent({
		name: "Dialog",
		props: {
			modelValue: { type: Boolean, default: false },
			options: { type: Object, default: () => ({}) },
		},
		template: `<div><slot name="body" /><slot name="actions" /></div>`,
	})
	const FeatherIcon = defineComponent({
		name: "FeatherIcon",
		props: { name: { type: String, required: true } },
		template: `<i :data-icon="name" />`,
	})
	const stub = defineComponent({ name: "FrappeUIStub", render: () => null })
	return {
		Button,
		Dialog,
		FeatherIcon,
		Input: stub,
		call: vi.fn(),
		createResource: (opts) => {
			const reload = vi.fn()
			const r = reactive({
				loading: false,
				data: null,
				error: null,
				reload,
			})
			resources.push({ opts, resource: r })
			return r
		},
	}
})

vi.mock("@/composables/useFormatters", () => ({
	useFormatters: () => ({
		formatDate: (d) => `D:${d}`,
		formatTime: (t) => `T:${t}`,
		formatDateTime: (d, t) => `DT:${d} ${t}`,
	}),
}))

vi.mock("@/utils/currency", () => ({
	DEFAULT_CURRENCY: "IDR",
	formatCurrency: (amount, currency) => `${amount} ${currency}`,
}))

vi.mock("@/utils/invoice", () => ({
	getInvoiceStatusColor: () => "bg-gray-100 text-gray-800",
}))

vi.mock("@/utils/queue/queueNumber", () => ({
	formatQueueNumber: (n) => String(n),
}))

vi.mock("@/composables/useToast", () => ({
	useToast: () => ({ showSuccess: vi.fn(), showError: vi.fn() }),
}))

vi.mock("@/components/common/PosDialogShell.vue", () => ({
	default: defineComponent({
		name: "PosDialogShell",
		props: ["modelValue", "title", "subtitle", "icon"],
		template: `<div><slot name="toolbar" /><slot /></div>`,
	}),
}))

globalThis.__ = (message, replacements = []) => {
	if (!Array.isArray(replacements) || !replacements.length) return message
	let out = message
	for (const [i, v] of replacements.entries())
		out = out.split(`{${i}}`).join(String(v))
	return out
}

import InvoiceHistoryDialog from "./InvoiceHistoryDialog.vue"

describe("InvoiceHistoryDialog shift badge and filter", () => {
	function mountDialog() {
		resources.length = 0
		return mount(InvoiceHistoryDialog, {
			props: {
				modelValue: true,
				posProfile: "Outlet 1",
				posOpeningShift: "POS-OPEN-001",
			},
			global: {
				config: { globalProperties: { __: globalThis.__ } },
				stubs: { ReturnInvoiceDialog: true, InvoiceDetailDialog: true },
			},
		})
	}

	it("renders shift badge on rows and filters with shift filter chips", async () => {
		const wrapper = mountDialog()
		await flushPromises()

		const testInvoices = [
			{
				name: "INV-001",
				customer_name: "Customer A",
				grand_total: 10000,
				posting_date: "2026-10-23",
				posting_time: "10:00:00",
				status: "Paid",
				shift_slot: "SHIFT 1",
			},
			{
				name: "INV-002",
				customer_name: "Customer B",
				grand_total: 20000,
				posting_date: "2026-10-23",
				posting_time: "14:00:00",
				status: "Paid",
				shift_slot: "SHIFT 2",
			},
			{
				name: "INV-003",
				customer_name: "Customer C",
				grand_total: 30000,
				posting_date: "2026-10-23",
				posting_time: "16:00:00",
				status: "Paid",
				pos_profile: "Legacy Outlet",
			},
		]

		// Simulate resource success
		resources[0].opts.onSuccess(testInvoices)
		await flushPromises()

		// Badges rendered
		const badges = wrapper.findAll('[data-test="shift-badge"]')
		expect(badges.length).toBe(3)
		expect(badges[0].text()).toBe("SHIFT 1")
		expect(badges[1].text()).toBe("SHIFT 2")
		expect(badges[2].text()).toBe("Legacy Outlet")

		// Pill style check
		expect(badges[0].classes()).toContain("rounded-full")
		expect(badges[0].classes()).toContain("ring-1")
		expect(badges[0].classes()).toContain("ring-inset")

		// Filter chips
		const filterChips = wrapper.findAll('[data-test="shift-filter"] button')
		expect(filterChips.length).toBe(4) // All shifts + Legacy Outlet + SHIFT 1 + SHIFT 2
		expect(filterChips[0].text()).toContain("All shifts")

		// Filter by SHIFT 1
		const shift1Btn = filterChips.find((b) => b.text().includes("SHIFT 1"))
		await shift1Btn.trigger("click")
		await flushPromises()

		let visibleCards = wrapper.findAll('[data-test="shift-badge"]')
		expect(visibleCards.length).toBe(1)
		expect(wrapper.text()).toContain("INV-001")
		expect(wrapper.text()).not.toContain("INV-002")

		// Filter by All shifts
		await filterChips[0].trigger("click")
		await flushPromises()

		visibleCards = wrapper.findAll('[data-test="shift-badge"]')
		expect(visibleCards.length).toBe(3)
	})
})
