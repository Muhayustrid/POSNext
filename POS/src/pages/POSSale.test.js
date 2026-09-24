/**
 * @vitest-environment jsdom
 *
 * COR-FE-01 regression: a double-tap on "Complete Payment" while offline used
 * to emit `payment-completed` twice before the dialog unmounted, and each emit
 * ran handlePaymentCompleted to completion — two invoice_queue rows with two
 * different offline_ids, which the server dedupe cannot collapse on replay.
 * The checkout handler must treat concurrent completion attempts as one
 * submission, and the offline enqueue must hold cartStore.isSubmitting (the
 * PaymentDialog's :is-submitting prop) for the duration of the queue save.
 *
 * POSSale is mounted with every dialog/panel stubbed: the flow under test is
 * the page's own handler, driven exactly the way the real dialog drives it
 * (two `payment-completed` emits with no await in between, like a double-tap).
 */
import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { defineComponent, reactive } from "vue"

const resources = vi.hoisted(() => ({ instances: [] }))

vi.hoisted(() => {
	// jsdom has no matchMedia; usePWAInstall reads it in an onMounted hook.
	if (typeof window !== "undefined" && !window.matchMedia) {
		window.matchMedia = () => ({
			matches: false,
			addEventListener() {},
			removeEventListener() {},
			addListener() {},
			removeListener() {},
		})
	}
	// The app installs __() as a global property; some modules call it at
	// module-evaluation time, so this must be hoisted above the imports.
	globalThis.__ = (message, replacements = []) => {
		if (!Array.isArray(replacements) || !replacements.length) return message
		let out = message
		for (const [i, v] of replacements.entries())
			out = out.split(`{${i}}`).join(String(v))
		return out
	}
})

// Offline worker: every method becomes a resolved no-op except the invoice
// queue save, which hands out a distinct offline_id per call — exactly what
// makes the double-queue rows un-deducable server-side.
const offlineWorkerMock = vi.hoisted(() => {
	let seq = 0
	const saveOfflineInvoice = vi.fn(async (data) => ({
		success: true,
		offline_id: data?.offline_id || `pos_offline_test_${++seq}`,
	}))
	const worker = {
		saveOfflineInvoice,
		getOfflineInvoiceCount: vi.fn(async () => saveOfflineInvoice.mock.calls.length),
	}
	return {
		saveOfflineInvoice,
		offlineWorker: new Proxy(worker, {
			get: (t, prop) => (prop in t ? t[prop] : vi.fn(async () => ({}))),
		}),
	}
})

vi.mock("@/utils/offline/workerClient", () => ({
	offlineWorker: offlineWorkerMock.offlineWorker,
}))

vi.mock("@/utils/apiWrapper", () => ({
	call: vi.fn(async () => ({})),
}))

vi.mock("@/composables/useShiftSchedule", () => ({
	scheduleBlockingNow: () => false,
	scheduleEnforcedNow: () => false,
}))

vi.mock("@/utils/queue/queueNumber", () => ({
	acquireQueueNumber: vi.fn(async () => ({ queue_number: 1, date: "2026-09-22" })),
}))

vi.mock("@/data/session", () => ({
	sessionUser: () => "user",
	session: reactive({ logout: { loading: false, submit: vi.fn(async () => {}) } }),
}))

vi.mock("@/data/user", () => ({
	userData: reactive({
		userId: "user",
		fullName: "Kasir",
		userImage: null,
		refresh() {},
		getDisplayName() {
			return this.fullName || "User"
		},
		getImageUrl() {
			return this.userImage
		},
		getInitials() {
			return "K"
		},
	}),
	userResource: { fetch: vi.fn(), loading: false, data: null },
	useUserData: () => ({
		userName: { value: "Kasir" },
		userImage: { value: null },
		userInitials: { value: "K" },
		userId: { value: "user" },
		refresh: vi.fn(),
	}),
}))

vi.mock("@/utils/qzTray", () => ({
	qzConnected: { value: false },
	qzConnecting: { value: false },
	qzCertStatus: { value: "unknown" },
	getSavedPrinterName: () => null,
	savePrinterName: vi.fn(),
	connect: vi.fn(async () => {}),
	disconnect: vi.fn(async () => {}),
	findPrinters: vi.fn(async () => []),
	printHTML: vi.fn(async () => {}),
}))

vi.mock("frappe-ui", async () => {
	const { reactive } = await import("vue")
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
			const reload = vi.fn(async () => {
				r.loading = true
				return {}
			})
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
			resources.instances.push({ url: opts?.url, opts, resource: r })
			return r
		},
	}
})

import { shiftState } from "@/composables/useShift"
import { usePOSCartStore } from "@/stores/posCart"
import { usePOSShiftStore } from "@/stores/posShift"
import { usePOSSyncStore } from "@/stores/posSync"
import { usePOSUIStore } from "@/stores/posUI"
import POSSale from "./POSSale.vue"

const CART_ITEM = {
	item_code: "ITEM-1",
	item_name: "Kopi Susu",
	item_group: "Makanan",
	quantity: 1,
	qty: 1,
	rate: 25000,
	amount: 25000,
	uom: "Nos",
	stock_uom: "Nos",
	conversion_factor: 1,
	is_free_item: false,
	has_serial_no: 0,
	is_resolved_barcode: false,
}

