/**
 * @vitest-environment jsdom
 *
 * COR-FE-13 regression: _upsertPaymentEntry seeded the payment payload's
 * `type` with __("Cash"), so under a translated locale the payload carried
 * e.g. "Tunai" while the server matches the Mode of Payment type literal
 * "Cash" (isCashPaymentMethod also compares against lowercase "cash").
 * The payload must carry the untranslated constant; __() is for labels only.
 *
 * Mount keeps every store real (fresh Pinia) and only stubs frappe-ui, the
 * offline worker and toasts; the audited entry builder is reached the way
 * the component itself reaches it.
 */
import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { defineComponent, reactive } from "vue"

// Locale simulation: under locale id the catalog resolves Cash -> Tunai.
// The payload must not inherit this translation.
vi.hoisted(() => {
	globalThis.__ = (message) => ({ Cash: "Tunai" }[message] ?? message)
})

// Offline worker: every method becomes a resolved no-op.
const offlineWorkerMock = vi.hoisted(() => ({
	offlineWorker: new Proxy(
		{},
		{ get: (target, prop) => (prop in target ? target[prop] : vi.fn(async () => ({}))) },
	),
}))

vi.mock("@/utils/offline/workerClient", () => offlineWorkerMock)

vi.mock("@/utils/apiWrapper", () => ({
	call: vi.fn(async () => ({})),
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
	const { reactive } = await import("vue")
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
			return r
		},
	}
})

import { useDiscountRestrictionStore } from "@/stores/discountRestriction"

import PaymentDialog from "./PaymentDialog.vue"

function mountDialog(props = {}) {
	const pinia = createPinia()
	setActivePinia(pinia)
	return mount(PaymentDialog, {
		props: {
			modelValue: false,
			grandTotal: 50000,
			currency: "IDR",
			posProfile: "Profile 1",
			...props,
		},
		global: {
			plugins: [pinia],
			config: { globalProperties: { __: globalThis.__ } },
		},
	})
}

beforeEach(() => {
	vi.clearAllMocks()
})

describe("payment payload uses untranslated mode constants (COR-FE-13)", () => {
	it("falls back to the 'Cash' constant, never the translated label", async () => {
		const wrapper = mountDialog()
		await flushPromises()

		// Method without a server-side type: the audited fallback branch.
		wrapper.vm._upsertPaymentEntry({ mode_of_payment: "Cash" }, 25000)
		await flushPromises()

		expect(wrapper.vm.paymentEntries).toHaveLength(1)
		expect(wrapper.vm.paymentEntries[0].type).toBe("Cash")
		expect(wrapper.vm.paymentEntries[0].mode_of_payment).toBe("Cash")
	})

	it("keeps a server-provided type untouched", async () => {
		const wrapper = mountDialog()
		await flushPromises()

		wrapper.vm._upsertPaymentEntry(
			{ mode_of_payment: "Card", type: "General" },
			10000,
		)
		await flushPromises()

		expect(wrapper.vm.paymentEntries[0].type).toBe("General")
	})
})

describe("zero grand total (100% discount)", () => {
	it("can complete without any payment row", async () => {
		const wrapper = mountDialog({ grandTotal: 0 })
		await flushPromises()

		expect(wrapper.vm.paymentEntries).toHaveLength(0)
		expect(wrapper.vm.canComplete).toBe(true)
	})

	it("still requires payment when total is above zero", async () => {
		const wrapper = mountDialog({ grandTotal: 5000 })
		await flushPromises()

		expect(wrapper.vm.canComplete).toBe(false)
	})
})

describe("header discount from offer locks the additional discount", () => {
	it("locks (with the offer reason) even when a code is already entered", async () => {
		const wrapper = mountDialog({ headerDiscountFromOffer: true })
		await flushPromises()

		// Unlock the HQ-code half of the lock; the offer lock must still hold.
		useDiscountRestrictionStore().setCode("HQCODE")
		await flushPromises()

		expect(wrapper.vm.additionalDiscountCodeLocked).toBe(false)
		expect(wrapper.vm.additionalDiscountLocked).toBe(true)
		expect(wrapper.vm.additionalDiscountLockedReason).toBe("Locked (offer applied)")
	})

	it("stays unlocked when no offer drives the header and a code is entered", async () => {
		const wrapper = mountDialog({ headerDiscountFromOffer: false })
		await flushPromises()

		useDiscountRestrictionStore().setCode("HQCODE")
		await flushPromises()

		expect(wrapper.vm.additionalDiscountLocked).toBe(false)
		expect(wrapper.vm.additionalDiscountLockedReason).toBe("")
	})
})

describe("header discount from coupon locks the additional discount", () => {
	it("locks (with the coupon reason) even when a code is already entered", async () => {
		const wrapper = mountDialog({ headerDiscountFromCoupon: true })
		await flushPromises()

		// Unlock the HQ-code half of the lock; the coupon lock must still hold.
		useDiscountRestrictionStore().setCode("HQCODE")
		await flushPromises()

		expect(wrapper.vm.additionalDiscountCodeLocked).toBe(false)
		expect(wrapper.vm.additionalDiscountLocked).toBe(true)
		expect(wrapper.vm.additionalDiscountLockedReason).toBe("Locked (coupon applied)")
	})
})
