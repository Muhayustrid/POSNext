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

const storeRefs = vi.hoisted(() => ({
	hideExpectedAmount: false,
}))

vi.mock("../stores/posSettings", async () => {
	const { defineStore } = await import("pinia")
	const { computed } = await import("vue")
	return {
		usePOSSettingsStore: defineStore("posSettings", () => ({
			hideExpectedAmount: computed(() => storeRefs.hideExpectedAmount),
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

const syncStoreRefs = vi.hoisted(() => ({
	pendingInvoicesCount: 0,
}))
const updatePendingCountSpy = vi.hoisted(() => vi.fn())

// The dialog blocks closing over unsynced offline invoices / in-flight submits.
// Mock both stores so the test env never touches the offline worker.
vi.mock("../stores/posSync", async () => {
	const { defineStore } = await import("pinia")
	const { computed, ref } = await import("vue")
	return {
		usePOSSyncStore: defineStore("posSync", () => {
			const pendingInvoicesCount = ref(0)
			return {
				pendingInvoicesCount,
				hasPendingInvoices: computed(() => pendingInvoicesCount.value > 0),
				updatePendingCount: updatePendingCountSpy.mockImplementation(async () => {
					pendingInvoicesCount.value = syncStoreRefs.pendingInvoicesCount
				}),
			}
		}),
	}
})

const cartStoreRefs = vi.hoisted(() => ({
	isSubmitting: false,
}))

vi.mock("../stores/posCart", async () => {
	const { defineStore } = await import("pinia")
	const { ref } = await import("vue")
	return {
		usePOSCartStore: defineStore("posCart", () => ({
			isSubmitting: ref(cartStoreRefs.isSubmitting),
		})),
	}
})

vi.mock("../utils/printEod", () => ({
	printEODReport,
	downloadEodPDF: vi.fn().mockResolvedValue(undefined),
}))

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
		printEODReport.mockResolvedValue({ method: "silent", success: true, printed: true })
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

	it("print-failure state hides reconciliation inputs and shows the retry panel", async () => {
		printEODReport.mockResolvedValue({
			method: "silent",
			success: false,
			printed: false,
			error: new Error("No print driver available"),
		})
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

		// Footer shows the 3-button retry panel, no automatic fallback
		const text = rendered.text()
		expect(text).toContain("Print Ulang")
		expect(text).toContain("Export PDF")
		expect(text).toContain("Tutup")
		expect(text).not.toContain("EOD report pending print")
		expect(text).not.toContain("Cancel")
	})

	it("retryEodPrint closes the dialog after a successful print", async () => {
		printEODReport.mockResolvedValue({ method: "silent", success: true, printed: true })
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

	it("retryEodPrint keeps the retry panel up when the print fails again", async () => {
		printEODReport.mockResolvedValue({
			method: "silent",
			success: false,
			printed: false,
			error: new Error("No print driver available"),
		})
		const wrapper = await mountOpenDialog()
		wrapper.vm.eodPrintFailed = { closingShiftName: CLOSING_SHIFT_NAME }

		await wrapper.vm.retryEodPrint()

		expect(toastSpies.showWarning).toHaveBeenCalledWith(
			"EOD report did not print. Retry, or check the printer.",
			"Print failed",
		)
		expect(toastSpies.showSuccess).not.toHaveBeenCalled()
		expect(wrapper.emitted("update:modelValue")).toBeUndefined()
	})

	it("downloadEodReportPDF downloads the EOD PDF from the retry panel", async () => {
		const { downloadEodPDF } = await import("../utils/printEod")
		const wrapper = await mountOpenDialog()
		wrapper.vm.eodPrintFailed = { closingShiftName: CLOSING_SHIFT_NAME }

		await wrapper.vm.downloadEodReportPDF()
		expect(downloadEodPDF).toHaveBeenCalledWith(CLOSING_SHIFT_NAME)
	})

	it("retryEodPrint warns without closing the dialog when the print fails", async () => {
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

describe("ShiftClosingDialog closing always expected", () => {
	beforeEach(() => {
		vi.clearAllMocks()
		storeRefs.hideExpectedAmount = false
		reloadSettings.mockResolvedValue(undefined)
		submitClosingShift.submit.mockResolvedValue({ name: CLOSING_SHIFT_NAME })
		getClosingShiftData.submit.mockResolvedValue({
			pos_profile: POS_PROFILE,
			payment_reconciliation: [
				{ mode_of_payment: "Cash", expected_amount: 150000 },
				{ mode_of_payment: "QRIS", expected_amount: 50000 },
			],
			pos_transactions: [],
		})
		printEODReport.mockResolvedValue({ method: "silent", success: true, printed: true })
	})

	it("renders no amount inputs and submit sends rows without closing_amount", async () => {
		const rendered = mount(ShiftClosingDialog, {
			props: { modelValue: false, openingShift: "POS-OPEN-0001" },
			global: {
				plugins: [createPinia()],
				config: { globalProperties: { __: globalThis.__ } },
				stubs: {
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

		// No closing amount input should be rendered
		expect(rendered.findAll("input")).toHaveLength(0)
		expect(rendered.html()).not.toContain("Enter actual amount for")

		// Submit should send rows without closing_amount
		await rendered.vm.submitClosing()
		await flushPromises()

		expect(submitClosingShift.submit).toHaveBeenCalledTimes(1)
		const sentClosing = submitClosingShift.submit.mock.calls[0][0].closing_shift
		const sentRows = sentClosing.payment_reconciliation
		expect(sentRows.map((r) => r.closing_amount)).toEqual([undefined, undefined])
	})
})

describe("ShiftClosingDialog autoClose mode", () => {
	beforeEach(() => {
		vi.clearAllMocks()
		syncStoreRefs.pendingInvoicesCount = 0
		cartStoreRefs.isSubmitting = false
		storeRefs.hideExpectedAmount = true
		reloadSettings.mockResolvedValue(undefined)
		submitClosingShift.submit.mockResolvedValue({ name: CLOSING_SHIFT_NAME })
		getClosingShiftData.submit.mockResolvedValue({
			pos_profile: POS_PROFILE,
			payment_reconciliation: [
				{ mode_of_payment: "Cash", expected_amount: 150000 },
				{ mode_of_payment: "QRIS", expected_amount: 50000 },
			],
			pos_transactions: [],
		})
		printEODReport.mockResolvedValue({ method: "silent", success: true, printed: true })
	})

	it("with autoClose prop on, auto-submits, calls printEODReport, and emits shift-closed", async () => {
		const rendered = mount(ShiftClosingDialog, {
			props: { modelValue: true, openingShift: "POS-OPEN-0001", autoClose: true },
			global: {
				plugins: [createPinia()],
				config: { globalProperties: { __: globalThis.__ } },
				stubs: {
					FrappeUIStub: {
						template:
							"<div><slot name='body-content' /><slot name='actions' /><slot /></div>",
					},
				},
			},
			shallow: true,
		})
		await flushPromises()

		expect(submitClosingShift.submit).toHaveBeenCalledTimes(1)
		const sentClosing = submitClosingShift.submit.mock.calls[0][0].closing_shift
		const sentRows = sentClosing.payment_reconciliation
		expect(sentRows.map((r) => r.closing_amount)).toEqual([undefined, undefined])
		expect(printEODReport).toHaveBeenCalledWith(CLOSING_SHIFT_NAME, POS_PROFILE)
		expect(rendered.emitted("shift-closed")).toBeTruthy()
	})

	it("with autoClose prop on and printEODReport failure, keeps reprint final state", async () => {
		printEODReport.mockRejectedValue(new Error("Printer paper out"))
		const rendered = mount(ShiftClosingDialog, {
			props: { modelValue: true, openingShift: "POS-OPEN-0001", autoClose: true },
			global: {
				plugins: [createPinia()],
				config: { globalProperties: { __: globalThis.__ } },
				stubs: {
					FrappeUIStub: {
						template:
							"<div><slot name='body-content' /><slot name='actions' /><slot /></div>",
					},
				},
			},
			shallow: true,
		})
		await flushPromises()

		expect(submitClosingShift.submit).toHaveBeenCalledTimes(1)
		expect(rendered.vm.isPrintFailureState).toBe(true)
		expect(rendered.emitted("shift-closed")).toBeFalsy()
	})

	it("with autoClose prop on and submit failure, keeps dialog open in manual mode and emits auto-close-failed", async () => {
		submitClosingShift.submit.mockRejectedValue(new Error("Server error"))
		const rendered = mount(ShiftClosingDialog, {
			props: { modelValue: true, openingShift: "POS-OPEN-0001", autoClose: true },
			global: {
				plugins: [createPinia()],
				config: { globalProperties: { __: globalThis.__ } },
				stubs: {
					FrappeUIStub: {
						template:
							"<div><slot name='body-content' /><slot name='actions' /><slot /></div>",
					},
				},
			},
			shallow: true,
		})
		await flushPromises()

		expect(rendered.emitted("auto-close-failed")).toBeTruthy()
		expect(rendered.vm.errorMessage).toBe("Server error")
		expect(rendered.vm.open).toBe(true)
	})

	it("with autoClose prop on and loadClosingData failure, keeps dialog open in manual mode and emits auto-close-failed", async () => {
		getClosingShiftData.submit.mockRejectedValue(new Error("Network disconnect"))
		const rendered = mount(ShiftClosingDialog, {
			props: { modelValue: true, openingShift: "POS-OPEN-0001", autoClose: true },
			global: {
				plugins: [createPinia()],
				config: { globalProperties: { __: globalThis.__ } },
				stubs: {
					FrappeUIStub: {
						template:
							"<div><slot name='body-content' /><slot name='actions' /><slot /></div>",
					},
				},
			},
			shallow: true,
		})
		await flushPromises()

		expect(rendered.emitted("auto-close-failed")).toBeTruthy()
		expect(rendered.vm.errorMessage).toBe(
			"Unable to load shift data. Please check your connection and try again.",
		)
		expect(rendered.vm.open).toBe(true)
		expect(submitClosingShift.submit).not.toHaveBeenCalled()
	})

	it("with autoClose prop on, does not submit and emits auto-close-failed when offline invoices are pending sync", async () => {
		syncStoreRefs.pendingInvoicesCount = 2
		const rendered = mount(ShiftClosingDialog, {
			props: { modelValue: true, openingShift: "POS-OPEN-0001", autoClose: true },
			global: {
				plugins: [createPinia()],
				config: { globalProperties: { __: globalThis.__ } },
				stubs: {
					FrappeUIStub: {
						template:
							"<div><slot name='body-content' /><slot name='actions' /><slot /></div>",
					},
				},
			},
			shallow: true,
		})
		await flushPromises()

		expect(updatePendingCountSpy).toHaveBeenCalled()
		expect(submitClosingShift.submit).not.toHaveBeenCalled()
		expect(rendered.emitted("shift-closed")).toBeFalsy()
		expect(rendered.emitted("auto-close-failed")).toBeTruthy()
		expect(rendered.vm.open).toBe(true)
	})

	it("with autoClose prop on, does not submit and emits auto-close-failed when cart submission is in progress", async () => {
		cartStoreRefs.isSubmitting = true
		const rendered = mount(ShiftClosingDialog, {
			props: { modelValue: true, openingShift: "POS-OPEN-0001", autoClose: true },
			global: {
				plugins: [createPinia()],
				config: { globalProperties: { __: globalThis.__ } },
				stubs: {
					FrappeUIStub: {
						template:
							"<div><slot name='body-content' /><slot name='actions' /><slot /></div>",
					},
				},
			},
			shallow: true,
		})
		await flushPromises()

		expect(submitClosingShift.submit).not.toHaveBeenCalled()
		expect(rendered.emitted("shift-closed")).toBeFalsy()
		expect(rendered.emitted("auto-close-failed")).toBeTruthy()
		expect(rendered.vm.open).toBe(true)
	})
})