// Fixed payload a real completePayment() would emit for a fully-paid cash sale.
const PAYMENT_PAYLOAD = {
	payments: [{ mode_of_payment: "Cash", amount: 25000, type: "Cash" }],
	change_amount: 0,
	is_partial_payment: false,
	paid_amount: 25000,
	outstanding_amount: 0,
	sales_team: null,
	delivery_date: null,
	write_off_amount: 0,
	is_write_off: false,
	receivable_account: null,
	is_credit_sale: false,
}

// Emits payment-completed on click, the same event the real dialog emits.
const PaymentDialogStub = defineComponent({
	name: "PaymentDialog",
	props: { modelValue: { type: Boolean, default: false } },
	emits: ["payment-completed", "update:modelValue"],
	template: `<button data-test="complete-payment" @click="$emit('payment-completed', PAYMENT_PAYLOAD)"></button>`,
	setup() {
		return { PAYMENT_PAYLOAD }
	},
})

const ALL_DIALOG_STUBS = {
	PaymentDialog: PaymentDialogStub,
	ShiftClosingDialog: true,
	ShiftOpeningDialog: true,
	ClearCacheOverlay: true,
	SessionLockScreen: true,
	POSFooter: true,
	POSHeader: true,
	POSMenuDialog: true,
	BatchSerialDialog: true,
	CouponDialog: true,
	CreateCustomerDialog: true,
	CustomerDialog: true,
	DraftInvoicesDialog: true,
	InvoiceCart: true,
	InvoiceHistoryDialog: true,
	ShiftHistoryDialog: true,
	ItemSelectionDialog: true,
	ItemsSelector: true,
	OffersDialog: true,
	OfflineInvoicesDialog: true,
	PackageSelectionDialog: true,
	ReturnInvoiceDialog: true,
	InvoiceDetailDialog: true,
	LoadingSpinner: true,
}

let cartStore
let offlineStore
let uiStore
let wrapper

async function mountPOS() {
	const pinia = createPinia()
	setActivePinia(pinia)

	const shiftStore = usePOSShiftStore()
	shiftStore.checkShift = vi.fn(async () => true)

	cartStore = usePOSCartStore()
	offlineStore = usePOSSyncStore()
	uiStore = usePOSUIStore()

	wrapper = mount(POSSale, {
		global: {
			plugins: [pinia],
			config: { globalProperties: { __: globalThis.__ } },
			stubs: ALL_DIALOG_STUBS,
		},
	})
	await flushPromises()

	// Seed only after the mount-time initPOS() has settled: the first mount
	// runs setDefaultCustomer(), which would wipe a customer set before it.
	shiftState.value = {
		pos_opening_shift: {
			name: "POS-OPEN-1",
			period_start_date: "2026-09-22 08:00:00",
		},
		pos_profile: {
			name: "Profile 1",
			company: "Test Co",
			currency: "IDR",
			warehouse: "WH 1",
			customer: null,
		},
		company: "Test Co",
		isOpen: true,
		_initialElapsedMs: 0,
		_receivedAt: Date.now(),
		_serverNowMs: Date.now(),
	}
	cartStore.posProfile = "Profile 1"
	cartStore.posOpeningShift = "POS-OPEN-1"
	cartStore.customer = { name: "CUST-1", customer_name: "Walk-in Customer" }
	cartStore.invoiceItems = [{ ...CART_ITEM }]
	offlineStore.isOffline = true
	uiStore.showPaymentDialog = true
	await flushPromises()
	return wrapper
}

beforeEach(() => {
	offlineWorkerMock.saveOfflineInvoice.mockClear()
	resources.instances.length = 0
})

describe("offline checkout double-tap guard (COR-FE-01)", () => {
	it("queues offline invoice only once when payment-completed fires twice in a row", async () => {
		await mountPOS()

		// Double-tap: both clicks dispatch before any async work settles.
		const first = wrapper.find('[data-test="complete-payment"]').trigger("click")
		const second = wrapper.find('[data-test="complete-payment"]').trigger("click")
		await Promise.all([first, second])
		await flushPromises()

		expect(offlineWorkerMock.saveOfflineInvoice).toHaveBeenCalledTimes(1)
	})

	it("holds isSubmitting while the offline enqueue is in flight, then releases it", async () => {
		let release
		const gate = new Promise((resolve) => {
			release = resolve
		})
		offlineWorkerMock.saveOfflineInvoice.mockImplementationOnce(
			() => gate.then(() => ({ success: true, offline_id: "pos_offline_gated" })),
		)

		await mountPOS()

		const click = wrapper.find('[data-test="complete-payment"]').trigger("click")
		await flushPromises()
		// The enqueue is parked on the gated save: the dialog lock must be held.
		expect(cartStore.isSubmitting).toBe(true)

		release()
		await click
		await flushPromises()

		expect(cartStore.isSubmitting).toBe(false)
		expect(offlineWorkerMock.saveOfflineInvoice).toHaveBeenCalledTimes(1)
	})

	it("still closes the checkout and clears the cart after the queued save", async () => {
		await mountPOS()

		await wrapper.find('[data-test="complete-payment"]').trigger("click")
		await flushPromises()

		expect(uiStore.showPaymentDialog).toBe(false)
		expect(cartStore.invoiceItems).toHaveLength(0)
	})
})
