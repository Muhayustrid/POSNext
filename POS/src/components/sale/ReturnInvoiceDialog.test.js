// @vitest-environment jsdom
//
// Refund code gate (per-Company switch): the return dialog asks get_status
// with the ORIGINAL INVOICE's company, verifies codes with kind "refund",
// and the submit payload carries refund_confirmation_code (never the old
// discount_confirmation_code — the server rejects discount codes on returns).
import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { defineComponent, reactive } from "vue"

const callMock = vi.hoisted(() => vi.fn(async () => ({})))

vi.mock("@/utils/apiWrapper", () => ({ call: callMock }))

vi.mock("@/composables/useOfflineStatus", () => ({
	useOfflineStatus: () => ({ isOffline: { value: false } }),
}))

vi.mock("@/composables/useShiftSchedule", () => ({
	scheduleBlockingNow: () => false,
}))

vi.mock("@/utils/offline/workerClient", () => ({
	offlineWorker: new Proxy(
		{},
		{ get: (target, prop) => (prop in target ? target[prop] : vi.fn(async () => ({}))) },
	),
}))

vi.mock("@/composables/useToast", () => ({
	useToast: () => ({
		showSuccess: vi.fn(),
		showWarning: vi.fn(),
		showInfo: vi.fn(),
		showError: vi.fn(),
	}),
}))

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
		template: `<div><slot name="body" /><slot name="body-content" /><slot name="actions" /></div>`,
	})
	const stub = defineComponent({ name: "FrappeUIStub", render: () => null })
	return {
		Button,
		Dialog,
		FeatherIcon: stub,
		Input: stub,
		TextInput: stub,
		call: vi.fn(async () => ({})),
		createResource: (opts) => {
			const reload = vi.fn(async () => ({}))
			const r = reactive({
				loading: false,
				data: null,
				error: null,
				reload,
				fetch: reload,
				submit: reload,
				reset: vi.fn(),
				update: vi.fn(),
			})
			r.opts = opts
			return r
		},
	}
})

vi.mock("@/components/common/PosDialogShell.vue", () => ({
	default: defineComponent({
		name: "PosDialogShell",
		props: {
			title: { type: String, default: "" },
			size: { type: String, default: "md" },
		},
		template: `<div><slot name="toolbar" /><slot /><slot name="actions" /></div>`,
	}),
}))

import ReturnInvoiceDialog from "./ReturnInvoiceDialog.vue"

/**
 * Mount with modelValue open; the preselected invoice drives the dialog
 * through checkValidityAndOpenModal → fetchInvoiceResource (a mocked
 * createResource whose fetch resolves nothing), so we set the internal
 * originalInvoice/preparedReturnDoc via the component's own API surface
 * where possible. To reach the gate logic without reproducing the whole
 * prepare flow, tests mount and drive wrapper.vm state directly.
 */
async function mountDialog(props = {}) {
	const pinia = createPinia()
	setActivePinia(pinia)
	const wrapper = mount(ReturnInvoiceDialog, {
		props: {
			modelValue: false,
			posProfile: "Outlet 1",
			posOpeningShift: "SHIFT-001",
			currency: "IDR",
			...props,
		},
		global: {
			plugins: [pinia],
			config: { globalProperties: { __: globalThis.__ } },
		},
	})
	await wrapper.setProps({ modelValue: true })
	await flushPromises()
	return wrapper
}

const preparedDoc = {
	doctype: "POS Invoice",
	company: "Original Co",
	customer: "CUST-1",
	return_against: "INV-001",
	sales_team: [],
	items: [
		{
			item_code: "ITEM-1",
			item_name: "Kopi",
			rate: 10000,
			rate_with_tax: 11000,
			remaining_qty: 2,
			original_qty: 2,
			pos_invoice_item: "ROW-1",
		},
	],
	_original_invoice: {
		payments: [{ mode_of_payment: "Cash", amount: 22000 }],
		paid_amount: 22000,
		outstanding_amount: 0,
	},
}

