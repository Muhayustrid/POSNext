/**
 * @vitest-environment jsdom
 */
// Invoice History date range: the parent only hands over the latest 100
// invoices, so a range must be fetched from the server, not filtered locally.
import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"

const offline = { value: false }
const call = vi.fn()

vi.mock("frappe-ui", async () => {
	const { defineComponent, reactive } = await import("vue")
	const createResource = () => reactive({ loading: false, data: null, reload: vi.fn(), fetch: vi.fn() })
	return {
		Button: defineComponent({
			name: "Button",
			emits: ["click"],
			props: ["disabled", "variant"],
			template: `<button :disabled="disabled" @click="$emit('click')"><slot /></button>`,
		}),
		FeatherIcon: defineComponent({ name: "FeatherIcon", props: ["name"], template: "<i />" }),
		createResource,
		createListResource: createResource,
		createDocumentResource: createResource,
		Dialog: defineComponent({ template: "<div><slot /></div>" }),
		LoadingIndicator: defineComponent({ template: "<i />" }),
		call: (...args) => call(...args),
	}
})
vi.mock("@/composables/useFormatters", () => ({
	useFormatters: () => ({ formatDate: (d) => d, formatTime: (t) => t, formatDateTime: (d) => d }),
}))
vi.mock("@/utils/currency", () => ({
	DEFAULT_CURRENCY: "IDR",
	DEFAULT_LOCALE: "en-US",
	formatCurrency: (amount) => String(amount),
}))
vi.mock("@/utils/invoice", () => ({ getInvoiceStatusColor: () => "" }))
vi.mock("@/utils/queue/queueNumber", () => ({ formatQueueNumber: (n) => String(n) }))
vi.mock("@/composables/useToast", () => ({
	useToast: () => ({ showSuccess: vi.fn(), showError: vi.fn() }),
}))
vi.mock("@/composables/useShiftSchedule", () => ({ scheduleBlockingNow: () => false }))
vi.mock("@/utils/offline/offlineState", () => ({ isOffline: () => offline.value }))
vi.mock("@/utils/offline/sync", () => ({
	cacheUnpaidInvoices: vi.fn(),
	getCachedUnpaidInvoices: vi.fn(async () => []),
	cacheUnpaidSummary: vi.fn(),
	getCachedUnpaidSummary: vi.fn(async () => null),
}))
vi.mock("@/utils/logger", () => ({
	logger: { create: () => ({ debug: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() }) },
}))

globalThis.__ = (message, replacements = []) => {
	let out = message
	for (const [i, v] of (replacements || []).entries()) out = out.split(`{${i}}`).join(String(v))
	return out
}

import { useInvoiceFiltersStore } from "@/stores/invoiceFilters"
import InvoiceManagement from "./InvoiceManagement.vue"

function invoices(prefix, n, posting_date) {
	return Array.from({ length: n }, (_, i) => ({
		name: `${prefix}${String(i + 1).padStart(3, "0")}`,
		customer: "Walk-in",
		customer_name: "Walk-in",
		posting_date,
		posting_time: "10:00:00",
		grand_total: 1000,
		paid_amount: 1000,
		status: "Paid",
		items: [],
	}))
}

// 120 newer invoices (Oct 30-31): more than the 100 the parent passes down
const LATEST = [...invoices("PINV-31-", 63, "2026-10-31"), ...invoices("PINV-30-", 57, "2026-10-30")]
const OCT29 = invoices("PINV-29-", 16, "2026-10-29")

let serverPages // get_invoices responses keyed by `${from_date}|${to_date}|${offset}`

async function mountHistory() {
	const pinia = createPinia()
	setActivePinia(pinia)
	const wrapper = mount(InvoiceManagement, {
		props: {
			modelValue: true,
			embedded: true,
			posProfile: "RS DR SOETARTO KOTA YOGYAKARTA",
			historyInvoices: LATEST.slice(0, 100),
			draftInvoices: [],
		},
		global: { plugins: [pinia], config: { globalProperties: { __: globalThis.__ } } },
	})
	wrapper.vm.activeTab = "history"
	await flushPromises()
	return { wrapper, store: useInvoiceFiltersStore() }
}

