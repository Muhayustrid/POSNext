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
import { usePOSSettingsStore } from "@/stores/posSettings"

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

describe("fullscreen overlay layout and accessibility", () => {
	it("renders fullscreen dialog shell with accessibility attributes when open", async () => {
		const wrapper = mountDialog({ modelValue: true })
		await flushPromises()

		const dialog = wrapper.find('[role="dialog"]')
		expect(dialog.exists()).toBe(true)
		expect(dialog.attributes("aria-modal")).toBe("true")
		expect(dialog.attributes("aria-label")).toBe("Complete Payment")
		expect(dialog.classes()).toContain("fixed")
		expect(dialog.classes()).toContain("inset-0")
		expect(dialog.classes()).toContain("z-[350]")
		expect(dialog.classes()).toContain("rounded-none")
	})

	it("uses Complete Sales Order label when targetDoctype is Sales Order", async () => {
		const wrapper = mountDialog({ modelValue: true, targetDoctype: "Sales Order" })
		await flushPromises()

		const dialog = wrapper.find('[role="dialog"]')
		expect(dialog.attributes("aria-label")).toBe("Complete Sales Order")
	})

	it("emits update:modelValue false on close button click", async () => {
		const wrapper = mountDialog({ modelValue: true })
		await flushPromises()

		const closeButtons = wrapper.findAll('button[aria-label="Close"]')
		expect(closeButtons.length).toBeGreaterThanOrEqual(1)
		await closeButtons[0].trigger("click")

		expect(wrapper.emitted("update:modelValue")).toBeTruthy()
		expect(wrapper.emitted("update:modelValue")[0]).toEqual([false])
	})

	it("disables close buttons and blocks closing when isSubmitting is true", async () => {
		const wrapper = mountDialog({ modelValue: true, isSubmitting: true })
		await flushPromises()

		const closeButtons = wrapper.findAll('button[aria-label="Close"]')
		for (const btn of closeButtons) {
			expect(btn.attributes("disabled")).toBeDefined()
		}

		wrapper.vm.closeDialog()
		expect(wrapper.emitted("update:modelValue")).toBeFalsy()
	})

	it("closes on Escape key press when not submitting", async () => {
		const wrapper = mountDialog({ modelValue: true })
		await flushPromises()

		window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }))
		await flushPromises()

		expect(wrapper.emitted("update:modelValue")).toBeTruthy()
		expect(wrapper.emitted("update:modelValue")[0]).toEqual([false])
	})

	it("does not close on Escape key press when isSubmitting is true", async () => {
		const wrapper = mountDialog({ modelValue: true, isSubmitting: true })
		await flushPromises()

		window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }))
		await flushPromises()

		expect(wrapper.emitted("update:modelValue")).toBeFalsy()
	})
})

