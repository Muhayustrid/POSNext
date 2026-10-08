/**
 * @vitest-environment jsdom
 */
import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"
import { createPinia } from "pinia"

const toastSpies = vi.hoisted(() => ({
	showInfo: vi.fn(),
	showSuccess: vi.fn(),
	showWarning: vi.fn(),
}))
const printEODReport = vi.hoisted(() => vi.fn())
const reloadSettings = vi.hoisted(() => vi.fn().mockResolvedValue(undefined))
const getClosingShiftData = vi.hoisted(() => ({
	submit: vi.fn(),
	loading: false,
	error: null,
}))
const submitClosingShift = vi.hoisted(() => ({
	submit: vi.fn(),
	loading: false,
	data: null,
	error: null,
}))

vi.mock("frappe-ui", async () => {
	const { defineComponent } = await import("vue")
	const stub = defineComponent({ name: "FrappeUIStub", render: () => null })
	return { Dialog: stub, Button: stub, FeatherIcon: stub, Input: stub }
})

vi.mock("../composables/useShift", async () => {
	const { ref } = await import("vue")
	return {
		shiftState: ref({ _initialElapsedMs: 0, _receivedAt: 0 }),
		useShift: () => ({ getClosingShiftData, submitClosingShift }),
	}
})

vi.mock("../composables/useFormatters", () => ({
	useFormatters: () => ({
		formatCurrency: (v) => String(v ?? ""),
		formatQuantity: (v) => String(v ?? ""),
		formatDateTime: (v) => String(v ?? ""),
		formatTime: (v) => String(v ?? ""),
	}),
}))

vi.mock("../composables/useToast", () => ({ useToast: () => toastSpies }))

vi.mock("../stores/posSettings", async () => {
	const { defineStore } = await import("pinia")
	const { ref } = await import("vue")
	return {
		usePOSSettingsStore: defineStore("posSettings", () => ({
			hideExpectedAmount: ref(false),
			reloadSettings,
		})),
	}
})

vi.mock("../stores/posShift", async () => {
	const { defineStore } = await import("pinia")
	const { ref } = await import("vue")
	return {
		usePOSShiftStore: defineStore("posShift", () => ({
			currentTime: ref(""),
			shiftDuration: ref(""),
			shiftTimerPaused: ref(false),
		})),
	}
})

// The dialog blocks closing over unsynced offline invoices / in-flight submits.
// Mock both stores so the test env never touches the offline worker.
vi.mock("../stores/posSync", async () => {
	const { defineStore } = await import("pinia")
	const { computed, ref } = await import("vue")
	const pendingInvoicesCount = ref(0)
	return {
		usePOSSyncStore: defineStore("posSync", () => ({
			pendingInvoicesCount,
			hasPendingInvoices: computed(() => pendingInvoicesCount.value > 0),
			updatePendingCount: vi.fn().mockResolvedValue(undefined),
		})),
	}
})

vi.mock("../stores/posCart", async () => {
	const { defineStore } = await import("pinia")
	const { ref } = await import("vue")
	return {
		usePOSCartStore: defineStore("posCart", () => ({
			isSubmitting: ref(false),
		})),
	}
})

vi.mock("../utils/printEod", () => ({ printEODReport }))

// Provide a trivial global translation helper the way printEod.test does.
globalThis.__ = (message, replacements = []) => {
	if (!Array.isArray(replacements) || !replacements.length) return message
	let out = message
	for (const [i, v] of replacements.entries())
		out = out.split(`{${i}}`).join(String(v))
	return out
}

import ShiftClosingDialog from "./ShiftClosingDialog.vue"

const CLOSING_SHIFT_NAME = "POS-CLOS-0001"
const POS_PROFILE = "POS Profile juri1"

function mountDialog() {
	return mount(ShiftClosingDialog, {
		props: { modelValue: false, openingShift: "POS-OPEN-0001" },
		global: {
			plugins: [createPinia()],
			// The app installs __() as a global property; the template needs it.
			config: { globalProperties: { __: globalThis.__ } },
		},
		shallow: true,
	})
}

/** Open the dialog so the watch loads closing data (and closingData.pos_profile). */
async function mountOpenDialog() {
	const wrapper = mountDialog()
	await wrapper.setProps({ modelValue: true })
	await flushPromises()
	return wrapper
}

