/**
 * @vitest-environment jsdom
 *
 * Regression: the open-state watcher used to call resetDialog() on CLOSE,
 * rebuilding the DOM (step=1, profiles=null) exactly while the reka-ui unmount
 * transition was running. The transition stalled, the dialog stayed on screen
 * showing "No POS Profiles available...", and because the parent's modelValue
 * was already false, Cancel/X emitted false->false = no-op. The only escape
 * was a full reload.
 *
 * The fix: reset happens on OPEN (inside initDialog), close leaves state
 * untouched, and the step-3 "Close & Open New" flow is delegated to the
 * page-level ShiftClosingDialog via the close-existing-shift emit.
 */
import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"

const shiftMocks = vi.hoisted(() => ({
	checkOpeningShift: { fetch: vi.fn(), reset: vi.fn() },
	createOpeningShift: {
		submit: vi.fn(),
		loading: false,
		error: null,
		reset: vi.fn(),
	},
	getOpeningDialogData: {
		fetch: vi.fn(),
		loading: false,
		data: null,
		error: null,
		reset: vi.fn(),
	},
}))

const resources = vi.hoisted(() => ({ instances: [] }))

// Resources are created inside setup(), so their fetch impls are configured
// by url here and consulted at call time, before any mount.
const resourceImpls = vi.hoisted(() => ({ byUrl: {} }))

vi.mock("frappe-ui", async () => {
	const { defineComponent, reactive } = await import("vue")
	const Dialog = defineComponent({
		name: "Dialog",
		props: {
			modelValue: { type: Boolean, default: false },
			options: { type: Object, default: () => ({}) },
		},
		template: `<div><slot name="body-content" /><slot name="actions" /></div>`,
	})
	const Button = defineComponent({
		name: "Button",
		emits: ["click"],
		template: `<button v-bind="$attrs" @click="$emit('click')"><slot /></button>`,
	})
	return {
		Dialog,
		Button,
		FeatherIcon: defineComponent({ name: "FeatherIcon", render: () => null }),
		createResource: (opts) => {
			const r = reactive({
				loading: false,
				data: null,
				error: null,
				url: opts?.url,
				fetch: vi.fn(() => {
					const impl = resourceImpls.byUrl[opts?.url]
					return Promise.resolve(impl ? impl() : null).then((data) => {
						r.data = data
						r.loading = false
						return data
					})
				}),
				submit: vi.fn(async () => null),
				reset: vi.fn(() => {
					r.data = null
					r.error = null
					r.loading = false
				}),
				update: vi.fn(),
			})
			resources.instances.push(r)
			return r
		},
	}
})

vi.mock("../composables/useShift", () => ({
	useShift: () => shiftMocks,
}))

vi.mock("../composables/useFormatters", () => ({
	useFormatters: () => ({
		formatDateTime: (v) => String(v ?? ""),
		formatTime: (v) => String(v ?? ""),
		formatCurrency: (v) => String(v ?? ""),
	}),
}))

vi.mock("../utils/apiWrapper", () => ({
	serverErrorMessage: (error) => String(error?.message || error),
}))

// The app installs __() as a global property; the template needs it.
vi.hoisted(() => {
	globalThis.__ = (message, replacements = []) => {
		if (!Array.isArray(replacements) || !replacements.length) return message
		let out = message
		for (const [i, v] of replacements.entries())
			out = out.split(`{${i}}`).join(String(v))
		return out
	}
})

import ShiftOpeningDialog from "./ShiftOpeningDialog.vue"

const PROFILES_URL = "pos_next.api.pos_profile.get_pos_profiles"
const PROFILES = [
	{ name: "Profile 1", company: "Test Co", currency: "IDR" },
	{ name: "Profile 2", company: "Test Co", currency: "IDR" },
]
const EXISTING_SHIFT = {
	pos_opening_shift: { name: "POS-OPEN-9" },
	pos_profile: { name: "Profile 1" },
}

function profilesResource() {
	return resources.instances.find((r) => r.url === PROFILES_URL)
}

function mountDialog(props = {}) {
	return mount(ShiftOpeningDialog, {
		props: { modelValue: false, ...props },
		global: {
			config: { globalProperties: { __: globalThis.__ } },
			stubs: {
				Dialog: {
					template:
						"<div><slot name='body-content' /><slot name='actions' /><slot /></div>",
				},
				Button: {
					props: ["disabled"],
					template:
						"<button :disabled='disabled' v-bind='$attrs' @click=\"$emit('click')\"><slot /></button>",
				},
			},
		},
		shallow: true,
	})
}

