/**
 * @vitest-environment jsdom
 *
 * The Sales Recap dialog must expose a labelled Print button in its fixed
 * footer. The recap itself lives in SessionSummary, whose own print control is
 * an icon inside the scroll region — easy to miss and scrolled out of sight,
 * which is exactly how the button came to be reported as missing.
 */
import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"

// The app installs __() as a global property; templates need it, and it must
// exist before the component module is evaluated.
vi.hoisted(() => {
	globalThis.__ = (message, replacements = []) => {
		if (!Array.isArray(replacements) || !replacements.length) return message
		let out = message
		for (const [i, v] of replacements.entries())
			out = out.split(`{${i}}`).join(String(v))
		return out
	}
})

const resources = vi.hoisted(() => ({ instances: [] }))

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
		template: `<div><slot name="body" /></div>`,
	})
	return {
		Button,
		Dialog,
		createResource: (opts) => {
			const reload = vi.fn(() => {
				r.loading = true
			})
			const r = reactive({ loading: false, data: null, error: null, reload })
			resources.instances.push({ url: opts.url, opts, resource: r })
			return r
		},
	}
})

vi.mock("@/composables/useFormatters", () => ({
	useFormatters: () => ({
		formatDate: (d) => `D:${d}`,
		formatTime: (t) => `T:${t}`,
		formatDateTime: (d) => `DT:${d}`,
	}),
}))

vi.mock("@/utils/currency", () => ({
	DEFAULT_CURRENCY: "IDR",
	formatCurrency: (amount, currency) => `${amount} ${currency}`,
}))

vi.mock("reka-ui", () => ({
	DialogTitle: { name: "DialogTitle", template: "<h2><slot /></h2>" },
}))

const printMock = vi.hoisted(() => ({ printSalesRecap: vi.fn() }))
vi.mock("@/utils/salesRecap", async (importOriginal) => ({
	...(await importOriginal()),
	printSalesRecap: printMock.printSalesRecap,
}))

import SalesRecapDialog from "./SalesRecapDialog.vue"

// Shape mirrors the real get_session_summary payload the component renders.
const SUMMARY = {
	opening_shift: "POSA-OS-26-0000007",
	pos_profile: "POS - PKU DELANGGU",
	shift_name: null,
	cashier: "Administrator",
	company: "PT JUARA ROTI",
	company_currency: "IDR",
	cash_mode_of_payment: "Cash",
	period_start_date: "2026-09-18 09:32:26",
	generated_at: "2026-09-18 14:00:00",
	status: "Open",
	closing_time: null,
	closing_source: null,
	counted_invoices: 2,
	invoice_count: 2,
	sales_count: 2,
	returns_count: 0,
	gross_sales: 30000,
	returns_total: 0,
	net_sales: 30000,
	net_total: 30000,
	total_qty: 3,
	average_sale: 15000,
	credit_outstanding: 0,
	currency_breakdown: [{ currency: "IDR", amount: 30000 }],
	payments: [
		{ mode_of_payment: "Cash PKU DELANGGU", amount: 30000, is_cash: true, configured: true },
	],
	opening_cash: 100000,
	cash_collected: 30000,
	cash_in_hand: 130000,
	cash_expected: 130000,
	expense: null,
	expense_supported: false,
	total_cash: 30000,
	total_non_cash: 0,
	methods_grand_total: 30000,
	items: [{ item_code: "RP001", item_name: "Ropi Butter", qty: 3, base_net_amount: 30000 }],
	packages: [],
	items_shown: 1,
	items_total_groups: 1,
	items_truncated: false,
	tax_total: 0,
	total_tax: 0,
	other_charges: [],
	other_charges_total: 0,
	categories: [{ category: "Roti", qty: 3, base_net_amount: 30000 }],
	categories_shown: 1,
	categories_total_groups: 1,
	categories_truncated: false,
	item_discount: 0,
	total_discount: 0,
}

function findResource(urlPart) {
	return resources.instances.find((i) => i.url.includes(urlPart))
}

async function mountOpen() {
	const wrapper = mount(SalesRecapDialog, {
		props: { modelValue: true, posProfile: "POS - PKU DELANGGU", openingShift: "POSA-OS-26-0000007" },
		attachTo: document.body,
		global: { config: { globalProperties: { __: globalThis.__ } } },
	})
	await flushPromises()
	return wrapper
}

describe("SalesRecapDialog print control", () => {
	beforeEach(() => {
		resources.instances.length = 0
		printMock.printSalesRecap.mockReset().mockResolvedValue(undefined)
	})

	it("shows a labelled Print button in the fixed footer", async () => {
		const wrapper = await mountOpen()
		const footer = wrapper.find('[data-test="dialog-footer"]')
		expect(footer.exists()).toBe(true)
		expect(footer.text()).toContain("Print")
		expect(footer.text()).toContain("Close")
	})

	it("prints the loaded recap through the shared EOD-style sheet", async () => {
		const wrapper = await mountOpen()
		const session = findResource("get_session_summary")
		session.resource.data = SUMMARY
		session.resource.loading = false
		await flushPromises()

		const printButton = wrapper
			.findAll("button")
			.find((b) => b.text().includes("Print"))
		await printButton.trigger("click")
		await flushPromises()

		expect(printMock.printSalesRecap).toHaveBeenCalledTimes(1)
		expect(printMock.printSalesRecap).toHaveBeenCalledWith(
			SUMMARY,
			expect.objectContaining({ posProfile: "POS - PKU DELANGGU" }),
		)
	})

	it("keeps the button disabled until a recap is loaded", async () => {
		const wrapper = await mountOpen()
		const printButton = wrapper
			.findAll("button")
			.find((b) => b.text().includes("Print"))
		// no data yet -> disabled, and clicking must not reach the printer
		expect(printButton.attributes("disabled")).toBeDefined()
		await printButton.trigger("click")
		await flushPromises()
		expect(printMock.printSalesRecap).not.toHaveBeenCalled()
	})
})
