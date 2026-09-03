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
		)
		expect(toastSpies.showInfo).not.toHaveBeenCalled()
		// Closing is aborted so the cashier can retry the print.
		expect(wrapper.emitted("shift-closed")).toBeUndefined()
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
		)
		expect(toastSpies.showInfo).not.toHaveBeenCalled()
		expect(toastSpies.showSuccess).not.toHaveBeenCalled()
		expect(wrapper.emitted("update:modelValue")).toBeUndefined()
	})
})