/** Mount closed, then open — drives initDialog through the watcher. */
async function mountOpenDialog(existingShift = null) {
	resourceImpls.byUrl[PROFILES_URL] = () => PROFILES
	shiftMocks.checkOpeningShift.fetch.mockResolvedValue(existingShift)
	const wrapper = mountDialog()
	await wrapper.setProps({ modelValue: true })
	await flushPromises()
	return wrapper
}

beforeEach(() => {
	vi.clearAllMocks()
	resources.instances.length = 0
	resourceImpls.byUrl = {}
})

describe("ShiftOpeningDialog close keeps state intact (stuck-dialog regression)", () => {
	it("does not fetch or reset while closed at mount", async () => {
		mountDialog()
		await flushPromises()

		expect(profilesResource().fetch).not.toHaveBeenCalled()
		expect(shiftMocks.checkOpeningShift.fetch).not.toHaveBeenCalled()
	})

	it("leaves step and profiles untouched when the dialog closes", async () => {
		const wrapper = await mountOpenDialog()
		expect(wrapper.vm.step).toBe(1)
		expect(profilesResource().data).toEqual(PROFILES)

		await wrapper.setProps({ modelValue: false })
		await flushPromises()

		// The old reset-on-close blanked these mid-transition, stranding the
		// dialog on the "No POS Profiles" screen.
		expect(wrapper.vm.step).toBe(1)
		expect(profilesResource().data).toEqual(PROFILES)
		expect(profilesResource().fetch).toHaveBeenCalledTimes(1)
	})

	it("re-initializes from scratch on the next open (reset moved to open)", async () => {
		const wrapper = await mountOpenDialog()
		wrapper.vm.step = 2
		wrapper.vm.selectedProfile = PROFILES[0]
		wrapper.vm.openingBalances = { Cash: "1.000" }

		await wrapper.setProps({ modelValue: false })
		await wrapper.setProps({ modelValue: true })
		await flushPromises()

		expect(wrapper.vm.step).toBe(1)
		expect(wrapper.vm.selectedProfile).toBeNull()
		expect(wrapper.vm.openingBalances).toEqual({})
		expect(profilesResource().fetch).toHaveBeenCalledTimes(2)
	})

	it("lands on step 3 when an open shift already exists", async () => {
		const wrapper = await mountOpenDialog(EXISTING_SHIFT)

		expect(wrapper.vm.step).toBe(3)
		expect(wrapper.vm.existingShift).toEqual(EXISTING_SHIFT)
	})
})

describe("ShiftOpeningDialog close-existing-shift handoff", () => {
	it("emits the stale shift name and closes itself for the page-level dialog", async () => {
		const wrapper = await mountOpenDialog(EXISTING_SHIFT)

		wrapper.vm.closeAndOpenNew()

		expect(wrapper.emitted("close-existing-shift")).toEqual([["POS-OPEN-9"]])
		expect(wrapper.emitted("update:modelValue")).toContainEqual([false])
		expect(wrapper.emitted("dialog-closed")).toEqual([
			[{ reason: "close-and-open-new" }],
		])
	})

	it("does nothing without an existing shift", async () => {
		const wrapper = await mountOpenDialog(null)

		wrapper.vm.closeAndOpenNew()

		expect(wrapper.emitted("close-existing-shift")).toBeUndefined()
		expect(wrapper.emitted("update:modelValue")).toBeUndefined()
	})
})