async function mountWithInvoice(props = {}) {
	const wrapper = await mountDialog(props)
	await flushPromises()
	// Only claim canCreateReturn when an invoice is actually set up.
	if (props?.preselectedCompany) return wrapper
	await flushPromises()
	// Simulate the fetchInvoiceResource.onSuccess outcome (the mocked
	// resource's fetch is a no-op, so seed the state it would set).
	wrapper.vm.preparedReturnDoc = preparedDoc
	wrapper.vm.originalInvoice = {
		name: "INV-001",
		customer: "CUST-1",
		company: "Original Co",
		docstatus: 1,
		is_return: 0,
		payments: [{ mode_of_payment: "Cash", amount: 22000 }],
		grand_total: 22000,
		paid_amount: 22000,
		outstanding_amount: 0,
	}
	wrapper.vm.returnItems = [
		{
			name: "ROW-1",
			item_code: "ITEM-1",
			item_name: "Kopi",
			quantity: 2,
			return_qty: 2,
			selected: false,
			rate: 10000,
			rate_with_tax: 11000,
		},
	]
	await flushPromises()
	return wrapper
}

beforeEach(() => {
	vi.clearAllMocks()
	callMock.mockImplementation(async () => ({}))
})

describe("ReturnInvoiceDialog refund code gate", () => {
	it("hides the code input and enables submit when refund_code_required is false", async () => {
		callMock.mockImplementation(async (method) => {
			if (method === "pos_next.api.discount_code.get_status") {
				return { enabled: true, discount_code_required: true, refund_code_required: false }
			}
			return {}
		})

		const wrapper = await mountWithInvoice()
		// Select an item so the code section would render if the gate were on.
		// Credit-sale return (no cash refund validation) isolates the gate's
		// effect on canCreateReturn from payment-form validity.
		wrapper.vm.isOriginalCreditSale = true
		wrapper.vm.returnItems[0].selected = true
		await flushPromises()

		expect(wrapper.find('input[maxlength="20"]').exists()).toBe(false)
		expect(wrapper.vm.refundCodeRequired).toBe(false)
		// Gate off = submit is not blocked by the (absent) code check
		expect(wrapper.vm.canCreateReturn).toBe(true)
	})

	it("keeps refundCodeRequired true (fail closed) when get_status rejects", async () => {
		// First a successful "gate off" answer, then a fetch for the same
		// company that REJECTS — invoked directly (not via the company
		// watcher, which resets the flag first) so the catch branch itself
		// must flip the false state back to true.
		let failNext = false
		callMock.mockImplementation(async (method) => {
			if (method === "pos_next.api.discount_code.get_status") {
				if (failNext) throw new Error("server down")
				return { refund_code_required: false }
			}
			return {}
		})

		const wrapper = await mountWithInvoice()
		expect(wrapper.vm.refundCodeRequired).toBe(false)

		failNext = true
		await wrapper.vm.fetchRefundGateStatus()
		await flushPromises()

		expect(wrapper.vm.refundCodeRequired).toBe(true)
	})

	it("never calls get_status with an empty company", async () => {
		const wrapper = await mountDialog()
		await flushPromises()

		// Invoice not loaded yet: no request may go out (fail closed instead).
		const statusCalls = callMock.mock.calls.filter(
			([m]) => m === "pos_next.api.discount_code.get_status",
		)
		expect(statusCalls).toHaveLength(0)
	})

	it("applies only the response matching the current company (stale responses lose)", async () => {
		// Company-keyed answers; the first (slow) response for Outlet Co would
		// wrongly lock the gate again after Original Co answered "not required".
		let resolveOutlet
		callMock.mockImplementation(async (method, params) => {
			if (method === "pos_next.api.discount_code.get_status") {
				if (params.company === "Outlet Co") {
					return new Promise((resolve) => {
						resolveOutlet = () => resolve({ refund_code_required: true })
					})
				}
				if (params.company === "Original Co") {
					return { refund_code_required: false }
				}
			}
			return {}
		})

		const wrapper = await mountWithInvoice({ preselectedCompany: "Outlet Co" })
		// Seed the pre-company state, then swap to the real invoice company.
		wrapper.vm.originalInvoice = { name: "INV-OLD", company: "Outlet Co" }
		await flushPromises()
		expect(wrapper.vm.refundCodeRequired).toBe(true)

		wrapper.vm.originalInvoice = {
			name: "INV-001",
			company: "Original Co",
			customer: "CUST-1",
			payments: [],
			grand_total: 11000,
			paid_amount: 11000,
			outstanding_amount: 0,
		}
		await flushPromises()
		expect(wrapper.vm.refundCodeRequired).toBe(false)

		// The slow Outlet Co response arrives late — it must not win.
		resolveOutlet()
		await flushPromises()
		expect(wrapper.vm.refundCodeRequired).toBe(false)
	})

	it("clears a verified code when the invoice company changes", async () => {
		callMock.mockImplementation(async (method, params) => {
			if (method === "pos_next.api.discount_code.get_status") {
				return { refund_code_required: true }
			}
			if (method === "pos_next.api.discount_code.check_code") {
				return { valid: true }
			}
			return {}
		})

		const wrapper = await mountWithInvoice()
		wrapper.vm.refundCode = "REFUND12"
		await wrapper.vm.verifyRefundCode()
		expect(wrapper.vm.refundCodeVerified).toBe(true)

		// Swap to an invoice from another outlet
		wrapper.vm.originalInvoice = {
			name: "INV-002",
			company: "Other Co",
			customer: "CUST-1",
			payments: [],
			grand_total: 11000,
			paid_amount: 11000,
			outstanding_amount: 0,
		}
		await flushPromises()

		expect(wrapper.vm.refundCode).toBe("")
		expect(wrapper.vm.refundCodeVerified).toBe(false)
		expect(wrapper.vm.refundCodeError).toBe("")
		expect(wrapper.vm.refundCodeRequired).toBe(true)
	})

	it("shows the fail-closed code input with maxlength 20 when the gate is on", async () => {
		callMock.mockImplementation(async (method) => {
			if (method === "pos_next.api.discount_code.get_status") {
				return { enabled: true, discount_code_required: true, refund_code_required: true }
			}
			return {}
		})

		const wrapper = await mountWithInvoice()
		wrapper.vm.returnItems[0].selected = true
		await flushPromises()

		const input = wrapper.find('input[maxlength="20"]')
		expect(input.exists()).toBe(true)
		expect(wrapper.vm.refundCodeRequired).toBe(true)
	})

	it("re-asks get_status with the original invoice's company when it loads", async () => {
		callMock.mockImplementation(async (method) => {
			if (method === "pos_next.api.discount_code.get_status") {
				return { enabled: true, discount_code_required: true, refund_code_required: true }
			}
			return {}
		})

		const wrapper = await mountDialog()
		await flushPromises()

		// Gate off while no invoice is loaded: no get_status may go out at all
		// (fix 2 — never call with an empty company; fail closed instead).
		const statusCalls = callMock.mock.calls.filter(
			([m]) => m === "pos_next.api.discount_code.get_status",
		)
		expect(statusCalls).toHaveLength(0)

		// Once the invoice loads, the gate is re-checked with its company.
		wrapper.vm.preparedReturnDoc = preparedDoc
		wrapper.vm.originalInvoice = {
			name: "INV-001",
			company: "Original Co",
			customer: "CUST-1",
			payments: [],
			grand_total: 11000,
			paid_amount: 11000,
			outstanding_amount: 0,
		}
		await flushPromises()

		const statusCallsAfter = callMock.mock.calls.filter(
			([m]) => m === "pos_next.api.discount_code.get_status",
		)
		expect(statusCallsAfter.length).toBeGreaterThanOrEqual(1)
		const lastStatusCall = statusCallsAfter.pop()
		expect(lastStatusCall[1]).toMatchObject({ company: "Original Co", pos_profile: "Outlet 1" })
	})

	it("verifies the code with kind 'refund'", async () => {
		callMock.mockImplementation(async (method) => {
			if (method === "pos_next.api.discount_code.get_status") {
				return { enabled: true, discount_code_required: true, refund_code_required: true }
			}
			if (method === "pos_next.api.discount_code.check_code") {
				return { valid: true }
			}
			return {}
		})

		const wrapper = await mountWithInvoice()
		wrapper.vm.refundCode = "REFUND12"
		await wrapper.vm.verifyRefundCode()

		const checkCall = callMock.mock.calls.find(
			([m]) => m === "pos_next.api.discount_code.check_code",
		)
		expect(checkCall).toBeTruthy()
		expect(checkCall[1]).toMatchObject({
			code: "REFUND12",
			company: "Original Co",
			kind: "refund",
		})
		expect(wrapper.vm.refundCodeVerified).toBe(true)
	})

	it("sends refund_confirmation_code (not discount_confirmation_code) in the return payload", async () => {
		callMock.mockImplementation(async (method) => {
			if (method === "pos_next.api.discount_code.get_status") {
				return { enabled: true, discount_code_required: true, refund_code_required: true }
			}
			if (method === "pos_next.api.discount_code.check_code") {
				return { valid: true }
			}
			return {}
		})

		const wrapper = await mountWithInvoice()
		wrapper.vm.returnItems[0].selected = true
		wrapper.vm.refundCode = "REFUND12"
		wrapper.vm.refundCodeVerified = true
		await flushPromises()

		// The mocked resource is inert; call its makeParams to inspect the payload.
		const invoiceData = JSON.parse(wrapper.vm.createReturnResource.opts.makeParams().invoice)
		expect(invoiceData.refund_confirmation_code).toBe("REFUND12")
		expect(invoiceData.discount_confirmation_code).toBeUndefined()
	})

	describe("ReturnInvoiceDialog shift badge and filter", () => {
		it("renders shift badge on returnable invoices and filters by selected shift", async () => {
			const wrapper = await mountDialog()
			await flushPromises()

			wrapper.vm.invoiceList = [
				{
					name: "INV-001",
					customer_name: "Customer A",
					grand_total: 15000,
					posting_date: "2026-10-23",
					status: "Paid",
					shift_slot: "SHIFT 1",
				},
				{
					name: "INV-002",
					customer_name: "Customer B",
					grand_total: 25000,
					posting_date: "2026-10-23",
					status: "Paid",
					shift_slot: "SHIFT 2",
				},
			]
			await flushPromises()

			const badges = wrapper.findAll('[data-test="shift-badge"]')
			expect(badges.length).toBe(2)
			expect(badges[0].text()).toBe("SHIFT 1")
			expect(badges[0].classes()).toContain("rounded-full")
			expect(badges[0].classes()).toContain("ring-1")
			expect(badges[0].classes()).toContain("ring-inset")

			const filterChips = wrapper.findAll('[data-test="shift-filter"] button')
			expect(filterChips.length).toBe(3) // All shifts + SHIFT 1 + SHIFT 2

			// Click SHIFT 2
			const shift2Btn = filterChips.find((b) => b.text().includes("SHIFT 2"))
			await shift2Btn.trigger("click")
			await flushPromises()

			const visibleBadges = wrapper.findAll('[data-test="shift-badge"]')
			expect(visibleBadges.length).toBe(1)
			expect(visibleBadges[0].text()).toBe("SHIFT 2")

			// Click All shifts
			await filterChips[0].trigger("click")
			await flushPromises()
			expect(wrapper.findAll('[data-test="shift-badge"]').length).toBe(2)
		})
	})
})
