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

// IndexedDB draft store: every method becomes a resolved no-op, but saveDraft
// records its payload so the draft round-trip tests can assert exactly what a
// real save would persist.
const draftManagerMock = vi.hoisted(() => ({
	saveDraft: vi.fn(async (data) => ({ draft_id: "DRAFT-SAVED", ...data })),
	updateDraft: vi.fn(async (draftId, data) => ({ draft_id: draftId, ...data })),
	getAllDrafts: vi.fn(async () => []),
	getDraftById: vi.fn(async () => null),
	getDraftsCount: vi.fn(async () => 0),
	deleteDraft: vi.fn(async () => {}),
	clearAllDrafts: vi.fn(async () => {}),
}))

// Payload handed to the page by the DraftInvoicesDialog stub on load-draft;
// each test plants the draft it wants to load before clicking.
const draftEmits = vi.hoisted(() => ({ loadDraft: null }))

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

vi.mock("@/utils/draftManager", () => draftManagerMock)

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

// Coupon-shaped discount the CouponDialog would hand to applyDiscountToCart.
const COUPON = { name: "DISKON10RB", code: "DISKON10RB", amount: 10000 }

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

// Emit-only stubs for the dialogs the shift and draft regressions are driven
// through, mirroring how the real dialogs emit into the page's handlers.
const ShiftOpeningDialogStub = defineComponent({
	name: "ShiftOpeningDialog",
	props: { modelValue: { type: Boolean, default: false } },
	emits: ["shift-opened"],
	template: `<button data-test="shift-opened" @click="$emit('shift-opened')"></button>`,
})

const ShiftClosingDialogStub = defineComponent({
	name: "ShiftClosingDialog",
	props: { modelValue: { type: Boolean, default: false } },
	emits: ["shift-closed"],
	template: `<button data-test="shift-closed" @click="$emit('shift-closed')"></button>`,
})

const DraftInvoicesDialogStub = defineComponent({
	name: "DraftInvoicesDialog",
	props: { modelValue: { type: Boolean, default: false } },
	emits: ["load-draft", "drafts-updated"],
	template: `<button data-test="load-draft" @click="$emit('load-draft', draftEmits.loadDraft)"></button>`,
	setup() {
		return { draftEmits }
	},
})