describe("ShiftOpeningDialog refuses a profile held by another cashier", () => {
	it("pops up at Next and stays on step 1 when the profile is locked", async () => {
		const wrapper = await mountOpenDialog()
		wrapper.vm.selectedProfile = {
			...PROFILES[0],
			locked: true,
			active_shift: {
				name: "POPEN-1",
				user_name: "Jalu",
				since: "10-10-2026 05:35",
			},
		}

		await wrapper.vm.nextStep()

		expect(wrapper.vm.step).toBe(1)
		expect(wrapper.vm.showBlocked).toBe(true)
		expect(wrapper.vm.blockedMessage).toContain("Jalu")
		const dialogData = resources.instances.find(
			(r) => r.url === "pos_next.api.shifts.get_opening_dialog_data",
		)
		expect(dialogData.fetch).not.toHaveBeenCalled()
	})

	it("joins a manager straight into the running shift without balances", async () => {
		const wrapper = await mountOpenDialog()
		shiftMocks.createOpeningShift.submit.mockClear()
		wrapper.vm.selectedProfile = {
			...PROFILES[0],
			joinable: true,
			active_shift: { name: "POPEN-1", user_name: "Jalu", since: "10-10-2026 05:35" },
		}

		await wrapper.vm.nextStep()

		expect(wrapper.vm.step).toBe(1)
		expect(shiftMocks.createOpeningShift.submit).toHaveBeenCalledWith(
			expect.objectContaining({ pos_profile: "Profile 1", balance_details: [], join_only: 1 }),
		)
		const dialogData = resources.instances.find(
			(r) => r.url === "pos_next.api.shifts.get_opening_dialog_data",
		)
		expect(dialogData.fetch).not.toHaveBeenCalled()
		expect(wrapper.emitted("shift-opened")).toBeTruthy()
	})

	it("proceeds to balances for an unlocked profile", async () => {
		const wrapper = await mountOpenDialog()
		wrapper.vm.selectedProfile = PROFILES[0]

		await wrapper.vm.nextStep()

		expect(wrapper.vm.step).toBe(2)
		expect(wrapper.vm.showBlocked).toBe(false)
	})
})

