/**
 * @vitest-environment jsdom
 */
import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"

// Controllable createResource double: components register options, tests
// resolve/reject by mutating the reactive resource the component holds.
const resources = vi.hoisted(() => ({ instances: [] }))

const frappeCall = vi.hoisted(() => vi.fn())

vi.mock("frappe-ui", async () => {
	const { defineComponent, reactive } = await import("vue")
	const Button = defineComponent({
		name: "Button",
		emits: ["click"],
		template: `<button v-bind="$attrs" @click="$emit('click')"><slot /></button>`,
	})
	// renders the body so dialog-internal content (tabs, summary) mounts
	const Dialog = defineComponent({
		name: "Dialog",
		props: {
			modelValue: { type: Boolean, default: false },
			options: { type: Object, default: () => ({}) },
		},
		template: `<div><slot name="body" /></div>`,
	})
	const FeatherIcon = defineComponent({
		name: "FeatherIcon",
		props: { name: { type: String, required: true } },
		template: `<svg data-feather-icon="true" aria-hidden="true"><use :xlink:href="'#' + name" /></svg>`,
	})
	const stub = defineComponent({ name: "FrappeUIStub", render: () => null })
	return {
		Button,
		Dialog,
		FeatherIcon,
		Input: stub,
		call: frappeCall,
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

// DialogTitle needs a reka-ui DialogRoot context the stubbed Dialog lacks.
vi.mock("reka-ui", () => ({
	DialogTitle: { name: "DialogTitle", template: "<h2><slot /></h2>" },
}))

// Manager flag source; the period bar is manager-only. Reactive so the
// visibility test can flip it, defaulting to manager for the period-lens
// tests below.
const bootstrapMock = vi.hoisted(() => ({ store: null }))
vi.mock("@/stores/bootstrap", async () => {
	const { reactive } = await import("vue")
	bootstrapMock.store = reactive({ data: { is_management: true } })
	return { useBootstrapStore: () => bootstrapMock.store }
})

// Real period math, stubbed printer: the print stack needs a device.
const printMock = vi.hoisted(() => ({ printSalesRecap: vi.fn() }))
vi.mock("@/utils/salesRecap", async (importOriginal) => ({
	...(await importOriginal()),
	printSalesRecap: printMock.printSalesRecap,
}))

// The app installs __() as a global property; templates need it.
globalThis.__ = (message, replacements = []) => {
	if (!Array.isArray(replacements) || !replacements.length) return message
	let out = message
	for (const [i, v] of replacements.entries())
		out = out.split(`{${i}}`).join(String(v))
	return out
}

import { periodRange } from "@/utils/salesRecap"
import InvoiceHistoryDialog from "./InvoiceHistoryDialog.vue"
import SessionSummary from "./SessionSummary.vue"

const SUMMARY = {
	opening_shift: "POS-SH-1",
	pos_profile: "Kasir 1",
	shift_name: "Shift Pagi",
	cashier: "Session",
	company_currency: "IDR",
	cash_mode_of_payment: "Cash",
	period_start_date: "2026-09-08 06:32:09",
	generated_at: "2026-09-08 09:10:36",
	status: "Open",
	closing_time: null,
	closing_source: null,
	counted_invoices: 4,
	invoice_count: 4,
	sales_count: 3,
	returns_count: 1,
	gross_sales: 52500,
	returns_total: 2000,
	net_sales: 50500,
	net_total: 50500,
	total_qty: 5,
	average_sale: 16833.333,
	credit_outstanding: 0,
	currency_breakdown: [{ currency: "IDR", amount: 50500 }],
	payments: [
		{ mode_of_payment: "Cash", amount: 40500, is_cash: true, configured: true },
		{
			mode_of_payment: "QRIS",
			amount: 10000,
			is_cash: false,
			configured: true,
		},
		{ mode_of_payment: "Debit", amount: 0, is_cash: false, configured: true },
		{
			mode_of_payment: "EWallet Lama",
			amount: 500,
			is_cash: false,
			configured: false,
		},
	],
	opening_cash: 100000,
	cash_collected: 40500,
	cash_in_hand: 140500,
	cash_expected: 140500,
	expense: null,
	expense_supported: false,
	total_cash: 40500,
	total_non_cash: 10500,
	methods_grand_total: 51000,
	items: [
		{ item_code: "I1", item_name: "Kopi", qty: 2, base_net_amount: 1000 },
	],
	packages: [
		{
			item_code: "P1",
			item_name: "Paket Hemat",
			qty: 1,
			base_net_amount: 50000,
		},
	],
	items_shown: 2,
	items_total_groups: 2,
	items_truncated: false,
	tax_total: 110,
	total_tax: 110,
	other_charges: [
		{
			account_head: "Service Charge - J",
			label: "Service Charge",
			amount: 50,
		},
	],
	other_charges_total: 50,
	categories: [
		{ category: "Makanan", qty: 1, base_net_amount: 40000 },
		{ category: "Minuman", qty: 4, base_net_amount: 10500 },
	],
	categories_shown: 2,
	categories_total_groups: 2,
	categories_truncated: false,
	item_discount: 0,
	total_discount: 1250,
}

function summaryResource() {
	const found = resources.instances.find(
		(i) => i.url === "pos_next.api.shifts.get_session_summary",
	)
	return found
}

function mountSummary(props = {}) {
	const wrapper = mount(SessionSummary, {
		props: { openingShift: "OS-1", ...props },
		global: { config: { globalProperties: { __: globalThis.__ } } },
	})
	return wrapper
}

async function mountWithSummary(overrides = {}) {
	resources.instances.length = 0
	const wrapper = mountSummary()
	const entry = summaryResource()
	entry.resource.data = { ...SUMMARY, ...overrides }
	entry.resource.loading = false
	await flushPromises()
	return { wrapper, entry }
}

beforeEach(() => {
	resources.instances.length = 0
	bootstrapMock.store.data.is_management = true
})

describe("SessionSummary", () => {
	it("shows the loading state before data arrives", async () => {
		const wrapper = mountSummary()
		const entry = summaryResource()
		entry.resource.loading = true
		await flushPromises()

		expect(wrapper.text()).toContain("Loading session summary...")
	})

	it("registers the session summary endpoint with the shift", async () => {
		mountSummary({ openingShift: "OS-9" })
		const entry = summaryResource()
		expect(entry).toBeTruthy()
		expect(entry.opts.makeParams()).toEqual({ opening_shift: "OS-9" })
	})

	it("shows an error state with retry when there is no cached data", async () => {
		const wrapper = mountSummary()
		const entry = summaryResource()
		entry.resource.loading = false
		await flushPromises()

		expect(wrapper.text()).toContain("Could not load the session summary.")

		entry.resource.reload.mockClear()
		// the period chips are buttons too — target Retry by its label
		const retry = wrapper.findAll("button").find((b) => b.text() === "Retry")
		await retry.trigger("click")
		expect(entry.resource.reload).toHaveBeenCalled()
	})

	it("renders every report section in the required order", async () => {
		const { wrapper } = await mountWithSummary()
		const html = wrapper.html()

		const markers = [
			"Shift Pagi",
			"Cashier",
			"Sales Summary",
			"Cash Summary",
			"Payment Methods",
			"Charges & Discounts",
			"Refund",
			"Sales per Category",
			"Package Detail", // "Item & Package Detail" (html-escaped in html())
		]
		let last = -1
		for (const marker of markers) {
			const at = html.indexOf(marker)
			expect(at, `${marker} missing`).toBeGreaterThan(-1)
			expect(at, `${marker} out of order`).toBeGreaterThan(last)
			last = at
		}
	})

	it("renders the shift info: name, cashier, opening, closing", async () => {
		const { wrapper } = await mountWithSummary()
		// shift name sits in the header (frozen group, else profile)
		expect(wrapper.text()).toContain("Shift Pagi")
		const info = wrapper.find('[data-test="shift-info"]').text()
		expect(info).toContain("Session")
		expect(info).toContain("D:2026-09-08 06:32:09")
		// no closing time yet: explicit dash + note, never a guessed time
		expect(info).toContain("Not closed yet")

		// profile fallback when no group is frozen on the shift
		const noGroup = await mountWithSummary({ shift_name: null })
		expect(noGroup.wrapper.text()).toContain("Kasir 1")
	})

	it("marks the closing time as actual or estimate", async () => {
		const actual = await mountWithSummary({
			closing_time: "2026-09-08 17:00:00",
			closing_source: "actual",
		})
		expect(actual.wrapper.find('[data-test="shift-info"]').text()).toContain(
			"Actual",
		)
		expect(actual.wrapper.find('[data-test="shift-info"]').text()).toContain(
			"D:2026-09-08 17:00:00",
		)

		const estimate = await mountWithSummary({
			closing_time: "2026-09-08 22:00:00",
			closing_source: "estimate",
		})
		expect(estimate.wrapper.find('[data-test="shift-info"]').text()).toContain(
			"estimate",
		)
	})

	it("shows the sales hero card with the order stats and honest caption", async () => {
		const { wrapper } = await mountWithSummary()
		const sales = wrapper.find('[data-test="sales-summary"]').text()

		expect(sales).toContain("50500 IDR") // total sales (after returns)
		expect(sales).toContain("3 orders") // orders inline with the hero
		expect(sales).toContain("Avg 16833.333 IDR / order")
		expect(sales).toContain("After returns, tax included")
		// jargon captions are gone — the values carry the meaning
		expect(sales).not.toContain("Submitted sales invoices (returns not counted)")
		expect(sales).not.toContain("Gross Sales ÷ Total Orders")
	})

	it("shows gross sales as its own card with the honest caption", async () => {
		const { wrapper } = await mountWithSummary()
		const gross = wrapper.find('[data-test="gross-sales"]').text()

		expect(gross).toContain("Gross Sales")
		expect(gross).toContain("52500 IDR")
		expect(gross).toContain("Before returns, incl. tax")
		expect(gross).not.toContain("before tax")
	})

	it("hides the gross sales card when there is nothing to show", async () => {
		const { wrapper } = await mountWithSummary({
			gross_sales: 0,
			credit_outstanding: 0,
		})
		expect(wrapper.find('[data-test="gross-sales"]').exists()).toBe(false)
	})

	it("shows the cash summary with the honest expense placeholder", async () => {
		const { wrapper } = await mountWithSummary()
		const cash = wrapper.find('[data-test="cash-summary"]').text()

		expect(cash).toContain("100000 IDR") // opening balance
		expect(cash).toContain("40500 IDR") // cash receipts
		expect(cash).toContain("Not recorded") // expense: unsupported, not zero
		expect(cash).toContain("140500 IDR") // cash in hand
		expect(cash).toContain("Cash in Hand is the balance before any expenses")
	})

	it("shows a recorded expense when one exists", async () => {
		const { wrapper } = await mountWithSummary({ expense: 25000 })
		expect(wrapper.find('[data-test="cash-summary"]').text()).toContain(
			"25000 IDR",
		)
		expect(wrapper.find('[data-test="cash-summary"]').text()).not.toContain(
			"Not recorded",
		)
	})

	it("lists all configured payment methods (zero included) plus unconfigured ones", async () => {
		const { wrapper } = await mountWithSummary()
		const section = wrapper.find('[data-test="payments-section"]')
		const table = wrapper.find('[data-test="payments-table"]')
		const text = table.text()

		// every method appears, zero included
		expect(text).toContain("Cash")
		expect(text).toContain("QRIS")
		expect(text).toContain("Debit")
		expect(text).toContain("EWallet Lama")
		expect(text).toContain("Unused") // Debit at 0
		expect(text).toContain("Not on the POS Profile") // used, unconfigured
		expect(text).toContain("40500 IDR")
		expect(text).toContain("500 IDR")
		// totals sit below the method rows, side by side + grand total
		expect(section.text()).toContain("Total Cash")
		expect(section.text()).toContain("40500 IDR")
		expect(section.text()).toContain("Total Non-Cash")
		expect(section.text()).toContain("10500 IDR")
		expect(section.text()).toContain("Grand Total")
		expect(section.text()).toContain("51000 IDR")
	})

	it("shows charges and discounts without fabricating a split", async () => {
		const { wrapper } = await mountWithSummary()
		const charges = wrapper.find('[data-test="charges-section"]').text()

		expect(charges).toContain("Service Charge") // listed under its real name
		expect(charges).toContain("50 IDR")
		expect(charges).toContain("Tax / PPN")
		expect(charges).toContain("110 IDR")
		expect(charges).toContain("Discount")
		expect(charges).toContain("1250 IDR")
	})

	it("shows the refund total with the return count pill", async () => {
		const { wrapper } = await mountWithSummary()
		const refund = wrapper.find('[data-test="refund-section"]').text()
		expect(refund).toContain("Total Refunds")
		expect(refund).toContain("-2000 IDR")
		expect(refund).toContain("1 return invoices")
	})

	it("shows a No refunds pill when there are no returns", async () => {
		const { wrapper } = await mountWithSummary({
			returns_total: 0,
			returns_count: 0,
		})
		const refund = wrapper.find('[data-test="refund-section"]')
		expect(refund.text()).toContain("No refunds")
		expect(refund.text()).not.toContain("return invoices")
		expect(refund.text()).toContain("0 IDR")
		expect(refund.text()).not.toContain("-0")
	})

	it("renders sales per category as the primary table", async () => {
		const { wrapper } = await mountWithSummary()
		const table = wrapper.find('[data-test="categories-table"]')
		expect(table.exists()).toBe(true)
		expect(table.text()).toContain("Makanan")
		expect(table.text()).toContain("Minuman")
		expect(table.text()).toContain("40000 IDR")

		const capped = await mountWithSummary({
			categories_truncated: true,
			categories_shown: 2,
			categories_total_groups: 30,
		})
		expect(capped.wrapper.text()).toContain(
			"Showing top 2 of 30 categories; session totals above cover everything.",
		)
	})

	it("labels uncategorised revenue explicitly", async () => {
		const { wrapper } = await mountWithSummary({
			categories: [{ category: null, qty: 1, base_net_amount: 900 }],
		})
		expect(wrapper.find('[data-test="categories-table"]').text()).toContain(
			"No category",
		)
	})

	it("renders item and package detail as an always-open section", async () => {
		const { wrapper } = await mountWithSummary()
		const details = wrapper.find('[data-test="items-details"]')
		expect(details.exists()).toBe(true)
		expect(details.element.tagName.toLowerCase()).not.toBe("details")
		expect(details.text()).toContain("Paket Hemat")
		expect(details.find('[data-test="items-table"]').exists()).toBe(true)
		// per-row price and a subtotal column replace the bare revenue figure
		const table = details.find('[data-test="items-table"]')
		expect(table.text()).toContain("Price")
		expect(table.text()).toContain("Subtotal")
	})

	it("shows the discount column only when some line is discounted", async () => {
		const withDiscounts = await mountWithSummary({
			items: [
				{
					item_code: "I1",
					item_name: "Kopi",
					qty: 2,
					base_net_amount: 1000,
					price_list_rate: 600,
					discount_amount: 200,
				},
			],
		})
		const shown = withDiscounts.wrapper.find('[data-test="items-details"]')
		expect(shown.text()).toContain("Discount")
		expect(shown.text()).toContain("200 IDR")

		const noDiscounts = await mountWithSummary({
			items: [
				{
					item_code: "I1",
					item_name: "Kopi",
					qty: 2,
					base_net_amount: 1000,
					price_list_rate: 500,
					discount_amount: 0,
				},
			],
		})
		const hidden = noDiscounts.wrapper.find('[data-test="items-details"]')
		expect(hidden.text()).not.toContain("Discount")
	})

	it("treats missing price/discount fields as zero (old servers)", async () => {
		const { wrapper } = await mountWithSummary()
		const table = wrapper.find('[data-test="items-table"]')
		expect(table.text()).toContain("0 IDR")
		// no discount data at all → column hidden
		expect(table.text()).not.toContain("Discount")
	})

	it("always shows the item code under each item name", async () => {
		const duplicates = await mountWithSummary({
			items: [
				{
					item_code: "I1",
					item_name: "Kopi Susu",
					qty: 2,
					base_net_amount: 1000,
				},
				{
					item_code: "I2",
					item_name: "Kopi Susu",
					qty: 1,
					base_net_amount: 2000,
				},
			],
		})
		const dupTable = duplicates.wrapper.find('[data-test="items-table"]')
		expect(dupTable.text()).toContain("I1")
		expect(dupTable.text()).toContain("I2")

		const unique = await mountWithSummary({
			items: [
				{ item_code: "I1", item_name: "Kopi", qty: 2, base_net_amount: 1000 },
				{ item_code: "I2", item_name: "Teh", qty: 1, base_net_amount: 2000 },
			],
		})
		const uniqueTable = unique.wrapper.find('[data-test="items-table"]')
		expect(uniqueTable.text()).toContain("Kopi")
		expect(uniqueTable.text()).toContain("I1")
	})

	it("discloses a capped item table", async () => {
		const { wrapper } = await mountWithSummary({
			items_truncated: true,
			items_shown: 2,
			items_total_groups: 120,
		})
		expect(wrapper.text()).toContain(
			"Showing top 2 of 120 items; the totals above cover the whole session.",
		)
	})

	it("keeps sections rendered with a notice when the session has no sales", async () => {
		const { wrapper } = await mountWithSummary({ counted_invoices: 0 })
		expect(wrapper.text()).toContain("No sales yet in this session.")
		// cash summary still matters (opening balance) — not hidden
		expect(wrapper.find('[data-test="cash-summary"]').exists()).toBe(true)
	})

	it("stacks the sales metrics on mobile and keeps tabular numbers", async () => {
		const { wrapper } = await mountWithSummary()
		const sales = wrapper.find('[data-test="sales-summary"]')
		// hero values wrap under each other on mobile (flex-wrap), no fixed grid
		const hero = sales.find('[data-test="kpi-total-sales"]')
		expect(hero.exists()).toBe(true)
		expect(sales.html()).toContain("flex-wrap")
		// shift info is one quiet meta line under the title
		const info = wrapper.find('[data-test="shift-info"]')
		expect(info.exists()).toBe(true)
		expect(info.classes()).toContain("truncate")
		// numeric values use tabular numerals
		expect(sales.html()).toContain("tabular-nums")
	})

	it("keeps the last snapshot visible and flags it stale on refresh errors", async () => {
		const { wrapper, entry } = await mountWithSummary()
		expect(wrapper.text()).not.toContain(
			"Showing data from the last successful refresh.",
		)

		entry.opts.onError()
		await flushPromises()
		expect(wrapper.text()).toContain(
			"Showing data from the last successful refresh.",
		)
		// cached numbers still on screen — not fake-live
		expect(wrapper.text()).toContain("50500 IDR")
	})

	it("reloads on manual refresh and clears the stale flag", async () => {
		const { wrapper, entry } = await mountWithSummary()
		entry.opts.onError()
		await flushPromises()

		entry.resource.reload.mockClear()
		await wrapper.find('button[aria-label="Refresh"]').trigger("click")

		expect(entry.resource.reload).toHaveBeenCalledTimes(1)
		await flushPromises()
		expect(wrapper.text()).not.toContain(
			"Showing data from the last successful refresh.",
		)
	})

	it("shows the no-shift empty state without an opening shift", () => {
		resources.instances.length = 0
		const wrapper = mountSummary({ openingShift: "" })
		expect(wrapper.text()).toContain("No open shift for this session.")
		// no fetch attempted without a shift
		const entry = summaryResource()
		expect(entry.resource.reload).not.toHaveBeenCalled()
	})
})

describe("InvoiceHistoryDialog", () => {
	function mountDialog(posOpeningShift = "OS-1") {
		return mount(InvoiceHistoryDialog, {
			props: { modelValue: true, posProfile: "juri1", posOpeningShift },
			global: {
				config: { globalProperties: { __: globalThis.__ } },
				stubs: { ReturnInvoiceDialog: true, Teleport: true },
			},
		})
	}

	it("opens straight on the transactions list with or without a shift", async () => {
		resources.instances.length = 0
		const wrapper = mountDialog()
		await flushPromises()

		// the session recap lives in the Sales Recap view now, not here
		const urls = resources.instances.map((i) => i.url)
		expect(urls).not.toContain("pos_next.api.shifts.get_session_summary")
		expect(wrapper.find('[role="tablist"]').exists()).toBe(false)
		expect(wrapper.text()).toContain("No invoices found")
	})

	it("uses a constrained dialog with a fixed header, close and a minimal footer", async () => {
		resources.instances.length = 0
		const wrapper = mountDialog()
		await flushPromises()

		const dialog = wrapper.findComponent({ name: "Dialog" })
		// one shared width (~1024px) for every cashier list dialog
		expect(dialog.props("options").size).toBe("5xl")
		expect(
			wrapper
				.find('[data-test="dialog-header"] button[aria-label="Close"]')
				.exists(),
		).toBe(true)
		const body = wrapper.find('[data-test="dialog-body"]')
		expect(body.classes()).toContain("overflow-y-auto")
		expect(wrapper.find('[data-test="dialog-footer"]').text()).toContain(
			"Close",
		)
	})

	it("opens the eye/View detail inside the dialog and loads it", async () => {
		resources.instances.length = 0
		frappeCall.mockResolvedValue({
			name: "INV-0001",
			customer_name: "Budi",
			status: "Paid",
			grand_total: 25000,
			items: [{ item_name: "Kopi", qty: 1, rate: 25000, amount: 25000 }],
			payments: [],
		})
		const wrapper = mountDialog()
		await flushPromises()
		resources.instances
			.find((i) => i.url === "pos_next.api.invoices.get_invoices")
			.opts.onSuccess([
				{ name: "INV-0001", posting_date: "2026-10-01", status: "Paid", docstatus: 1, grand_total: 25000, payments: [] },
			])
		await flushPromises()

		const view = wrapper.findAll("button").find((b) => b.text() === "View")
		await view.trigger("click")
		await flushPromises()

		expect(frappeCall).toHaveBeenCalledWith("pos_next.api.invoices.get_invoice", {
			invoice_name: "INV-0001",
		})
		expect(wrapper.text()).toContain("Kopi")
		expect(wrapper.text()).not.toContain("Failed to load invoice details")

		await wrapper.find('button[aria-label="Back"]').trigger("click")
		await flushPromises()
		expect(wrapper.text()).not.toContain("Kopi")
		expect(wrapper.text()).toContain("INV-0001")
	})

	it("names the cashier on the date line, and only when the API provides one", async () => {
		resources.instances.length = 0
		const wrapper = mountDialog()
		await flushPromises()
		const entry = resources.instances.find(
			(i) => i.url === "pos_next.api.invoices.get_invoices",
		)

		const invoice = {
			name: "INV-0001",
			posting_date: "2026-09-20",
			posting_time: "10:00:00",
			buyer_name: "Budi",
			status: "Paid",
			grand_total: 25000,
			payments: [],
		}
		entry.opts.onSuccess([{ ...invoice, cashier_name: "Siti" }])
		await flushPromises()
		expect(wrapper.text()).toContain("Siti")

		entry.opts.onSuccess([invoice])
		await flushPromises()
		expect(wrapper.text()).not.toContain("Siti")
	})
})

describe("SessionSummary period lens", () => {
	const PERIOD = {
		...SUMMARY,
		opening_shift: undefined,
		shift_name: undefined,
		cashier: undefined,
		period_from: "2026-09-01",
		period_to: "2026-09-07",
		shift_count: 9,
	}

	function periodResource() {
		return resources.instances.find(
			(i) => i.url === "pos_next.api.shifts.get_period_summary",
		)
	}

	function mountPeriod(props = {}) {
		resources.instances.length = 0
		return mount(SessionSummary, {
			props: { openingShift: "OS-1", posProfile: "Kasir 1", ...props },
			global: {
				config: { globalProperties: { __: globalThis.__ } },
				stubs: { Teleport: true },
			},
		})
	}

	it("offers the open shift first, then the profile-wide windows", () => {
		const wrapper = mountPeriod()
		const chips = wrapper.findAll('[data-test^="period-chip-"]').map((c) => c.text())
		expect(chips).toEqual([
			"This Shift",
			"Today",
			"Yesterday",
			"Last 7 Days",
			"This Month",
			"This Year",
			"Custom Range",
		])
		// the shift lens is the default and the only fetch on mount
		expect(summaryResource().resource.reload).toHaveBeenCalledTimes(1)
		expect(periodResource().resource.reload).not.toHaveBeenCalled()
	})

	it("switching to a preset fetches the profile window with explicit dates", async () => {
		const wrapper = mountPeriod()
		await wrapper.find('[data-test="period-chip-yesterday"]').trigger("click")
		await flushPromises()

		const period = periodResource()
		expect(period.resource.reload).toHaveBeenCalledTimes(1)
		const expected = periodRange("yesterday")
		expect(period.opts.makeParams()).toEqual({
			pos_profile: "Kasir 1",
			from_date: expected.from,
			to_date: expected.to,
		})
		// the shift's numbers never masquerade as the window's while it loads
		expect(wrapper.text()).toContain("Loading session summary...")
	})

	it("shows the period header instead of the shift info once the window loads", async () => {
		const wrapper = mountPeriod()
		await wrapper.find('[data-test="period-chip-last7"]').trigger("click")
		const period = periodResource()
		period.resource.data = PERIOD
		period.resource.loading = false
		await flushPromises()

		expect(wrapper.find('[data-test="shift-info"]').exists()).toBe(false)
		const info = wrapper.find('[data-test="period-info"]').text()
		expect(info).toContain("D:2026-09-01 – D:2026-09-07")
		expect(info).toContain("9")
		expect(info).toContain("All cashiers")
		expect(wrapper.find("h3").text()).toBe("Kasir 1")
		// every money section is still on screen
		expect(wrapper.find('[data-test="sales-summary"]').exists()).toBe(true)
		expect(wrapper.find('[data-test="payments-table"]').exists()).toBe(true)
		expect(wrapper.find('[data-test="categories-table"]').exists()).toBe(true)
	})

	it("waits for both custom dates before fetching", async () => {
		const wrapper = mountPeriod()
		await wrapper.find('[data-test="period-chip-custom"]').trigger("click")
		await flushPromises()
		const period = periodResource()
		expect(period.resource.reload).not.toHaveBeenCalled()
		expect(wrapper.text()).toContain(
			"Pick a from and to date to load the recap.",
		)

		await wrapper.find('[data-test="period-from"]').setValue("2026-08-01")
		await wrapper.find('[data-test="period-to"]').setValue("2026-08-31")
		await flushPromises()
		expect(period.resource.reload).toHaveBeenCalledTimes(1)
		expect(period.opts.makeParams()).toEqual({
			pos_profile: "Kasir 1",
			from_date: "2026-08-01",
			to_date: "2026-08-31",
		})
	})

	it("defaults to today's window when there is a profile but no open shift", async () => {
		const wrapper = mountPeriod({ openingShift: "" })
		await flushPromises()

		const options = wrapper.findAll('[data-test^="period-chip-"]').map((o) => o.text())
		expect(options).not.toContain("This Shift")
		const period = periodResource()
		expect(period.resource.reload).toHaveBeenCalledTimes(1)
		expect(period.opts.makeParams().from_date).toBe(periodRange("today").from)
		expect(summaryResource().resource.reload).not.toHaveBeenCalled()
	})

	it("returns to the shift lens with a fresh session fetch", async () => {
		const wrapper = mountPeriod()
		const session = summaryResource()
		await wrapper.find('[data-test="period-chip-month"]').trigger("click")
		await wrapper.find('[data-test="period-chip-shift"]').trigger("click")
		await flushPromises()
		expect(session.resource.reload).toHaveBeenCalledTimes(2)
	})

	it("prints the loaded recap for the profile", async () => {
		printMock.printSalesRecap.mockReset().mockResolvedValue(undefined)
		const wrapper = mountPeriod()
		const session = summaryResource()
		session.resource.data = SUMMARY
		session.resource.loading = false
		await flushPromises()

		await wrapper.find('button[aria-label="Print"]').trigger("click")
		await flushPromises()
		expect(printMock.printSalesRecap).toHaveBeenCalledTimes(1)
		expect(printMock.printSalesRecap).toHaveBeenCalledWith(SUMMARY, {
			posProfile: "Kasir 1",
		})
	})

	it("shows the period selector to managers only", () => {
		const asManager = mountPeriod()
		expect(asManager.find('[data-test="period-chip-shift"]').exists()).toBe(true)
		asManager.unmount()

		bootstrapMock.store.data.is_management = false
		const asCashier = mountPeriod()
		expect(asCashier.find('[data-test="period-chip-shift"]').exists()).toBe(false)
		// data logic untouched: the shift lens still loads
		expect(summaryResource().resource.reload).toHaveBeenCalledTimes(1)
		asCashier.unmount()
	})
})

describe("SessionSummary mobile period sheet", () => {
	function periodResource() {
		return resources.instances.find(
			(i) => i.url === "pos_next.api.shifts.get_period_summary",
		)
	}

	function mountSheet(props = {}) {
		resources.instances.length = 0
		return mount(SessionSummary, {
			props: { openingShift: "OS-1", posProfile: "Kasir 1", ...props },
			global: {
				config: { globalProperties: { __: globalThis.__ } },
				stubs: { Teleport: true },
			},
		})
	}

	it("opens the sheet from the pill and applies a preset", async () => {
		const wrapper = mountSheet()
		const pill = wrapper.find('[data-test="period-filter-button"]')
		expect(pill.exists()).toBe(true)
		expect(pill.text()).toContain("This Shift")

		await pill.trigger("click")
		expect(wrapper.find('[data-test="period-option-today"]').exists()).toBe(true)

		await wrapper.find('[data-test="period-option-yesterday"]').trigger("click")
		await flushPromises()
		expect(periodResource().resource.reload).toHaveBeenCalledTimes(1)
		expect(wrapper.find('[data-test="period-option-today"]').exists()).toBe(false)
		wrapper.unmount()
	})

	it("keeps the sheet open for custom and closes on Apply", async () => {
		const wrapper = mountSheet()
		await wrapper.find('[data-test="period-filter-button"]').trigger("click")
		await wrapper.find('[data-test="period-option-custom"]').trigger("click")
		await flushPromises()
		expect(wrapper.find('[data-test="sheet-period-from"]').exists()).toBe(true)

		await wrapper.find('[data-test="sheet-period-from"]').setValue("2026-08-01")
		await wrapper.find('[data-test="sheet-period-to"]').setValue("2026-08-31")
		await flushPromises()
		expect(periodResource().resource.reload).toHaveBeenCalledTimes(1)
		expect(wrapper.find('[data-test="period-filter-button"]').text()).toContain(
			"2026-08-01",
		)

		const apply = wrapper.findAll("button").find((b) => b.text() === "Apply")
		await apply.trigger("click")
		expect(wrapper.find('[data-test="sheet-period-from"]').exists()).toBe(false)
		wrapper.unmount()
	})

	it("hides the filter pill from non-managers", () => {
		bootstrapMock.store.data.is_management = false
		const wrapper = mountSheet()
		expect(wrapper.find('[data-test="period-filter-button"]').exists()).toBe(false)
		wrapper.unmount()
	})
})
