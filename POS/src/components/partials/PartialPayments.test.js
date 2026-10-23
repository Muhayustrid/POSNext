/**
 * @vitest-environment jsdom
 */
import { describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { defineComponent } from "vue"

const callMock = vi.hoisted(() => vi.fn())

vi.mock("frappe-ui", async () => {
	const { defineComponent, reactive } = await import("vue")
	const Button = defineComponent({
		name: "Button",
		emits: ["click"],
		template: `<button v-bind="$attrs" @click="$emit('click')"><slot /></button>`,
	})
	return {
		Button,
		call: callMock,
		createResource: () => reactive({ loading: false, data: null, error: null, reload: vi.fn(), fetch: vi.fn() }),
	}
})

vi.mock("@/utils/currency", () => ({
	DEFAULT_CURRENCY: "IDR",
	formatCurrency: (amount, currency) => `${amount} ${currency}`,
}))

vi.mock("@/utils/invoice", () => ({
	getInvoiceStatusColor: () => "bg-gray-100 text-gray-800",
}))

vi.mock("@/composables/useToast", () => ({
	useToast: () => ({ showSuccess: vi.fn(), showError: vi.fn() }),
}))

vi.mock("@/composables/useFormatters", () => ({
	useFormatters: () => ({
		formatDate: (d) => `D:${d}`,
		formatTime: (t) => `T:${t}`,
	}),
}))

vi.mock("@/composables/useShiftSchedule", () => ({
	scheduleBlockingNow: () => false,
}))

globalThis.__ = (message, replacements = []) => {
	if (!Array.isArray(replacements) || !replacements.length) return message
	let out = message
	for (const [i, v] of replacements.entries())
		out = out.split(`{${i}}`).join(String(v))
	return out
}

import PartialPayments from "./PartialPayments.vue"

describe("PartialPayments shift badge and filter", () => {
	function mountComponent() {
		const pinia = createPinia()
		setActivePinia(pinia)
		return mount(PartialPayments, {
			props: {
				modelValue: true,
				posProfile: "Outlet 1",
			},
			global: {
				plugins: [pinia],
				config: { globalProperties: { __: globalThis.__ } },
				stubs: { PaymentDialog: true },
			},
		})
	}

	it("renders shift badge on partial payment invoices and filters with chips", async () => {
		callMock.mockImplementation(async (method) => {
			if (
				method === "pos_next.api.partial_payments.get_partial_paid_invoices"
			) {
				return [
					{
						name: "ACC-SINV-001",
						customer_name: "Customer A",
						grand_total: 50000,
						paid_amount: 20000,
						outstanding_amount: 30000,
						posting_date: "2026-10-23",
						posting_time: "10:00:00",
						status: "Partly Paid",
						shift_slot: "SHIFT 1",
					},
					{
						name: "ACC-SINV-002",
						customer_name: "Customer B",
						grand_total: 80000,
						paid_amount: 40000,
						outstanding_amount: 40000,
						posting_date: "2026-10-23",
						posting_time: "14:00:00",
						status: "Partly Paid",
						shift_slot: "SHIFT 2",
					},
				]
			}
			if (
				method === "pos_next.api.partial_payments.get_partial_payment_summary"
			) {
				return { count: 2, total_outstanding: 70000, total_paid: 60000 }
			}
			return []
		})

		const wrapper = mountComponent()
		await flushPromises()

		const badges = wrapper.findAll('[data-test="shift-badge"]')
		expect(badges.length).toBe(2)
		expect(badges[0].text()).toBe("SHIFT 1")
		expect(badges[1].text()).toBe("SHIFT 2")
		expect(badges[0].classes()).toContain("rounded-full")
		expect(badges[0].classes()).toContain("ring-1")
		expect(badges[0].classes()).toContain("ring-inset")

		const filterChips = wrapper.findAll('[data-test="shift-filter"] button')
		expect(filterChips.length).toBe(3) // All shifts + SHIFT 1 + SHIFT 2

		// Click SHIFT 1
		const shift1Btn = filterChips.find((b) => b.text().includes("SHIFT 1"))
		await shift1Btn.trigger("click")
		await flushPromises()

		const visibleCards = wrapper.findAll('[data-test="shift-badge"]')
		expect(visibleCards.length).toBe(1)
		expect(wrapper.text()).toContain("ACC-SINV-001")
		expect(wrapper.text()).not.toContain("ACC-SINV-002")

		// Click All shifts
		await filterChips[0].trigger("click")
		await flushPromises()
		expect(wrapper.findAll('[data-test="shift-badge"]').length).toBe(2)
	})
})