const InvoiceCartSaveStub = defineComponent({
	name: "InvoiceCart",
	emits: ["save-draft"],
	template: `<button data-test="save-draft" @click="$emit('save-draft')"></button>`,
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

async function mountPOS(stubOverrides = {}) {
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
			stubs: { ...ALL_DIALOG_STUBS, ...stubOverrides },
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
	// Real cart mutations keep the incremental cache warm; direct seeding must
	// do the same or discount clamping sees a zero subtotal.
	cartStore.rebuildIncrementalCache()
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

describe("draft load discount isolation (COR-FE-02)", () => {
	it("resets the live cart's coupon and header discount when loading a draft without them", async () => {
		await mountPOS({ DraftInvoicesDialog: DraftInvoicesDialogStub })

		cartStore.applyDiscountToCart({ ...COUPON })
		expect(cartStore.additionalDiscount).toBe(10000)
		expect(cartStore.appliedCoupon).not.toBeNull()

		draftEmits.loadDraft = {
			draft_id: "DRAFT-B",
			items: [{ ...CART_ITEM, item_code: "ITEM-B" }],
			customer: { name: "CUST-2", customer_name: "Budi" },
			buyer_name: "",
		}
		await wrapper.find('[data-test="load-draft"]').trigger("click")
		await flushPromises()

		// The displaced cart's discount must not ride into draft B's cart...
		expect(cartStore.appliedCoupon).toBeNull()
		expect(cartStore.additionalDiscount).toBe(0)
		expect(cartStore.invoiceItems[0].item_code).toBe("ITEM-B")
		// ...and the displaced cart was saved as a draft with its discount intact.
		const displaced = draftManagerMock.saveDraft.mock.calls.at(-1)[0]
		expect(displaced.applied_coupon).toEqual(COUPON)
		expect(displaced.additional_discount).toBe(10000)
	})

	it("restores the coupon and header discount when a discounted draft is reloaded", async () => {
		await mountPOS({
			InvoiceCart: InvoiceCartSaveStub,
			DraftInvoicesDialog: DraftInvoicesDialogStub,
		})

		cartStore.applyDiscountToCart({ ...COUPON })
		await wrapper.find('[data-test="save-draft"]').trigger("click")
		await flushPromises()

		// Saving as draft clears the cart and the payload carries the discount.
		expect(cartStore.isEmpty).toBe(true)
		const payload = draftManagerMock.saveDraft.mock.calls.at(-1)[0]
		expect(payload.applied_coupon).toEqual(COUPON)
		expect(payload.additional_discount).toBe(10000)

		draftEmits.loadDraft = { ...payload }
		await wrapper.find('[data-test="load-draft"]').trigger("click")
		await flushPromises()

		expect(cartStore.appliedCoupon).toEqual(COUPON)
		expect(cartStore.additionalDiscount).toBe(10000)
	})

	it("caps a percentage coupon at its max_amount when restoring a draft", async () => {
		await mountPOS({ DraftInvoicesDialog: DraftInvoicesDialogStub })

		// CouponDialog clamps only at apply time; restoring the draft reruns
		// the raw percentage (10% of 25000 = 2500), which must stay capped.
		const capped = { name: "PCT10MAX", code: "PCT10MAX", percentage: 10, max_amount: 2000 }
		draftEmits.loadDraft = {
			draft_id: "DRAFT-CAP",
			items: [{ ...CART_ITEM, item_code: "ITEM-CAP" }],
			customer: { name: "CUST-2", customer_name: "Budi" },
			buyer_name: "",
			applied_coupon: capped,
			additional_discount: 2000,
		}
		await wrapper.find('[data-test="load-draft"]').trigger("click")
		await flushPromises()

		expect(cartStore.appliedCoupon).toEqual(capped)
		expect(cartStore.additionalDiscount).toBe(2000)
	})

	it("restores a manual header discount from a draft that has no coupon", async () => {
		await mountPOS({ DraftInvoicesDialog: DraftInvoicesDialogStub })

		draftEmits.loadDraft = {
			draft_id: "DRAFT-C",
			items: [{ ...CART_ITEM, item_code: "ITEM-C" }],
			customer: { name: "CUST-2", customer_name: "Budi" },
			buyer_name: "",
			additional_discount: 7000,
		}
		await wrapper.find('[data-test="load-draft"]').trigger("click")
		await flushPromises()

		expect(cartStore.appliedCoupon).toBeNull()
		expect(cartStore.additionalDiscount).toBe(7000)
	})
})

describe("shift boundary cart isolation (COR-FE-03)", () => {
	it("clears the active cart when the shift is closed", async () => {
		await mountPOS({ ShiftClosingDialog: ShiftClosingDialogStub })

		cartStore.applyDiscountToCart({ ...COUPON })
		expect(cartStore.invoiceItems).toHaveLength(1)

		await wrapper.find('[data-test="shift-closed"]').trigger("click")
		await flushPromises()

		expect(cartStore.invoiceItems).toHaveLength(0)
		expect(cartStore.appliedCoupon).toBeNull()
		expect(cartStore.additionalDiscount).toBe(0)
	})

	it("clears the cart when a new shift opens under a different profile", async () => {
		await mountPOS({ ShiftOpeningDialog: ShiftOpeningDialogStub })

		shiftState.value = {
			...shiftState.value,
			pos_profile: { ...shiftState.value.pos_profile, name: "Profile 2" },
		}
		await flushPromises()
		expect(cartStore.posProfile).toBe("Profile 1")

		await wrapper.find('[data-test="shift-opened"]').trigger("click")
		await flushPromises()

		expect(cartStore.posProfile).toBe("Profile 2")
		expect(cartStore.invoiceItems).toHaveLength(0)
	})

	it("keeps the cart when the same profile reopens a shift", async () => {
		await mountPOS({ ShiftOpeningDialog: ShiftOpeningDialogStub })

		await wrapper.find('[data-test="shift-opened"]').trigger("click")
		await flushPromises()

		expect(cartStore.posProfile).toBe("Profile 1")
		expect(cartStore.invoiceItems).toHaveLength(1)
	})
})