describe("ShiftClosingDialog EOD print feedback", () => {
	beforeEach(() => {
		vi.clearAllMocks()
		reloadSettings.mockResolvedValue(undefined)
		submitClosingShift.submit.mockResolvedValue({ name: CLOSING_SHIFT_NAME })
		getClosingShiftData.submit.mockResolvedValue({
			pos_profile: POS_PROFILE,
			payment_reconciliation: [
				{ mode_of_payment: "Cash", expected_amount: 100 },
			],
			pos_transactions: [],
		})
		printEODReport.mockResolvedValue({ method: "silent", success: true })
	})

	it("submitClosing consumes printEODReport's lane: info toast for printview", async () => {
		printEODReport.mockResolvedValue({ method: "printview", success: true })
		const wrapper = await mountOpenDialog()

		await wrapper.vm.submitClosing()
		await flushPromises()

		expect(printEODReport).toHaveBeenCalledWith(CLOSING_SHIFT_NAME, POS_PROFILE)
		expect(toastSpies.showInfo).toHaveBeenCalledTimes(1)
		expect(toastSpies.showInfo).toHaveBeenCalledWith(
			"Direct print was not detected. The EOD report was opened in a print preview window instead.",
		)
		expect(toastSpies.showSuccess).not.toHaveBeenCalled()
		// Normal mode still finishes the close even when only the preview opened.
		expect(wrapper.emitted("shift-closed")).toHaveLength(1)
	})

	it("submitClosing stays quiet on the silent lane", async () => {
		const wrapper = await mountOpenDialog()

		await wrapper.vm.submitClosing()
		await flushPromises()

		expect(printEODReport).toHaveBeenCalledTimes(1)
		expect(toastSpies.showInfo).not.toHaveBeenCalled()
		expect(toastSpies.showWarning).not.toHaveBeenCalled()
		expect(wrapper.emitted("shift-closed")).toHaveLength(1)
	})

	it("submitClosing raises the retry banner when the EOD print throws", async () => {
		printEODReport.mockRejectedValue(new Error("No print driver available"))
		const wrapper = await mountOpenDialog()

		await wrapper.vm.submitClosing()
		await flushPromises()

		expect(toastSpies.showWarning).toHaveBeenCalledWith(
			"EOD report did not print. Use the Reprint button to retry.",
			"Print failed",
		)
		expect(toastSpies.showInfo).not.toHaveBeenCalled()
		// Closing is aborted so the cashier can retry the print.
		expect(wrapper.emitted("shift-closed")).toBeUndefined()

		// Failure state hides the reconciliation form, shows the final panel.
		expect(wrapper.vm.isPrintFailureState).toBe(true)
		expect(wrapper.vm.showSuccessReport).toBe(true)
	})

	it("print-failure state hides reconciliation inputs and shows Reprint/Done", async () => {
		printEODReport.mockRejectedValue(new Error("No print driver available"))
		// Mount with slot-rendering stubs so body/footer templates actually
		// render (the default null stub never mounts slot content).
		const rendered = mount(ShiftClosingDialog, {
			props: { modelValue: false, openingShift: "POS-OPEN-0001" },
			global: {
				plugins: [createPinia()],
				config: { globalProperties: { __: globalThis.__ } },
				stubs: {
					// The frappe-ui mock registers Dialog/Button/FeatherIcon all as
					// "FrappeUIStub"; re-stub it to render slots so the templates show.
					// Dialog needs the named body-content/actions slots.
					FrappeUIStub: {
						template:
							"<div><slot name='body-content' /><slot name='actions' /><slot /></div>",
					},
				},
			},
			shallow: true,
		})
		await rendered.setProps({ modelValue: true })
		await flushPromises()

		await rendered.vm.submitClosing()
		await flushPromises()

		expect(rendered.vm.isPrintFailureState).toBe(true)
		// Reconciliation form must be gone from the rendered body
		expect(rendered.html()).not.toContain("Enter actual amount for")
		expect(rendered.html()).toContain("The EOD report has not been printed yet.")

		// Footer shows Reprint + Done, not the mixed-signal texts
		const text = rendered.text()
		expect(text).toContain("Reprint EOD Report")
		expect(text).toContain("Done")
		expect(text).not.toContain("EOD report pending print")
		expect(text).not.toContain("Cancel")
	})

	it("retryEodPrint reports success only for the silent lane", async () => {
		const wrapper = await mountOpenDialog()
		wrapper.vm.eodPrintFailed = { closingShiftName: CLOSING_SHIFT_NAME }

		await wrapper.vm.retryEodPrint()

		expect(printEODReport).toHaveBeenCalledWith(CLOSING_SHIFT_NAME, POS_PROFILE)
		expect(toastSpies.showSuccess).toHaveBeenCalledTimes(1)
		expect(toastSpies.showSuccess).toHaveBeenCalledWith(
			"EOD report printed successfully",
		)
		expect(toastSpies.showInfo).not.toHaveBeenCalled()
		expect(wrapper.emitted("update:modelValue")).toContainEqual([false])
	})

	it("retryEodPrint points at the preview window for the printview lane", async () => {
		printEODReport.mockResolvedValue({ method: "printview", success: true })
		const wrapper = await mountOpenDialog()
		wrapper.vm.eodPrintFailed = { closingShiftName: CLOSING_SHIFT_NAME }

		await wrapper.vm.retryEodPrint()

		expect(toastSpies.showInfo).toHaveBeenCalledTimes(1)
		expect(toastSpies.showInfo).toHaveBeenCalledWith(
			"The EOD report was opened in a print preview window.",
		)
		expect(toastSpies.showSuccess).not.toHaveBeenCalled()
		expect(wrapper.emitted("update:modelValue")).toContainEqual([false])
	})

	it("retryEodPrint warns without closing the dialog when the print throws", async () => {
		printEODReport.mockRejectedValue(new Error("No print driver available"))
		const wrapper = await mountOpenDialog()
		wrapper.vm.eodPrintFailed = { closingShiftName: CLOSING_SHIFT_NAME }

		await wrapper.vm.retryEodPrint()

		expect(toastSpies.showWarning).toHaveBeenCalledWith(
			"EOD report did not print. Retry, or check the printer.",
			"Print failed",
		)
		expect(toastSpies.showInfo).not.toHaveBeenCalled()
		expect(toastSpies.showSuccess).not.toHaveBeenCalled()
		expect(wrapper.emitted("update:modelValue")).toBeUndefined()
	})
})

