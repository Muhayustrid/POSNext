/**
 * @vitest-environment jsdom
 */
import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"

// frappe-ui double: Button forwards clicks, FeatherIcon marks its name.
// createResource returns a never-resolving stub — stores must construct but not fetch.
vi.mock("frappe-ui", async () => {
	const { defineComponent, reactive } = await import("vue")
	const Button = defineComponent({
		name: "Button",
		emits: ["click"],
		props: ["disabled", "variant"],
		template: `<button :disabled="disabled" @click="$emit('click')"><slot /></button>`,
	})
	const FeatherIcon = defineComponent({
		name: "FeatherIcon",
		props: ["name"],
		template: `<i :data-icon="name" />`,
	})
	const createResource = (opts) => {
		const r = reactive({ loading: false, data: null, error: null, reload: vi.fn(), fetch: vi.fn() })
		return r
	}
	return {
		Button,
		FeatherIcon,
		createResource,
		createListResource: createResource,
		createDocumentResource: createResource,
		Dialog: defineComponent({ template: "<div><slot /></div>" }),
		LoadingIndicator: defineComponent({ template: "<i />" }),
		call: vi.fn(async () => []),
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

vi.mock("@/utils/invoice", () => ({
	getInvoiceStatusColor: () => "bg-gray-100 text-gray-800",
}))

vi.mock("@/utils/queue/queueNumber", () => ({
	formatQueueNumber: (n) => String(n),
}))

vi.mock("@/composables/useToast", () => ({
	useToast: () => ({ showSuccess: vi.fn(), showError: vi.fn(), showToast: vi.fn(), showWarning: vi.fn() }),
}))

vi.mock("@/composables/useShiftSchedule", () => ({
	scheduleBlockingNow: () => false,
	scheduleEnforcedNow: () => false,
}))

vi.mock("@/utils/offline/offlineState", () => ({
	isOffline: () => false,
}))

vi.mock("@/utils/offline/sync", () => ({
	cacheUnpaidInvoices: vi.fn(),
	getCachedUnpaidInvoices: vi.fn(async () => []),
	cacheUnpaidSummary: vi.fn(),
	getCachedUnpaidSummary: vi.fn(async () => null),
}))

vi.mock("@/utils/logger", () => ({
	logger: { create: () => ({ debug: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() }) },
}))

vi.mock("@/composables/useInvoiceFilters", async () => {
	// Pass-through filtering: identity composable so tests control rows exactly.
	const { computed } = await import("vue")
	return {
		useInvoiceFilters: (invoices) => ({
			filteredInvoices: computed(() =>
				(Array.isArray(invoices.value) ? invoices.value : []).filter(Boolean)
			),
			uniqueCustomers: computed(() => []),
			uniqueProducts: computed(() => []),
			filterStats: computed(() => ({})),
		}),
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

import InvoiceManagement from "./InvoiceManagement.vue"

// invoice: minimal shape for history/returns rows
function invoice(name, overrides = {}) {
	return {
		name,
		customer: `Customer ${name}`,
		customer_name: `Customer ${name}`,
		posting_date: "2026-10-01",
		posting_time: "10:00:00",
		grand_total: 1000,
		paid_amount: 1000,
		status: "Paid",
		...overrides,
	}
}

function makeInvoices(n, overrides = {}) {
	return Array.from({ length: n }, (_, i) => invoice(`INV-${String(i + 1).padStart(4, "0")}`, overrides))
}

function mountComponent(props = {}) {
	const pinia = createPinia()
	setActivePinia(pinia)
	return mount(InvoiceManagement, {
		props: {
			modelValue: true,
			embedded: true,
			posProfile: "POS-1",
			historyInvoices: [],
			draftInvoices: [],
			...props,
		},
		global: {
			plugins: [pinia],
			config: { globalProperties: { __: globalThis.__ } },
		},
	})
}

async function mountOnTab(tab, props = {}) {
	const wrapper = mountComponent(props)
	wrapper.vm.activeTab = tab
	await flushPromises()
	return wrapper
}

const PAGER_TESTID = '[data-test="list-pager"]'

beforeEach(() => {
	// InvoiceManagement mounts on the partial tab; the component fetches unpaid
	// lists via frappe-ui `call` (mocked at module level in the app tests, and
	// not triggered here since the partial tab renders skeletons regardless).
})

describe("InvoiceManagement pager", () => {
	it("shows 20 of 45 history rows with Page 1 of 3 and a working Next", async () => {
		const wrapper = await mountOnTab("history", {
			historyInvoices: makeInvoices(45),
		})

		expect(wrapper.vm.activeTab).toBe("history")
		const rows = wrapper.findAll("tbody tr")
		expect(rows.length).toBe(20)

		const pager = wrapper.find(PAGER_TESTID)
		expect(pager.exists()).toBe(true)
		expect(pager.text()).toContain("Showing 1–20 of 45")
		expect(pager.text()).toContain("Page 1 of 3")

		await pager.find("button[aria-label='Next page']").trigger("click")
		await flushPromises()
		expect(wrapper.vm.historyPager.page).toBe(2)
		const pager2 = wrapper.find(PAGER_TESTID)
		expect(pager2.text()).toContain("Showing 21–40 of 45")
	})

	it("hides the pager when the list has 20 or fewer rows", async () => {
		const wrapper = await mountOnTab("history", {
			historyInvoices: makeInvoices(20),
		})
		expect(wrapper.findAll("tbody tr").length).toBe(20)
		expect(wrapper.find(PAGER_TESTID).exists()).toBe(false)
	})

	it("resets to page 1 when switching tabs", async () => {
		const wrapper = await mountOnTab("history", {
			historyInvoices: makeInvoices(45),
		})
		await wrapper.find(PAGER_TESTID).find("button[aria-label='Next page']").trigger("click")
		await flushPromises()
		expect(wrapper.vm.historyPager.page).toBe(2)

		wrapper.vm.activeTab = "returns"
		await flushPromises()
		expect(wrapper.vm.historyPager.page).toBe(1)
	})

	it("renders drafts with a pager when over 20 and clamps when the list shrinks", async () => {
		const drafts = Array.from({ length: 25 }, (_, i) => ({
			draft_id: `DRAFT-${i + 1}`,
			created_at: "2026-10-01T10:00:00Z",
			items: [{ quantity: 1, rate: 500 }],
		}))
		const wrapper = await mountOnTab("drafts", { draftInvoices: drafts })
		expect(wrapper.findAll("tbody tr").length).toBe(20)
		const pager = wrapper.find(PAGER_TESTID)
		expect(pager.exists()).toBe(true)
		expect(pager.text()).toContain("Page 1 of 2")

		// Deleting drafts down to one page hides the pager and clamps page
		await wrapper.setProps({ draftInvoices: drafts.slice(0, 20) })
		await flushPromises()
		expect(wrapper.vm.draftsPager.page).toBe(1)
		expect(wrapper.find(PAGER_TESTID).exists()).toBe(false)
	})

	it("returns pager pages through the return list", async () => {
		const returns = makeInvoices(45, { is_return: true, status: "Credit Note Issued" })
		const wrapper = await mountOnTab("returns", { historyInvoices: returns })
		expect(wrapper.findAll("tbody tr").length).toBe(20)
		const pager = wrapper.find(PAGER_TESTID)
		expect(pager.text()).toContain("Page 1 of 3")
		await pager.find("button[aria-label='Next page']").trigger("click")
		await flushPromises()
		expect(wrapper.vm.returnsPager.page).toBe(2)
	})
})

describe("InvoiceManagement cashier column", () => {
	it("renders cashier_name in the history table, falling back to owner", async () => {
		const rows = [
			invoice("INV-0001", { cashier_name: "Budi" }),
			invoice("INV-0002", { cashier_name: null, owner: "owner@example.com" }),
			invoice("INV-0003", { cashier_name: null, owner: null }),
		]
		const wrapper = await mountOnTab("history", { historyInvoices: rows })
		const cells = wrapper.findAll("tbody tr").map((r) => r.findAll("td").map((c) => c.text()))
		expect(cells[0][2]).toContain("Budi")
		expect(cells[1][2]).toContain("owner@example.com")
		expect(cells[2][2]).toContain("–")
	})

	it("renders cashier_name in the returns table with owner fallback", async () => {
		const rows = [
			invoice("RET-0001", { is_return: true, cashier_name: "Sari" }),
			invoice("RET-0002", { is_return: true, cashier_name: null, owner: "owner2@example.com" }),
		]
		const wrapper = await mountOnTab("returns", { historyInvoices: rows })
		const cells = wrapper.findAll("tbody tr").map((r) => r.findAll("td").map((c) => c.text()))
		expect(cells[0][2]).toContain("Sari")
		expect(cells[1][2]).toContain("owner2@example.com")
		// header text is just the label (no markup leaked into it)
		expect(wrapper.findAll("thead th").map((th) => th.text())).toContain("Cashier")
	})

	it("shows a cashier line with a user icon on the mobile history card", async () => {
		const rows = [invoice("INV-0001", { cashier_name: "Budi" })]
		const wrapper = await mountOnTab("history", { historyInvoices: rows })
		// ".md\\:hidden" breaks jsdom's selector engine; match the class attr directly
		const card = wrapper
			.findAll("div")
			.find((d) => d.classes().includes("md:hidden"))
		expect(card).toBeTruthy()
		const line = card.find(".text-gray-400")
		expect(line.exists()).toBe(true)
		expect(line.text()).toContain("Budi")
		expect(line.find("[data-icon='user']").exists()).toBe(true)
	})

	it("omits the drafts cashier cell when the draft has neither cashier_name nor owner", async () => {
		const drafts = [
			{ draft_id: "DRAFT-1", created_at: "2026-10-01T10:00:00Z", items: [], cashier_name: "Budi" },
			{ draft_id: "DRAFT-2", created_at: "2026-10-01T11:00:00Z", items: [] },
			{ draft_id: "DRAFT-3", created_at: "2026-10-01T12:00:00Z", items: [], owner: "o@x.com" },
		]
		const wrapper = await mountOnTab("drafts", { draftInvoices: drafts })
		const cells = wrapper.findAll("tbody tr").map((r) => r.findAll("td").map((c) => c.text()))
		expect(cells[0][2]).toContain("Budi")
		expect(cells[1].length).toBe(5) // draft, customer, items, total, actions — no cashier cell
		expect(cells[2][2]).toContain("o@x.com")
	})
})