describe("Complete Payment relayout (native amount input and quick amounts)", () => {
	const defaultCashMethod = {
		mode_of_payment: "Cash",
		type: "Cash",
		account_type: "Cash",
		default: true,
	}

	it("renders native number input and no custom numeric keypad", async () => {
		const wrapper = mountDialog({ modelValue: true, grandTotal: 50000, currency: "IDR" })
		wrapper.vm.paymentMethods = [defaultCashMethod]
		wrapper.vm.lastSelectedMethod = defaultCashMethod
		await flushPromises()

		const input = wrapper.find('input[type="number"]')
		expect(input.exists()).toBe(true)
		expect(input.attributes("inputmode")).toBe("decimal")

		const buttons = wrapper.findAll("button")
		const hasDoubleZeroBtn = buttons.some((b) => b.text().trim() === "00")
		expect(hasDoubleZeroBtn).toBe(false)

		expect(wrapper.find(".numpad-display").exists()).toBe(false)
		expect(wrapper.find(".numpadDisplay").exists()).toBe(false)
		expect(wrapper.vm.numpadDisplay).toBeUndefined()
		expect(wrapper.vm.numpadInput).toBeUndefined()
	})

	it("types an amount and clicking Add appends a payment entry and clears input", async () => {
		const wrapper = mountDialog({ modelValue: true, grandTotal: 50000, currency: "IDR" })
		wrapper.vm.paymentMethods = [defaultCashMethod]
		wrapper.vm.lastSelectedMethod = defaultCashMethod
		await flushPromises()

		const input = wrapper.find('input[type="number"]')
		await input.setValue("20000")

		const addBtn = wrapper.findAll("button").find((b) => b.text().trim() === "Add")
		expect(addBtn).toBeDefined()
		await addBtn.trigger("click")
		await flushPromises()

		expect(wrapper.vm.paymentEntries).toHaveLength(1)
		expect(wrapper.vm.paymentEntries[0].amount).toBe(20000)
		expect(wrapper.vm.paymentEntries[0].mode_of_payment).toBe("Cash")
		expect(wrapper.vm.amountInput).toBe("")
		expect(input.element.value).toBe("")
	})

	it("displays Cash Received for cash and switches to Payment Amount for non-cash method", async () => {
		const wrapper = mountDialog({ modelValue: true, grandTotal: 50000, currency: "IDR" })
		wrapper.vm.paymentMethods = [defaultCashMethod]
		wrapper.vm.lastSelectedMethod = defaultCashMethod
		await flushPromises()

		const label = wrapper.findAll("label").find((l) => l.text().includes("Cash Received"))
		expect(label).toBeDefined()

		const qrisMethod = { mode_of_payment: "QRIS", type: "Phone", account_type: "Bank" }
		wrapper.vm.paymentMethods.push(qrisMethod)
		wrapper.vm.selectPaymentMethod(qrisMethod)
		await flushPromises()

		const updatedLabel = wrapper.findAll("label").find((l) => l.text().includes("Payment Amount"))
		expect(updatedLabel).toBeDefined()
		expect(wrapper.findAll("label").some((l) => l.text().includes("Cash Received"))).toBe(false)
	})

	it("pays remaining amount with Uang Pas button for IDR cash", async () => {
		const wrapper = mountDialog({ modelValue: true, grandTotal: 50000, currency: "IDR" })
		wrapper.vm.paymentMethods = [defaultCashMethod]
		wrapper.vm.lastSelectedMethod = defaultCashMethod
		await flushPromises()

		const uangPasBtn = wrapper.findAll("button").find((b) => b.text().trim() === "Uang Pas")
		expect(uangPasBtn).toBeDefined()

		await uangPasBtn.trigger("click")
		await flushPromises()

		expect(wrapper.vm.paymentEntries).toHaveLength(1)
		expect(wrapper.vm.paymentEntries[0].amount).toBe(50000)
		expect(wrapper.vm.remainingAmount).toBe(0)
	})

	it("uses a 2-column grid for quick amounts", async () => {
		const wrapper = mountDialog({ modelValue: true, grandTotal: 50000, currency: "IDR" })
		wrapper.vm.paymentMethods = [defaultCashMethod]
		wrapper.vm.lastSelectedMethod = defaultCashMethod
		await flushPromises()

		const uangPasBtn = wrapper.findAll("button").find((b) => b.text().trim() === "Uang Pas")
		expect(uangPasBtn).toBeDefined()
		expect(uangPasBtn.element.parentElement.classList.contains("grid-cols-2")).toBe(true)
	})

	it("disables input with Exact amount only placeholder for non-cash in exact-amount mode", async () => {
		const wrapper = mountDialog({ modelValue: true, grandTotal: 50000, currency: "IDR" })
		const settingsStore = usePOSSettingsStore(wrapper.vm.$pinia)
		settingsStore.settings.use_exact_amount = 1

		const cardMethod = { mode_of_payment: "Card", type: "Card", account_type: "Bank" }
		wrapper.vm.paymentMethods = [cardMethod]
		wrapper.vm.lastSelectedMethod = cardMethod
		await flushPromises()

		const input = wrapper.find('input[type="number"]')
		expect(input.attributes("disabled")).toBeDefined()
		expect(input.attributes("placeholder")).toBe("Exact amount only")
	})

	it.each([false, true])("hides non-cash shortcuts and can pay the balance (exact mode: %s)", async (exact) => {
		const wrapper = mountDialog({ modelValue: true, grandTotal: 50000 })
		usePOSSettingsStore(wrapper.vm.$pinia).settings.use_exact_amount = Number(exact)
		const method = { mode_of_payment: "QRIS", type: "Bank", account_type: "Bank" }
		wrapper.vm.paymentMethods = [method]
		wrapper.vm.lastSelectedMethod = method
		await flushPromises()

		expect(wrapper.findAll("button").some((b) => b.text().includes("55.000"))).toBe(false)
		const pay = wrapper.find('button[data-payment-balance]')
		expect(pay.exists()).toBe(true)
		await pay.trigger("click")
		await flushPromises()
		expect(wrapper.vm.paymentEntries[0].amount).toBe(50000)
		expect(wrapper.vm.paymentEntries[0].mode_of_payment).toBe("QRIS")
		wrapper.unmount()
	})

	it("adds a typed amount once when Enter is pressed", async () => {
		const wrapper = mountDialog({ modelValue: true })
		wrapper.vm.paymentMethods = [defaultCashMethod]
		wrapper.vm.lastSelectedMethod = defaultCashMethod
		await flushPromises()

		const input = wrapper.find('#payment-amount')
		await input.setValue("20000")
		await input.trigger("keyup", { key: "Enter" })
		await flushPromises()

		expect(wrapper.vm.paymentEntries).toHaveLength(1)
		expect(wrapper.vm.paymentEntries[0].amount).toBe(20000)
		expect(wrapper.vm.amountInput).toBe("")
		wrapper.unmount()
	})

	it.each(["Debit Card", "E-Wallet"])("hides denominations for %s even with a legacy Cash type", async (name) => {
		const wrapper = mountDialog({ modelValue: true })
		const method = { mode_of_payment: name, type: "Cash", account_type: "Cash" }
		wrapper.vm.paymentMethods = [method]
		wrapper.vm.lastSelectedMethod = method
		await flushPromises()
		expect(wrapper.vm.isCashTenderSelected).toBe(false)
		expect(wrapper.findAll("button").some((b) => b.text() === "Uang Pas")).toBe(false)
		expect(wrapper.find('button[data-payment-balance]').exists()).toBe(true)
		wrapper.unmount()
	})

	it.each([
		["Cash", "Cash", "banknote"],
		["QRIS", "Bank", "grid"],
		["Debit Card", "Cash", "credit-card"],
		["Credit Card", "Bank", "credit-card"],
		["E-Wallet", "Cash", "smartphone"],
		["Transfer", "Bank", "briefcase"],
	])("uses an SVG icon name for %s", (mode_of_payment, type, icon) => {
		const wrapper = mountDialog()
		expect(wrapper.vm.paymentMethodIcon({ mode_of_payment, type })).toBe(icon)
		wrapper.unmount()
	})

	it("clears amountInput when clearAll is called", async () => {
		const wrapper = mountDialog({ modelValue: true, grandTotal: 50000, currency: "IDR" })
		wrapper.vm.paymentMethods = [defaultCashMethod]
		wrapper.vm.lastSelectedMethod = defaultCashMethod
		await flushPromises()

		wrapper.vm.amountInput = "25000"
		wrapper.vm.clearAll()
		expect(wrapper.vm.amountInput).toBe("")
	})

	describe("tap-to-pay and added payments list", () => {
		const defaultCardMethod = {
			mode_of_payment: "Card",
			type: "Card",
			account_type: "Bank",
		}

		it("tenders exact remainder when method is tapped without typed amount", async () => {
			const wrapper = mountDialog({ modelValue: true, grandTotal: 50000, currency: "IDR" })
			wrapper.vm.paymentMethods = [defaultCashMethod]
			await flushPromises()

			wrapper.vm.handleMethodTap(defaultCashMethod)
			await flushPromises()

			expect(wrapper.vm.paymentEntries).toHaveLength(1)
			expect(wrapper.vm.paymentEntries[0].amount).toBe(50000)
			expect(wrapper.vm.paymentEntries[0].mode_of_payment).toBe("Cash")
			expect(wrapper.vm.remainingAmount).toBe(0)
			wrapper.unmount()
		})

		it("tenders typed amount when method is tapped with amount input", async () => {
			const wrapper = mountDialog({ modelValue: true, grandTotal: 50000, currency: "IDR" })
			wrapper.vm.paymentMethods = [defaultCashMethod]
			await flushPromises()

			wrapper.vm.amountInput = "20000"
			wrapper.vm.handleMethodTap(defaultCashMethod)
			await flushPromises()

			expect(wrapper.vm.paymentEntries).toHaveLength(1)
			expect(wrapper.vm.paymentEntries[0].amount).toBe(20000)
			expect(wrapper.vm.paymentEntries[0].mode_of_payment).toBe("Cash")
			expect(wrapper.vm.remainingAmount).toBe(30000)
			expect(wrapper.vm.amountInput).toBe("")
			wrapper.unmount()
		})

		it("replaces a fully-paid single tender when another method is tapped", async () => {
			const debitMethod = {
				mode_of_payment: "Debit Card",
				type: "Bank",
				account_type: "Bank",
			}
			const wrapper = mountDialog({ modelValue: true, grandTotal: 50000, currency: "IDR" })
			wrapper.vm.paymentMethods = [debitMethod, defaultCashMethod]
			await flushPromises()

			wrapper.vm.handleMethodTap(debitMethod)
			await flushPromises()
			expect(wrapper.vm.paymentEntries).toHaveLength(1)
			expect(wrapper.vm.paymentEntries[0].mode_of_payment).toBe("Debit Card")

			wrapper.vm.handleMethodTap(defaultCashMethod)
			await flushPromises()

			expect(wrapper.vm.paymentEntries).toHaveLength(1)
			expect(wrapper.vm.paymentEntries[0].mode_of_payment).toBe("Cash")
			expect(wrapper.vm.paymentEntries[0].amount).toBe(50000)
			expect(wrapper.vm.remainingAmount).toBe(0)
			expect(wrapper.vm.lastSelectedMethod.mode_of_payment).toBe("Cash")
			wrapper.unmount()
		})

		it("replaces a fully-paid single tender on quick add (long-press path)", async () => {
			const debitMethod = {
				mode_of_payment: "Debit Card",
				type: "Bank",
				account_type: "Bank",
			}
			const wrapper = mountDialog({ modelValue: true, grandTotal: 50000, currency: "IDR" })
			wrapper.vm.paymentMethods = [debitMethod, defaultCashMethod]
			await flushPromises()

			wrapper.vm.handleMethodTap(debitMethod)
			await flushPromises()

			wrapper.vm.quickAddPayment(defaultCashMethod)
			await flushPromises()

			expect(wrapper.vm.paymentEntries).toHaveLength(1)
			expect(wrapper.vm.paymentEntries[0].mode_of_payment).toBe("Cash")
			expect(wrapper.vm.paymentEntries[0].amount).toBe(50000)
			expect(wrapper.vm.remainingAmount).toBe(0)
			wrapper.unmount()
		})

		it("renders added payments list and undo button removes one entry via removeEntry", async () => {
			const wrapper = mountDialog({ modelValue: true, grandTotal: 50000, currency: "IDR" })
			wrapper.vm.paymentMethods = [defaultCashMethod, defaultCardMethod]
			await flushPromises()

			wrapper.vm.addCustomPayment(defaultCashMethod, 20000)
			wrapper.vm.addCustomPayment(defaultCardMethod, 30000)
			await flushPromises()

			expect(wrapper.vm.paymentEntries).toHaveLength(2)
			const addedPaymentsContainer = wrapper.find("[data-added-payments]")
			expect(addedPaymentsContainer.exists()).toBe(true)

			const removeButtons = wrapper.findAll("button[data-remove-payment]")
			expect(removeButtons).toHaveLength(2)

			await removeButtons[0].trigger("click")
			await flushPromises()

			expect(wrapper.vm.paymentEntries).toHaveLength(1)
			expect(wrapper.vm.paymentEntries[0].mode_of_payment).toBe("Card")
			expect(wrapper.vm.paymentEntries[0].amount).toBe(30000)
			expect(wrapper.vm.remainingAmount).toBe(20000)
			wrapper.unmount()
		})
	})
})