describe("ShiftClosingDialog counted-amount prefill", () => {
	beforeEach(() => {
		vi.clearAllMocks()
		reloadSettings.mockResolvedValue(undefined)
		printEODReport.mockResolvedValue({ method: "silent", success: true })
	})

	it("prefills each row from the expected total, still editable with live grouping", async () => {
		getClosingShiftData.submit.mockResolvedValue({
			pos_profile: POS_PROFILE,
			payment_reconciliation: [
				{ mode_of_payment: "Cash", expected_amount: 71000 },
				{ mode_of_payment: "QRIS", expected_amount: 25000 },
			],
			pos_transactions: [],
		})
		const wrapper = await mountOpenDialog()
		const rows = wrapper.vm.closingData.payment_reconciliation
		expect(rows.map((r) => r.closing_text)).toEqual(["71.000", "25.000"])
		expect(rows.map((r) => r.closing_amount)).toEqual([71000, 25000])
		expect(rows.map((r) => r.difference)).toEqual([0, 0])
		// the cashier can submit right away — prefill counts as filled
		expect(wrapper.vm.canSubmit).toBe(true)

		// an edit recalculates live through the same mask as the opening dialog
		wrapper.vm.onClosingInput(rows[0], "20000")
		expect(rows[0].closing_text).toBe("20.000")
		expect(rows[0].closing_amount).toBe(20000)
		expect(rows[0].difference).toBe(-51000)
		expect(wrapper.vm.canSubmit).toBe(true)
	})

	it("prefills a negative expected with its sign", async () => {
		getClosingShiftData.submit.mockResolvedValue({
			pos_profile: POS_PROFILE,
			payment_reconciliation: [
				{ mode_of_payment: "Cash", expected_amount: -19750 },
			],
			pos_transactions: [],
		})
		const wrapper = await mountOpenDialog()
		const row = wrapper.vm.closingData.payment_reconciliation[0]
		expect(row.closing_text).toBe("-19.750")
		expect(row.closing_amount).toBe(-19750)
		expect(row.difference).toBe(0)
	})
})
