/**
 * @vitest-environment jsdom
 */
import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"

// Controllable createResource double: tests resolve by mutating the reactive
// resource the component holds (same pattern as SessionSummary.test.js).
const resources = vi.hoisted(() => ({ instances: [] }))

vi.mock("frappe-ui", async () => {
	const { defineComponent, reactive } = await import("vue")
	const Button = defineComponent({
		name: "Button",
		emits: ["click"],
		template: `<button v-bind="$attrs" @click="$emit('click')"><slot /></button>`,
	})
	const stub = defineComponent({ name: "FrappeUIStub", render: () => null })
	return {
		Button,
		FeatherIcon: stub,
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

// Read-only store usage: a fixed duration keeps the header chip deterministic;
// currentTime is the shell's 1s tick that re-arms the minutes-ago computed.
vi.mock("@/stores/posShift", async () => {
	const { reactive, ref } = await import("vue")
	const store = reactive({
		shiftDuration: "01:23:45",
		currentTime: ref("09:00:00"),
	})
	return {
		usePOSShiftStore: () => store,
	}
})

// The app installs __() as a global property; templates need it.
globalThis.__ = (message, replacements = []) => {
	if (!Array.isArray(replacements) || !replacements.length) return message
	let out = message
	for (const [i, v] of replacements.entries())
		out = out.split(`{${i}}`).join(String(v))
	return out
}

import ShiftDashboard from "./ShiftDashboard.vue"
import { usePOSShiftStore } from "@/stores/posShift"

const DASHBOARD = {
	opening_shift: "POS-SH-1",
	pos_profile: "Kasir 1",
	cashier: "Budi",
	company_currency: "IDR",
	period_start_date: "2026-09-20 06:00:00",
	generated_at: "2026-09-20 09:00:00",
	net_sales: 150000,
	gross_sales: 160000,
	sales_count: 3,
	returns_count: 1,
	returns_total: 10000,
	average_sale: 50000,
	total_qty: 7,
	invoice_discount: 2000,
	item_discount: 0,
	total_discount: 2000,
	payments: [
		{ mode_of_payment: "Cash", amount: 90000, is_cash: true, configured: true },
		{ mode_of_payment: "QRIS", amount: 60000, is_cash: false, configured: true },
	],
	total_cash: 90000,
	total_non_cash: 60000,
	methods_grand_total: 150000,
	opening_cash: 50000,
	cash_collected: 90000,
	cash_expected: 140000,
	expense: null,
	expense_supported: false,
	items: [
		{ item_code: "I1", item_name: "Kopi Susu", qty: 3, base_net_amount: 60000 },
		{ item_code: "I2", item_name: "Roti", qty: 2, base_net_amount: 30000 },
	],
	hourly: [
		{ start: "2026-09-20 08:00:00", net_sales: 50000, sales_count: 1, is_current: false },
		{ start: "2026-09-20 09:00:00", net_sales: 100000, sales_count: 2, is_current: true },
		{ start: "2026-09-20 10:00:00", net_sales: -20000, sales_count: 0, is_current: false },
		{ start: "2026-09-20 11:00:00", net_sales: 0, sales_count: 0, is_current: false },
	],
	recent: [
		{
			name: "INV-2026-0001",
			posting_dt: "2026-09-20 08:15:00",
			is_return: false,
			outstanding_amount: 0,
			amount: 50000,
			payment_mode: "Cash",
		},
		{
			name: "INV-2026-0002",
			posting_dt: "2026-09-20 10:15:00",
			is_return: true,
			outstanding_amount: 25000,
			amount: -20000,
			payment_mode: null,
		},
	],
}

function dashboardResource() {
	return resources.instances.find(
		(i) => i.url === "pos_next.api.shifts.get_shift_dashboard",
	)
}

function periodDashboardResource() {
	return resources.instances.find(
		(i) => i.url === "pos_next.api.shifts.get_period_dashboard",
	)
}

function mountDashboard(props = {}) {
	resources.instances.length = 0
	const wrapper = mount(ShiftDashboard, {
		props: { openingShift: "OS-1", ...props },
		global: { config: { globalProperties: { __: globalThis.__ } } },
	})
	return { wrapper, entry: dashboardResource() }
}

async function mountWithData(overrides = {}) {
	const { wrapper, entry } = mountDashboard()
	entry.resource.data = { ...DASHBOARD, ...overrides }
	entry.resource.loading = false
	await flushPromises()
	return { wrapper, entry }
}

beforeEach(() => {
	resources.instances.length = 0
})

describe("ShiftDashboard", () => {
	it("registers the shift dashboard endpoint with the shift param", () => {
		mountDashboard({ openingShift: "OS-9" })
		const entry = dashboardResource()
		expect(entry).toBeTruthy()
		expect(entry.opts.makeParams()).toEqual({ opening_shift: "OS-9" })
	})

	it("shows a full-panel skeleton before the first load", async () => {
		const { wrapper, entry } = mountDashboard()
		expect(entry.resource.reload).toHaveBeenCalledTimes(1)
		expect(wrapper.find('[data-test="dashboard-skeleton"]').exists()).toBe(true)

		entry.resource.data = DASHBOARD
		entry.resource.loading = false
		await flushPromises()
		expect(wrapper.find('[data-test="dashboard-skeleton"]').exists()).toBe(false)
		expect(wrapper.find('[data-test="kpi-grid"]').exists()).toBe(true)
	})

	it("renders the seven KPI cards with their values", async () => {
		const { wrapper } = await mountWithData()
		const kpis = wrapper.findAll('[data-test="kpi-grid"] > div')
		expect(kpis.length).toBe(7)
		expect(kpis[0].text()).toContain("Net Sales")
		expect(kpis[0].text()).toContain("150000 IDR")
		expect(kpis[1].text()).toContain("Transactions")
		expect(kpis[1].text()).toContain("3")
		expect(kpis[2].text()).toContain("Average Transaction")
		expect(kpis[2].text()).toContain("50000 IDR")
		expect(kpis[3].text()).toContain("Items Sold")
		expect(kpis[3].text()).toContain("7")
		expect(kpis[4].text()).toContain("Returns")
		expect(kpis[4].text()).toContain("1 · -10000 IDR")
		expect(kpis[5].text()).toContain("Discounts")
		expect(kpis[5].text()).toContain("2000 IDR")
		expect(wrapper.find('[data-test="kpi-cash"]').text()).toContain(
			"Cash in Drawer",
		)
		expect(wrapper.find('[data-test="kpi-cash"]').text()).toContain("140000 IDR")
		// expense_supported is false in the fixture
		expect(wrapper.find('[data-test="kpi-cash"]').text()).toContain(
			"Before expenses",
		)
	})

	it("drops the Before expenses subtitle when expenses are supported", async () => {
		const { wrapper } = await mountWithData({ expense_supported: true })
		expect(wrapper.find('[data-test="kpi-cash"]').text()).not.toContain(
			"Before expenses",
		)
	})

	it("renders the hourly chart with positive, current, negative and zero buckets", async () => {
		const { wrapper } = await mountWithData()
		// every bucket renders a column; zero buckets keep only the axis floor
		expect(wrapper.findAll('[data-test="hour-column"]').length).toBe(4)
		const bars = wrapper.findAll('[data-test="hour-bar"]')
		expect(bars.length).toBe(3)
		const classes = bars.map((b) => b.classes())
		expect(classes.some((c) => c.includes("bg-blue-300"))).toBe(true) // is_current
		expect(classes.some((c) => c.includes("bg-blue-500"))).toBe(true)
		expect(classes.some((c) => c.includes("bg-red-400"))).toBe(true) // negative
		const negative = bars.find((b) => b.classes().includes("bg-red-400"))
		expect(negative.attributes("title")).toContain("-20000 IDR")
		expect(wrapper.find('[data-test="hourly-chart"]').text()).toContain("08:00")
		expect(wrapper.find('[data-test="hourly-chart"]').text()).toContain("11:00")
		expect(wrapper.find('[data-test="hourly-section"]').text()).toContain(
			"Returns are netted into their hour.",
		)
	})

	it("renders payment rows with share percentages and the drawer annotation", async () => {
		const { wrapper } = await mountWithData()
		const rows = wrapper.findAll('[data-test="payment-row"]')
		expect(rows.length).toBe(2)
		const cash = rows[0]
		expect(cash.text()).toContain("Cash")
		expect(cash.text()).toContain("90000 IDR")
		expect(cash.text()).toContain("60%")
		expect(cash.find('[data-test="drawer-note"]').text()).toContain("in drawer")
		expect(cash.find('[data-test="drawer-note"]').text()).toContain("140000 IDR")
		expect(cash.find('[data-test="payment-bar"]').attributes("style")).toContain(
			"width: 60%",
		)
		expect(rows[1].text()).toContain("40%")
		expect(rows[1].find('[data-test="drawer-note"]').exists()).toBe(false)
	})

	it("guards the payment percentage when the grand total is zero", async () => {
		const { wrapper } = await mountWithData({ methods_grand_total: 0 })
		expect(wrapper.find('[data-test="payment-row"]').text()).toContain("0%")
	})

	it("caps the payment bar when change given pushes a method past 100%", async () => {
		const { wrapper } = await mountWithData({
			payments: [
				{ mode_of_payment: "Cash", amount: -120000, is_cash: true, configured: true },
			],
			methods_grand_total: 100000,
		})
		// the % text stays truthful, the bar never overflows the track
		expect(wrapper.find('[data-test="payment-row"]').text()).toContain("120%")
		const bar = wrapper.find('[data-test="payment-bar"]')
		expect(bar.attributes("style")).toContain("width: 100%")
		expect(bar.attributes("style")).not.toContain("width: 120%")
	})

	it("caps top items at five", async () => {
		const items = Array.from({ length: 7 }, (_, i) => ({
			item_code: `I${i}`,
			item_name: `Item ${i}`,
			qty: 1,
			base_net_amount: 1000 * (7 - i),
		}))
		const { wrapper } = await mountWithData({ items })
		const rows = wrapper.findAll('[data-test="top-item"]')
		expect(rows.length).toBe(5)
		expect(rows[0].text()).toContain("Item 0")
		expect(rows[0].text()).toContain("7000 IDR")
	})

	it("renders recent transactions with return and credit badges", async () => {
		const { wrapper } = await mountWithData()
		const rows = wrapper.findAll('[data-test="recent-row"]')
		expect(rows.length).toBe(2)
		expect(rows[0].text()).toContain("INV-2026-0001")
		expect(rows[0].text()).toContain("Cash")
		expect(rows[0].find('[data-test="return-badge"]').exists()).toBe(false)
		expect(rows[1].find('[data-test="return-badge"]').exists()).toBe(true)
		// outstanding_amount > 0 flags credit
		expect(rows[1].find('[data-test="credit-badge"]').exists()).toBe(true)
		expect(rows[1].text()).toContain("—") // null payment_mode
		const amount = rows[1].find('[data-test="recent-amount"]')
		expect(amount.classes()).toContain("text-red-600")
		expect(amount.text()).toContain("-20000 IDR")
	})

	it("shows the shift context chips and the updated-minutes-ago label", async () => {
		const { wrapper } = await mountWithData()
		const chips = wrapper.find('[data-test="shift-chips"]').text()
		expect(chips).toContain("POS-SH-1")
		expect(chips).toContain("Opened")
		expect(chips).toContain("01:23:45")
		expect(chips).toContain("Budi")
		expect(chips).toContain("Kasir 1")
		expect(wrapper.find('[data-test="updated-label"]').text()).toBe(
			"Updated 0 min ago",
		)
	})

	it("ages and ambers the updated label on a store clock tick", async () => {
		const now = Date.now()
		const clock = vi.spyOn(Date, "now").mockReturnValue(now)
		try {
			const { wrapper } = await mountWithData()
			const label = wrapper.find('[data-test="updated-label"]')
			expect(label.text()).toBe("Updated 0 min ago")
			expect(label.classes()).toContain("text-gray-500")

			// eleven minutes pass; the next 1s store tick re-evaluates the label
			clock.mockReturnValue(now + 11 * 60000)
			usePOSShiftStore().currentTime = "09:11:00"
			await flushPromises()

			const stale = wrapper.find('[data-test="updated-label"]')
			expect(stale.text()).toBe("Updated 11 min ago")
			expect(stale.classes()).toContain("text-amber-600")
		} finally {
			clock.mockRestore()
		}
	})

	it("shows error with retry when the first load fails", async () => {
		const { wrapper, entry } = mountDashboard()
		entry.resource.error = "boom"
		entry.resource.loading = false
		await flushPromises()
		expect(wrapper.find('[data-test="error-state"]').exists()).toBe(true)
		expect(wrapper.text()).toContain("Could not load the dashboard.")

		entry.resource.reload.mockClear()
		await wrapper.find('[data-test="error-state"] button').trigger("click")
		expect(entry.resource.reload).toHaveBeenCalledTimes(1)
	})

	it("keeps the last snapshot visible and flags it on a failed refresh", async () => {
		const { wrapper, entry } = await mountWithData()
		expect(wrapper.find('[data-test="error-alert"]').exists()).toBe(false)

		entry.resource.error = "boom"
		await flushPromises()
		expect(wrapper.find('[data-test="error-alert"]').exists()).toBe(true)
		// cached figures still on screen
		expect(wrapper.find('[data-test="kpi-grid"]').exists()).toBe(true)
		expect(wrapper.text()).toContain("150000 IDR")
	})

	it("keeps KPIs and sections visible with a notice when the shift has no sales", async () => {
		const { wrapper } = await mountWithData({
			sales_count: 0,
			returns_count: 0,
			returns_total: 0,
			recent: [],
			hourly: [],
			items: [],
		})
		expect(wrapper.find('[data-test="empty-notice"]').exists()).toBe(true)
		expect(wrapper.text()).toContain(
			"No sales yet in this shift. Sales appear here after the first invoice.",
		)
		expect(wrapper.find('[data-test="kpi-grid"]').exists()).toBe(true)
	})

	it("re-fetches from the toolbar refresh button only", async () => {
		const { wrapper, entry } = await mountWithData()
		expect(entry.resource.reload).toHaveBeenCalledTimes(1)

		entry.resource.reload.mockClear()
		await wrapper.find('[data-test="refresh-button"]').trigger("click")
		expect(entry.resource.reload).toHaveBeenCalledTimes(1)
	})

	it("fetches nothing without an opening shift", () => {
		const { wrapper, entry } = mountDashboard({ openingShift: "" })
		expect(entry.resource.reload).not.toHaveBeenCalled()
		expect(wrapper.text()).toContain("No open shift for this session.")
		wrapper.unmount()
	})
})

describe("ShiftDashboard period mode", () => {
	// One week of buckets keyed by posting day; same field names as the shift
	// payload plus `period`, minus shift-scoped fields (opening_shift etc.).
	const PERIOD = {
		pos_profile: "Kasir 1",
		company_currency: "IDR",
		generated_at: "2026-09-20 09:00:00",
		period: { from_date: "2026-09-13", to_date: "2026-09-20" },
		net_sales: 300000,
		sales_count: 6,
		returns_count: 1,
		returns_total: 20000,
		average_sale: 50000,
		total_qty: 12,
		total_discount: 4000,
		payments: [
			{ mode_of_payment: "Cash", amount: 180000, is_cash: true, configured: true },
			{ mode_of_payment: "QRIS", amount: 120000, is_cash: false, configured: true },
		],
		total_cash: 180000,
		total_non_cash: 120000,
		methods_grand_total: 300000,
		items: [
			{ item_code: "I1", item_name: "Kopi Susu", qty: 6, base_net_amount: 120000 },
		],
		hourly: [
			{ start: "2026-09-13 00:00:00", net_sales: 100000, sales_count: 2, is_current: false },
			{ start: "2026-09-14 00:00:00", net_sales: 200000, sales_count: 4, is_current: true },
		],
		recent: [
			{
				name: "INV-2026-0009",
				posting_dt: "2026-09-14 08:15:00",
				is_return: false,
				outstanding_amount: 0,
				amount: 50000,
				payment_mode: "Cash",
			},
		],
	}

	async function mountPeriodData(overrides = {}) {
		const { wrapper } = mountDashboard({ posProfile: "P1" })
		await wrapper.find('[data-test="mode-chip-today"]').trigger("click")
		const entry = periodDashboardResource()
		entry.resource.data = { ...PERIOD, ...overrides }
		entry.resource.loading = false
		await flushPromises()
		return wrapper
	}

	it("defaults to period mode with today's window when there is no shift", async () => {
		const { wrapper } = mountDashboard({ openingShift: "", posProfile: "P1" })
		const periodEntry = periodDashboardResource()
		expect(periodEntry.resource.reload).toHaveBeenCalledTimes(1)
		expect(dashboardResource().resource.reload).not.toHaveBeenCalled()
		expect(periodEntry.opts.makeParams()).toEqual({
			pos_profile: "P1",
			from_date: expect.any(String),
			to_date: expect.any(String),
		})

		periodEntry.resource.data = PERIOD
		periodEntry.resource.loading = false
		await flushPromises()
		// no shift chip exists; the period header carries preset + dates
		expect(wrapper.find('[data-test="chip-shift"]').exists()).toBe(false)
		expect(wrapper.find('[data-test="chip-period"]').text()).toBe("Today")
		expect(wrapper.find('[data-test="chip-period-range"]').text()).toContain(
			"D:2026-09-20",
		)
		wrapper.unmount()
	})

	it("renders period chips and swaps the cash card to Cash Payments", async () => {
		const wrapper = await mountPeriodData()
		const chips = wrapper.findAll('[data-test="mode-chips"] button')
		expect(chips.map((c) => c.text())).toEqual([
			"This Shift",
			"Today",
			"Yesterday",
			"Last 7 Days",
			"This Month",
			"This Year",
			"Custom Range",
		])
		expect(wrapper.find('[data-test="chip-period"]').text()).toBe("Today")

		const cash = wrapper.find('[data-test="kpi-cash"]')
		expect(cash.text()).toContain("Cash Payments")
		expect(cash.text()).toContain("180000 IDR")
		expect(cash.text()).not.toContain("Cash in Drawer")
		expect(cash.text()).not.toContain("Before expenses")
		// KPIs come from the period payload
		expect(wrapper.find('[data-test="kpi-net-sales"]').text()).toContain(
			"300000 IDR",
		)
		wrapper.unmount()
	})

	it("renders day-bucket labels and hides the drawer annotation", async () => {
		const wrapper = await mountPeriodData()
		expect(wrapper.find('[data-test="hourly-chart"]').text()).toContain("13/09")
		expect(wrapper.find('[data-test="hourly-chart"]').text()).toContain("14/09")

		const cashRow = wrapper.findAll('[data-test="payment-row"]')[0]
		expect(cashRow.find('[data-test="drawer-note"]').exists()).toBe(false)
		// payments themselves still render with their share
		expect(cashRow.text()).toContain("60%")
		wrapper.unmount()
	})

	it("refetches the right endpoint when the mode switches", async () => {
		const { wrapper, entry } = mountDashboard({ posProfile: "P1" })
		expect(entry.resource.reload).toHaveBeenCalledTimes(1)
		expect(periodDashboardResource().resource.reload).not.toHaveBeenCalled()

		const chip = (value) => wrapper.find(`[data-test="mode-chip-${value}"]`)
		await chip("today").trigger("click")
		await flushPromises()
		expect(periodDashboardResource().resource.reload).toHaveBeenCalledTimes(1)
		expect(entry.resource.reload).toHaveBeenCalledTimes(1)

		await chip("shift").trigger("click")
		await flushPromises()
		expect(entry.resource.reload).toHaveBeenCalledTimes(2)
		expect(periodDashboardResource().resource.reload).toHaveBeenCalledTimes(1)
		wrapper.unmount()
	})

	it("applies a custom range once both dates are set", async () => {
		const { wrapper } = mountDashboard({ posProfile: "P1" })
		const periodEntry = periodDashboardResource()
		await wrapper.find('[data-test="mode-chip-custom"]').trigger("click")
		await flushPromises()
		// incomplete custom range: nothing fetched, prompt shown, inputs out
		expect(periodEntry.resource.reload).not.toHaveBeenCalled()
		expect(wrapper.find('[data-test="pick-dates-notice"]').exists()).toBe(true)

		await wrapper.find('[data-test="period-from"]').setValue("2026-09-13")
		await flushPromises()
		expect(periodEntry.resource.reload).not.toHaveBeenCalled() // still missing "to"

		await wrapper.find('[data-test="period-to"]').setValue("2026-09-20")
		await flushPromises()
		expect(periodEntry.resource.reload).toHaveBeenCalledTimes(1)
		expect(periodEntry.opts.makeParams()).toEqual({
			pos_profile: "P1",
			from_date: "2026-09-13",
			to_date: "2026-09-20",
		})
		wrapper.unmount()
	})
})