const rangeCalls = () => call.mock.calls.filter(([method]) => method === "pos_next.api.invoices.get_invoices")
const rowNames = (wrapper) => wrapper.findAll("tbody tr").map((r) => r.find("td").text().split(" ")[0])

beforeEach(() => {
	offline.value = false
	serverPages = {}
	call.mockReset()
	call.mockImplementation(async (method, args) => {
		if (method !== "pos_next.api.invoices.get_invoices") return []
		return serverPages[`${args.from_date}|${args.to_date}|${args.offset}`] || []
	})
})

describe("Invoice History date range", () => {
	it("fetches an older range from the server instead of filtering the latest 100", async () => {
		serverPages["2026-10-01|2026-10-29|0"] = OCT29
		const { wrapper, store } = await mountHistory()

		store.setDateRange("2026-10-01", "2026-10-29")
		await flushPromises()

		expect(rangeCalls().at(-1)[1]).toMatchObject({
			pos_profile: "RS DR SOETARTO KOTA YOGYAKARTA",
			from_date: "2026-10-01",
			to_date: "2026-10-29",
			limit: 100,
			offset: 0,
		})
		expect(wrapper.text()).not.toContain("No invoices found")
		expect(rowNames(wrapper)).toEqual(OCT29.map((i) => i.name))
		// counter describes what was loaded, not a database total
		expect(wrapper.text()).toContain("16 of 16 loaded invoice(s)")
		expect(wrapper.find('[data-test="history-load-more"]').exists()).toBe(false)
	})

	it("loads older pages of a large range and resets to page 1 on a new range", async () => {
		const big = invoices("PINV-28-", 130, "2026-10-28")
		serverPages["2026-10-01|2026-10-29|0"] = big.slice(0, 100)
		serverPages["2026-10-01|2026-10-29|100"] = big.slice(100)
		const { wrapper, store } = await mountHistory()

		store.setDateRange("2026-10-01", "2026-10-29")
		await flushPromises()
		expect(wrapper.text()).toContain("100 of 100 loaded invoice(s)")
		const more = wrapper.find('[data-test="history-load-more"]')
		expect(more.exists()).toBe(true)

		await more.trigger("click")
		await flushPromises()
		expect(rangeCalls().at(-1)[1]).toMatchObject({ offset: 100 })
		expect(wrapper.find('[data-test="list-pager"]').text()).toContain("of 130")
		expect(wrapper.find('[data-test="history-load-more"]').exists()).toBe(false)

		wrapper.vm.historyPager.next()
		expect(wrapper.vm.historyPager.page).toBe(2)
		serverPages["2026-10-29|2026-10-29|0"] = OCT29
		store.setDateRange("2026-10-29", "2026-10-29")
		await flushPromises()
		expect(wrapper.vm.historyPager.page).toBe(1)
		expect(rowNames(wrapper)).toEqual(OCT29.map((i) => i.name))
	})

	it("ignores a stale response that arrives after a newer range", async () => {
		let releaseOld
		call.mockImplementation(async (method, args) => {
			if (method !== "pos_next.api.invoices.get_invoices") return []
			if (args.from_date === "2026-10-01") return new Promise((r) => (releaseOld = () => r(invoices("OLD-", 5, "2026-10-02"))))
			return OCT29
		})
		const { wrapper, store } = await mountHistory()

		store.setDateRange("2026-10-01", "2026-10-29")
		await flushPromises()
		store.setDateRange("2026-10-29", "2026-10-29")
		await flushPromises()
		releaseOld()
		await flushPromises()

		expect(rowNames(wrapper)).toEqual(OCT29.map((i) => i.name))
	})

	it("offline: filters only the cached list and says so instead of claiming no history", async () => {
		offline.value = true
		const { wrapper, store } = await mountHistory()

		store.setDateRange("2026-10-01", "2026-10-29")
		await flushPromises()

		expect(rangeCalls()).toHaveLength(0)
		expect(wrapper.find('[data-test="history-partial-notice"]').exists()).toBe(true)
	})

	it("without a date filter keeps the parent's latest list and makes no extra request", async () => {
		const { wrapper } = await mountHistory()
		expect(rangeCalls()).toHaveLength(0)
		expect(wrapper.find('[data-test="list-pager"]').text()).toContain("of 100")
	})
})