describe("ShiftOpeningDialog shift slots and Modal Awal cash only", () => {
	const DIALOG_DATA_URL = "pos_next.api.shifts.get_opening_dialog_data"

	it("only cash modes render an input, label is Modal Awal, non-cash modes sent with opening_amount 0", async () => {
		resourceImpls.byUrl[DIALOG_DATA_URL] = () => ({
			payments_method: [
				{
					name: "pm1",
					parent: "Profile 1",
					mode_of_payment: "Cash",
					is_drawer_cash: true,
				},
				{
					name: "pm2",
					parent: "Profile 1",
					mode_of_payment: "QRIS",
					is_drawer_cash: false,
				},
			],
			slots: { "Profile 1": [] },
		})
		const wrapper = await mountOpenDialog()
		wrapper.vm.selectedProfile = PROFILES[0]
		await wrapper.vm.nextStep()
		await flushPromises()

		expect(wrapper.vm.step).toBe(2)
		expect(wrapper.text()).toContain("Modal Awal")
		expect(wrapper.text()).not.toContain("Opening Balance (Optional)")

		// Only one input for Cash; no input rendered for QRIS
		const inputs = wrapper.findAll("input")
		expect(inputs).toHaveLength(1)

		// Set cash amount
		wrapper.vm.openingBalances.Cash = "50000"
		await wrapper.vm.openShift()
		await flushPromises()

		expect(shiftMocks.createOpeningShift.submit).toHaveBeenCalledWith(
			expect.objectContaining({
				pos_profile: "Profile 1",
				company: "Test Co",
				balance_details: [
					{ mode_of_payment: "Cash", opening_amount: 50000 },
					{ mode_of_payment: "QRIS", opening_amount: 0 },
				],
			}),
		)
	})

	it("shows a picker with both options and defaults to the most recently started slot", async () => {
		// 14:00: Shift 1 (05-15) and Shift 2 (13-21) overlap; Shift 2 started last
		vi.useFakeTimers({ toFake: ["Date"] })
		vi.setSystemTime(new Date(2026, 9, 28, 14, 0))
		resourceImpls.byUrl[DIALOG_DATA_URL] = () => ({
			payments_method: [
				{
					name: "pm1",
					parent: "Profile 1",
					mode_of_payment: "Cash",
					is_drawer_cash: true,
				},
			],
			slots: {
				"Profile 1": [
					{
						shift_name: "Shift 2",
						start_time: "13:00:00",
						end_time: "21:00:00",
						open_now: true,
					},
					{
						shift_name: "Shift 1",
						start_time: "05:00:00",
						end_time: "15:00:00",
						open_now: true,
					},
				],
			},
		})
		const wrapper = await mountOpenDialog()
		wrapper.vm.selectedProfile = PROFILES[0]
		await wrapper.vm.nextStep()
		await flushPromises()

		// Slot picker radios exist
		const options = wrapper.findAll('input[type="radio"]')
		expect(options).toHaveLength(2)
		expect(options[0].element.value).toBe("Shift 2")
		expect(options[1].element.value).toBe("Shift 1")

		// Default selection is the slot that started most recently
		expect(wrapper.vm.selectedSlot).toBe("Shift 2")
		vi.useRealTimers()

		// Submit sends default shift_slot
		await wrapper.vm.openShift()
		await flushPromises()

		expect(shiftMocks.createOpeningShift.submit).toHaveBeenCalledWith(
			expect.objectContaining({
				shift_slot: "Shift 2",
			}),
		)

		// Picking the second slot sends that slot
		await options[1].setValue(true)
		await wrapper.vm.openShift()
		await flushPromises()

		expect(shiftMocks.createOpeningShift.submit).toHaveBeenLastCalledWith(
			expect.objectContaining({
				shift_slot: "Shift 1",
			}),
		)
	})

	it("defaults to the latest-started slot regardless of list order, across midnight", async () => {
		vi.useFakeTimers({ toFake: ["Date"] })
		vi.setSystemTime(new Date(2026, 9, 28, 1, 0))
		resourceImpls.byUrl[DIALOG_DATA_URL] = () => ({
			payments_method: [
				{ name: "pm1", parent: "Profile 1", mode_of_payment: "Cash", is_drawer_cash: true },
			],
			slots: {
				"Profile 1": [
					{ shift_name: "Night", start_time: "22:00:00", end_time: "06:00:00", open_now: true },
					{ shift_name: "Late", start_time: "00:30:00", end_time: "08:00:00", open_now: true },
				],
			},
		})
		const wrapper = await mountOpenDialog()
		wrapper.vm.selectedProfile = PROFILES[0]
		await wrapper.vm.nextStep()
		await flushPromises()
		vi.useRealTimers()

		expect(wrapper.vm.selectedSlot).toBe("Late")
	})

	it("shows slot name without a picker when only one slot is open", async () => {
		resourceImpls.byUrl[DIALOG_DATA_URL] = () => ({
			payments_method: [
				{
					name: "pm1",
					parent: "Profile 1",
					mode_of_payment: "Cash",
					is_drawer_cash: true,
				},
			],
			slots: {
				"Profile 1": [
					{
						shift_name: "Morning",
						start_time: "05:00:00",
						end_time: "15:00:00",
						open_now: true,
					},
					{
						shift_name: "Night",
						start_time: "22:00:00",
						end_time: "06:00:00",
						open_now: false,
					},
				],
			},
		})
		const wrapper = await mountOpenDialog()
		wrapper.vm.selectedProfile = PROFILES[0]
		await wrapper.vm.nextStep()
		await flushPromises()

		// No select picker
		expect(wrapper.find('input[type="radio"]').exists()).toBe(false)

		// Name of the slot shows
		expect(wrapper.text()).toContain("Morning")
		expect(wrapper.vm.selectedSlot).toBe("Morning")

		await wrapper.vm.openShift()
		await flushPromises()

		expect(shiftMocks.createOpeningShift.submit).toHaveBeenCalledWith(
			expect.objectContaining({
				shift_slot: "Morning",
			}),
		)
	})

	it("shows outside shift hours message and disables open button when no slots are open", async () => {
		resourceImpls.byUrl[DIALOG_DATA_URL] = () => ({
			payments_method: [
				{
					name: "pm1",
					parent: "Profile 1",
					mode_of_payment: "Cash",
					is_drawer_cash: true,
				},
			],
			slots: {
				"Profile 1": [
					{
						shift_name: "Morning",
						start_time: "05:00:00",
						end_time: "15:00:00",
						open_now: false,
					},
					{
						shift_name: "Afternoon",
						start_time: "13:00:00",
						end_time: "21:00:00",
						open_now: false,
					},
				],
			},
		})
		const wrapper = await mountOpenDialog()
		wrapper.vm.selectedProfile = PROFILES[0]
		await wrapper.vm.nextStep()
		await flushPromises()

		// Outside shift hours message shows
		expect(wrapper.text().toLowerCase()).toContain("outside shift hours")

		// Open Shift button is disabled
		const openButton = wrapper
			.findAll("button")
			.find((b) => b.text().includes("Open Shift"))
		expect(openButton.attributes("disabled")).toBeDefined()

		// Attempting to open does not call submit
		await wrapper.vm.openShift()
		await flushPromises()
		expect(shiftMocks.createOpeningShift.submit).not.toHaveBeenCalled()
	})

	it("profile without slots opens without a slot picker and does not send shift_slot", async () => {
		resourceImpls.byUrl[DIALOG_DATA_URL] = () => ({
			payments_method: [
				{
					name: "pm1",
					parent: "Profile 1",
					mode_of_payment: "Cash",
					is_drawer_cash: true,
				},
			],
			slots: { "Profile 1": [] },
		})
		const wrapper = await mountOpenDialog()
		wrapper.vm.selectedProfile = PROFILES[0]
		await wrapper.vm.nextStep()
		await flushPromises()

		expect(wrapper.find("select").exists()).toBe(false)
		expect(wrapper.text().toLowerCase()).not.toContain("outside shift hours")

		const openButton = wrapper
			.findAll("button")
			.find((b) => b.text().includes("Open Shift"))
		expect(openButton.attributes("disabled")).toBeUndefined()

		await wrapper.vm.openShift()
		await flushPromises()

		expect(shiftMocks.createOpeningShift.submit).toHaveBeenCalledWith({
			pos_profile: "Profile 1",
			company: "Test Co",
			balance_details: [{ mode_of_payment: "Cash", opening_amount: 0 }],
		})
	})

	describe("ShiftOpeningDialog cash handover from previous shift", () => {
		const DIALOG_DATA_URL = "pos_next.api.shifts.get_opening_dialog_data"

		const baseDialogData = (handover = null) => ({
			payments_method: [
				{
					name: "pm1",
					parent: "Profile 1",
					mode_of_payment: "Cash",
					is_drawer_cash: true,
				},
			],
			slots: { "Profile 1": [] },
			handover: {
				"Profile 1": handover,
			},
		})

		it("no handover shows Modal Awal and enables Open Shift with empty input", async () => {
			resourceImpls.byUrl[DIALOG_DATA_URL] = () => baseDialogData(null)
			const wrapper = await mountOpenDialog()
			wrapper.vm.selectedProfile = PROFILES[0]
			await wrapper.vm.nextStep()
			await flushPromises()

			expect(wrapper.vm.step).toBe(2)
			expect(wrapper.text()).toContain("Modal Awal")
			expect(wrapper.text()).not.toContain("Cash Received from Previous Shift")

			const openButton = wrapper
				.findAll("button")
				.find((b) => b.text().includes("Open Shift"))
			expect(openButton.attributes("disabled")).toBeUndefined()
		})

		it("handover switches label, shows System line with formatted amount, and disables Open Shift while empty", async () => {
			resourceImpls.byUrl[DIALOG_DATA_URL] = () =>
				baseDialogData({
					amounts: { Cash: 1000000 },
					user_full_name: "Kasir A",
					period_end_date: "2026-10-28 14:02:00",
					auto_closed: 0,
					shift_slot: "SHIFT 1",
				})
			const wrapper = await mountOpenDialog()
			wrapper.vm.selectedProfile = PROFILES[0]
			await wrapper.vm.nextStep()
			await flushPromises()

			expect(wrapper.vm.step).toBe(2)
			expect(wrapper.text()).toContain("Cash Received from Previous Shift")
			expect(wrapper.text()).not.toContain("Modal Awal")
			expect(wrapper.text()).toContain("Cash (SHIFT 1)")
			expect(wrapper.text()).not.toContain("(System)")
			expect(wrapper.text()).toContain("1000000")
			expect(wrapper.text()).toContain("Kasir A")

			const openButton = wrapper
				.findAll("button")
				.find((b) => b.text().includes("Open Shift"))
			expect(openButton.attributes("disabled")).toBeDefined()

			expect(wrapper.text()).toContain("Count the cash and enter the amount received")
		})

		it("typing 0 enables Open Shift and submits opening_amount 0", async () => {
			resourceImpls.byUrl[DIALOG_DATA_URL] = () =>
				baseDialogData({
					amounts: { Cash: 1000000 },
					user_full_name: "Kasir A",
					period_end_date: "2026-10-28 14:02:00",
					auto_closed: 0,
				})
			const wrapper = await mountOpenDialog()
			wrapper.vm.selectedProfile = PROFILES[0]
			await wrapper.vm.nextStep()
			await flushPromises()

			const input = wrapper.find("input")
			await input.setValue("0")
			await flushPromises()

			const openButton = wrapper
				.findAll("button")
				.find((b) => b.text().includes("Open Shift"))
			expect(openButton.attributes("disabled")).toBeUndefined()

			await wrapper.vm.openShift()
			await flushPromises()

			expect(shiftMocks.createOpeningShift.submit).toHaveBeenCalledWith(
				expect.objectContaining({
					balance_details: [{ mode_of_payment: "Cash", opening_amount: 0 }],
				}),
			)
		})

		it("typing 200000 shows Difference with negative difference, typing 1000000 hides it", async () => {
			resourceImpls.byUrl[DIALOG_DATA_URL] = () =>
				baseDialogData({
					amounts: { Cash: 1000000 },
					user_full_name: "Kasir A",
					period_end_date: "2026-10-28 14:02:00",
					auto_closed: 0,
				})
			const wrapper = await mountOpenDialog()
			wrapper.vm.selectedProfile = PROFILES[0]
			await wrapper.vm.nextStep()
			await flushPromises()

			const input = wrapper.find("input")
			await input.setValue("200000")
			await flushPromises()

			expect(wrapper.text()).toContain("Difference:")
			expect(wrapper.text()).toContain("-800000")

			await input.setValue("1000000")
			await flushPromises()

			expect(wrapper.text()).not.toContain("Difference:")
			expect(wrapper.text()).toContain("Matches system")
		})

		it("shows closed automatically when auto_closed is 1", async () => {
			resourceImpls.byUrl[DIALOG_DATA_URL] = () =>
				baseDialogData({
					amounts: { Cash: 1000000 },
					user_full_name: "Kasir A",
					period_end_date: "2026-10-28 14:02:00",
					auto_closed: 1,
				})
			const wrapper = await mountOpenDialog()
			wrapper.vm.selectedProfile = PROFILES[0]
			await wrapper.vm.nextStep()
			await flushPromises()

			expect(wrapper.text()).toContain("closed automatically")
		})

		it("clears opening balances when switching POS profile so handover validation re-checks for the new profile", async () => {
			resourceImpls.byUrl[DIALOG_DATA_URL] = () => ({
				payments_method: [
					{
						name: "pm1",
						parent: "Profile 1",
						mode_of_payment: "Cash",
						is_drawer_cash: true,
					},
					{
						name: "pm2",
						parent: "Profile 2",
						mode_of_payment: "Cash",
						is_drawer_cash: true,
					},
				],
				slots: { "Profile 1": [], "Profile 2": [] },
				handover: {
					"Profile 1": {
						amounts: { Cash: 1000000 },
						user_full_name: "Kasir A",
						period_end_date: "2026-10-28 14:02:00",
						auto_closed: 0,
					},
					"Profile 2": {
						amounts: { Cash: 500000 },
						user_full_name: "Kasir B",
						period_end_date: "2026-10-28 14:05:00",
						auto_closed: 0,
					},
				},
			})
			const wrapper = await mountOpenDialog()
			wrapper.vm.selectPosProfile(PROFILES[0])
			await wrapper.vm.nextStep()
			await flushPromises()

			expect(wrapper.vm.step).toBe(2)
			const input = wrapper.find("input")
			await input.setValue("200000")
			await flushPromises()

			expect(wrapper.vm.openingBalances.Cash).toBe("200.000")
			let openButton = wrapper
				.findAll("button")
				.find((b) => b.text().includes("Open Shift"))
			expect(openButton.attributes("disabled")).toBeUndefined()

			// Back to step 1
			wrapper.vm.step = 1
			await flushPromises()

			// Select Profile 2
			wrapper.vm.selectPosProfile(PROFILES[1])
			await flushPromises()

			// Balances must be cleared
			expect(wrapper.vm.openingBalances).toEqual({})

			// Proceed to step 2 for Profile 2
			await wrapper.vm.nextStep()
			await flushPromises()

			expect(wrapper.vm.handoverIncomplete).toBe(true)
			openButton = wrapper
				.findAll("button")
				.find((b) => b.text().includes("Open Shift"))
			expect(openButton.attributes("disabled")).toBeDefined()
		})
	})
})
