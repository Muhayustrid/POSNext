/**
 * @vitest-environment jsdom
 */
import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { defineComponent } from "vue"

const callMock = vi.hoisted(() => vi.fn(() => Promise.resolve(null)))
const toastMock = vi.hoisted(() => ({
	showSuccess: vi.fn(),
	showError: vi.fn(),
	showWarning: vi.fn(),
	showInfo: vi.fn(),
}))
const preparePhotoMock = vi.hoisted(() => vi.fn())
const checkVideoMock = vi.hoisted(() => vi.fn())
const uploadPrivateMock = vi.hoisted(() => vi.fn())
const removeOrphanFileMock = vi.hoisted(() => vi.fn())

vi.mock("@/utils/apiWrapper", () => ({
	call: (...args) => callMock(...args),
}))

vi.mock("@/composables/useToast", () => ({
	useToast: () => toastMock,
}))

vi.mock("@/utils/outletMonitoringMedia", () => ({
	preparePhoto: (...args) => preparePhotoMock(...args),
	checkVideo: (...args) => checkVideoMock(...args),
	uploadPrivate: (...args) => uploadPrivateMock(...args),
	removeOrphanFile: (...args) => removeOrphanFileMock(...args),
}))

vi.mock("frappe-ui", async () => {
	const stub = defineComponent({
		name: "FeatherIconStub",
		props: ["name"],
		render: () => null,
	})
	const Button = defineComponent({
		name: "Button",
		template: `<button v-bind="$attrs" @click="$emit('click')"><slot /></button>`,
	})
	return {
		FeatherIcon: stub,
		Button,
		Dialog: defineComponent({
			props: ["modelValue"],
			template:
				'<div v-if="modelValue"><slot name="body-content" /><slot name="actions" /></div>',
		}),
	}
})

globalThis.__ = (msg, replacements = []) => {
	if (!Array.isArray(replacements) || !replacements.length) return msg
	let out = msg
	for (const [i, v] of replacements.entries()) {
		out = out.split(`{${i}}`).join(String(v))
	}
	return out
}

import OutletMonitoringDialog from "./OutletMonitoringDialog.vue"

describe("OutletMonitoringDialog", () => {
	beforeEach(() => {
		setActivePinia(createPinia())
		vi.clearAllMocks()
	})

	it("evidence complete tick does not render Pass styling before review", async () => {
		const reportData = {
			name: "OM-REP-001",
			template: "Daily Check",
			status: "Draft",
			company: "Roti Ropi",
			pos_profile: "POS-1",
			operational_date: "2026-11-05",
			due_at: "2026-11-05 10:00:00",
			checkpoints: [
				{
					name: "cp-row-1",
					checkpoint: "CP-1",
					checkpoint_name: "Clean Display",
					instruction: "Check shelves",
					required: 1,
					min_count: 1,
					max_count: 2,
					media_type: "Photo",
				},
			],
			media: [
				{
					name: "med-1",
					checkpoint_row: "cp-row-1",
					file_name: "FILE-1",
					file_url: "/private/files/shelves.jpg",
					media_type: "Photo",
					purged: 0,
				},
			],
		}

		callMock.mockImplementation((method, args) => {
			if (method.endsWith("get_my_tasks")) {
				return Promise.resolve({
					enabled: true,
					config: { show_menu_badge: 1 },
					count: 1,
					tasks: [
						{
							name: "OM-REP-001",
							template: "Daily Check",
							required_done: 1,
							required_total: 1,
						},
					],
				})
			}
			if (method.endsWith("get_report")) {
				return Promise.resolve(reportData)
			}
			return Promise.resolve(null)
		})

		const wrapper = mount(OutletMonitoringDialog, {
			props: {
				show: true,
				embedded: true,
				posProfile: "POS-1",
			},
			global: {
				config: { globalProperties: { __: globalThis.__ } },
			},
		})

		await flushPromises()

		// Click "Fill Report" to enter doc view
		const fillBtn = wrapper.find('[data-testid="fill-report-btn"]')
		if (fillBtn.exists()) {
			await fillBtn.trigger("click")
			await flushPromises()
		}

		// Find the complete tick/badge for this checkpoint
		const completeBadge = wrapper.find(
			'[data-testid="evidence-complete-badge"]',
		)
		expect(completeBadge.exists()).toBe(true)
		// Should NOT have green Pass classes before review
		expect(completeBadge.classes()).not.toContain("bg-green-50")
		expect(completeBadge.classes()).not.toContain("text-green-700")
		expect(completeBadge.classes()).not.toContain("text-green-600")
	})

	it("submit button disabled until all required checkpoints complete", async () => {
		const reportDataIncomplete = {
			name: "OM-REP-002",
			template: "Closing Check",
			status: "Draft",
			checkpoints: [
				{
					name: "cp-row-1",
					checkpoint_name: "Cash Drawer",
					required: 1,
					min_count: 1,
					max_count: 1,
					media_type: "Photo",
				},
			],
			media: [],
		}

		callMock.mockImplementation((method) => {
			if (method.endsWith("get_my_tasks")) {
				return Promise.resolve({
					enabled: true,
					count: 1,
					tasks: [{ name: "OM-REP-002", template: "Closing Check" }],
				})
			}
			if (method.endsWith("get_report")) {
				return Promise.resolve(reportDataIncomplete)
			}
			return Promise.resolve(null)
		})

		const wrapper = mount(OutletMonitoringDialog, {
			props: {
				show: true,
				embedded: true,
				posProfile: "POS-1",
			},
			global: {
				config: { globalProperties: { __: globalThis.__ } },
			},
		})

		await flushPromises()
		const fillBtn = wrapper.find('[data-testid="fill-report-btn"]')
		if (fillBtn.exists()) {
			await fillBtn.trigger("click")
			await flushPromises()
		}

		// Actions sit top-right in the report header, like Stock Opname / PO.
		const submitBtn = wrapper.find(
			'[data-testid="report-header"] [data-testid="submit-report-btn"]',
		)
		expect(submitBtn.exists()).toBe(true)
		expect(submitBtn.attributes("disabled")).toBeDefined()
	})

	it("deletes orphan upload when register fails", async () => {
		const reportData = {
			name: "OM-REP-003",
			template: "Daily Opening",
			status: "Draft",
			checkpoints: [
				{
					name: "cp-row-1",
					checkpoint_name: "Signboard",
					required: 1,
					min_count: 1,
					max_count: 1,
					media_type: "Photo",
				},
			],
			media: [],
		}

		callMock.mockImplementation((method) => {
			if (method.endsWith("get_my_tasks")) {
				return Promise.resolve({
					enabled: true,
					count: 1,
					tasks: [{ name: "OM-REP-003", template: "Daily Opening" }],
				})
			}
			if (method.endsWith("get_report")) {
				return Promise.resolve(reportData)
			}
			if (method.endsWith("register_media")) {
				return Promise.reject(new Error("Server validation error"))
			}
			return Promise.resolve(null)
		})

		preparePhotoMock.mockResolvedValue({
			main: new File(["main"], "main.jpg", { type: "image/jpeg" }),
			thumb: new File(["thumb"], "thumb.jpg", { type: "image/jpeg" }),
		})
		uploadPrivateMock
			.mockResolvedValueOnce({
				name: "FILE-ORPHAN-MAIN",
				file_url: "/private/files/main.jpg",
			})
			.mockResolvedValueOnce({
				name: "FILE-ORPHAN-THUMB",
				file_url: "/private/files/thumb.jpg",
			})

		const wrapper = mount(OutletMonitoringDialog, {
			props: {
				show: true,
				embedded: true,
				posProfile: "POS-1",
			},
			global: {
				config: { globalProperties: { __: globalThis.__ } },
			},
		})

		await flushPromises()
		const fillBtn = wrapper.find('[data-testid="fill-report-btn"]')
		if (fillBtn.exists()) {
			await fillBtn.trigger("click")
			await flushPromises()
		}

		// Trigger file input upload
		const fileInput = wrapper.find('input[type="file"]')
		expect(fileInput.exists()).toBe(true)

		const file = new File(["test"], "photo.jpg", { type: "image/jpeg" })
		// Simulate file selection
		await wrapper.vm.handleMediaUpload(file, reportData.checkpoints[0])
		await flushPromises()

		// Verify orphan files were cleaned up
		expect(removeOrphanFileMock).toHaveBeenCalledWith("FILE-ORPHAN-MAIN")
		expect(removeOrphanFileMock).toHaveBeenCalledWith("FILE-ORPHAN-THUMB")
		expect(toastMock.showError).toHaveBeenCalled()
	})

	it("history shows Needs Correction and Create Revision when correction_requested", async () => {
		callMock.mockImplementation((method) => {
			if (method.endsWith("get_my_tasks")) {
				return Promise.resolve({ enabled: true, count: 0, tasks: [] })
			}
			if (method.endsWith("get_history")) {
				return Promise.resolve({
					items: [
						{
							name: "OM-REP-REV-1",
							template: "Daily Closing",
							status: "Reviewed",
							review_result: "Fail",
							correction_requested: 1,
							review_note: "Please retake photo of cash register",
							operational_date: "2026-11-04",
						},
					],
					has_more: false,
				})
			}
			return Promise.resolve(null)
		})

		const wrapper = mount(OutletMonitoringDialog, {
			props: {
				show: true,
				embedded: true,
				posProfile: "POS-1",
			},
			global: {
				config: { globalProperties: { __: globalThis.__ } },
			},
		})

		await flushPromises()

		// Switch to history tab
		const historyTabBtn = wrapper.find('[data-testid="tab-history"]')
		expect(historyTabBtn.exists()).toBe(true)
		await historyTabBtn.trigger("click")
		await flushPromises()

		expect(wrapper.text()).toContain("Needs Correction")
		expect(wrapper.text()).toContain("Please retake photo of cash register")

		const createRevisionBtn = wrapper.find(
			'[data-testid="create-revision-btn"]',
		)
		expect(createRevisionBtn.exists()).toBe(true)
	})

	it("shows label chips only when cashier_can_label and Draft", async () => {
		const draftReport = {
			name: "OM-REP-CHIPS",
			template: "Daily Check",
			status: "Draft",
			checkpoints: [
				{
					name: "cp-row-1",
					checkpoint: "CP-1",
					checkpoint_name: "Clean Display",
					cashier_can_label: 1,
					label_options: [
						{ label: "Sesuai SOP", color: "Green" },
						{ label: "Tidak Sesuai SOP", color: "Red" },
					],
					cashier_label: null,
					required: 0,
					min_count: 0,
					max_count: 1,
					media_type: "Photo",
				},
				{
					name: "cp-row-2",
					checkpoint: "CP-2",
					checkpoint_name: "Floor",
					cashier_can_label: 0,
					label_options: [{ label: "Sesuai SOP", color: "Green" }],
					cashier_label: null,
					required: 0,
					min_count: 0,
					max_count: 1,
					media_type: "Photo",
				},
			],
			media: [],
		}

		callMock.mockImplementation((method) => {
			if (method.endsWith("get_my_tasks")) {
				return Promise.resolve({
					enabled: true,
					count: 1,
					tasks: [{ name: "OM-REP-CHIPS", template: "Daily Check" }],
				})
			}
			if (method.endsWith("get_report")) {
				return Promise.resolve(draftReport)
			}
			return Promise.resolve(null)
		})

		const wrapper = mount(OutletMonitoringDialog, {
			props: { show: true, embedded: true, posProfile: "POS-1" },
			global: {
				config: { globalProperties: { __: globalThis.__ } },
			},
		})

		await flushPromises()
		await wrapper.find('[data-testid="fill-report-btn"]').trigger("click")
		await flushPromises()

		const chips = wrapper.findAll('[data-testid="label-chip"]')
		expect(chips.length).toBe(2)
		expect(chips[0].text()).toBe("Sesuai SOP")
		expect(chips[1].text()).toBe("Tidak Sesuai SOP")
		// Chips are colored from the snapshot palette
		expect(chips[0].classes()).toContain("bg-green-50")
		expect(chips[1].classes()).toContain("bg-red-50")
	})

	it("tap chip calls set_cashier_label, tap again clears (label null)", async () => {
		const draftReport = {
			name: "OM-REP-TOGGLE",
			template: "Daily Check",
			status: "Draft",
			checkpoints: [
				{
					name: "cp-row-1",
					checkpoint: "CP-1",
					checkpoint_name: "Clean Display",
					cashier_can_label: 1,
					label_options: [
						{ label: "Sesuai SOP", color: "Green" },
						{ label: "Tidak Sesuai SOP", color: "Red" },
					],
					cashier_label: null,
					required: 0,
					min_count: 0,
					max_count: 1,
					media_type: "Photo",
				},
			],
			media: [],
		}

		callMock.mockImplementation((method, args) => {
			if (method.endsWith("get_my_tasks")) {
				return Promise.resolve({
					enabled: true,
					count: 1,
					tasks: [{ name: "OM-REP-TOGGLE", template: "Daily Check" }],
				})
			}
			if (method.endsWith("get_report")) {
				return Promise.resolve(draftReport)
			}
			if (method.endsWith("set_cashier_label")) {
				draftReport.checkpoints[0].cashier_label = args.label ?? null
				return Promise.resolve({ ...draftReport })
			}
			return Promise.resolve(null)
		})

		const wrapper = mount(OutletMonitoringDialog, {
			props: { show: true, embedded: true, posProfile: "POS-1" },
			global: {
				config: { globalProperties: { __: globalThis.__ } },
			},
		})

		await flushPromises()
		await wrapper.find('[data-testid="fill-report-btn"]').trigger("click")
		await flushPromises()

		const chips = wrapper.findAll('[data-testid="label-chip"]')
		await chips[0].trigger("click")
		await flushPromises()

		expect(callMock).toHaveBeenCalledWith(
			"pos_next.api.outlet_monitoring.set_cashier_label",
			{
				report: "OM-REP-TOGGLE",
				checkpoint_row: "cp-row-1",
				label: "Sesuai SOP",
			},
		)
		expect(
			wrapper
				.findAll('[data-testid="label-chip"]')[0]
				.attributes("aria-pressed"),
		).toBe("true")

		// Tap again clears the choice (label null)
		await wrapper.findAll('[data-testid="label-chip"]')[0].trigger("click")
		await flushPromises()

		expect(callMock).toHaveBeenCalledWith(
			"pos_next.api.outlet_monitoring.set_cashier_label",
			{ report: "OM-REP-TOGGLE", checkpoint_row: "cp-row-1", label: null },
		)
		expect(
			wrapper
				.findAll('[data-testid="label-chip"]')[0]
				.attributes("aria-pressed"),
		).toBe("false")
	})

	it("reviewed report shows label badge with snapshot color", async () => {
		const reviewedReport = {
			name: "OM-REP-REV-DETAIL",
			template: "Daily Closing",
			status: "Reviewed",
			correction_requested: 0,
			operational_date: "2026-11-04",
			checkpoints: [
				{
					name: "cp-1",
					checkpoint: "CP-1",
					checkpoint_name: "Cash Drawer",
					cashier_can_label: 1,
					label_options: [
						{ label: "Sesuai Standar", color: "Green" },
						{ label: "Tidak Standar", color: "Red" },
					],
					cashier_label: "Sesuai Standar",
					label: "Sesuai Standar",
					label_color: "Green",
					assessment_note: "",
					required: 1,
					min_count: 1,
					max_count: 1,
					media_type: "Photo",
				},
				{
					name: "cp-2",
					checkpoint: "CP-2",
					checkpoint_name: "Floor Cleanliness",
					cashier_can_label: 1,
					label_options: [
						{ label: "Bersih", color: "Green" },
						{ label: "Kotor / Perlu Tindakan", color: "Red" },
					],
					cashier_label: null,
					label: "Kotor / Perlu Tindakan",
					label_color: "Red",
					assessment_note: "Area kasir berdebu dan sampah berserakan",
					required: 1,
					min_count: 1,
					max_count: 1,
					media_type: "Photo",
				},
			],
			media: [],
		}

		callMock.mockImplementation((method) => {
			if (method.endsWith("get_my_tasks")) {
				return Promise.resolve({ enabled: true, count: 0, tasks: [] })
			}
			if (method.endsWith("get_history")) {
				return Promise.resolve({
					items: [
						{
							name: "OM-REP-REV-DETAIL",
							template: "Daily Closing",
							status: "Reviewed",
							correction_requested: 0,
							operational_date: "2026-11-04",
						},
					],
					has_more: false,
				})
			}
			if (method.endsWith("get_report")) {
				return Promise.resolve(reviewedReport)
			}
			return Promise.resolve(null)
		})

		const wrapper = mount(OutletMonitoringDialog, {
			props: {
				show: true,
				embedded: true,
				posProfile: "POS-1",
			},
			global: {
				config: { globalProperties: { __: globalThis.__ } },
			},
		})

		await flushPromises()
		await wrapper.find('[data-testid="tab-history"]').trigger("click")
		await flushPromises()

		const viewBtn = wrapper.find('[data-testid="view-report-btn"]')
		expect(viewBtn.exists()).toBe(true)
		await viewBtn.trigger("click")
		await flushPromises()

		const badges = wrapper.findAll('[data-testid="checkpoint-label-badge"]')
		expect(badges.length).toBe(2)
		expect(badges[0].text()).toBe("Sesuai Standar")
		expect(badges[0].classes()).toContain("bg-green-50")
		expect(badges[1].text()).toBe("Kotor / Perlu Tindakan")
		expect(badges[1].classes()).toContain("bg-red-50")

		const note = wrapper.find('[data-testid="checkpoint-assessment-note"]')
		expect(note.exists()).toBe(true)
		expect(note.text()).toContain("Area kasir berdebu dan sampah berserakan")

		// No editable chips after review
		expect(wrapper.findAll('[data-testid="label-chip"]').length).toBe(0)
	})

	it("history uses neutral status, no Non-Compliant", async () => {
		callMock.mockImplementation((method) => {
			if (method.endsWith("get_my_tasks")) {
				return Promise.resolve({ enabled: true, count: 0, tasks: [] })
			}
			if (method.endsWith("get_history")) {
				return Promise.resolve({
					items: [
						{
							name: "OM-REP-NEUTRAL",
							template: "Daily Closing",
							status: "Reviewed",
							correction_requested: 0,
							operational_date: "2026-11-04",
						},
					],
					has_more: false,
				})
			}
			return Promise.resolve(null)
		})

		const wrapper = mount(OutletMonitoringDialog, {
			props: { show: true, embedded: true, posProfile: "POS-1" },
			global: {
				config: { globalProperties: { __: globalThis.__ } },
			},
		})

		await flushPromises()
		await wrapper.find('[data-testid="tab-history"]').trigger("click")
		await flushPromises()

		expect(wrapper.text()).toContain("Reviewed")
		expect(wrapper.text()).not.toContain("Non-Compliant")
		expect(wrapper.text()).not.toContain("Compliant")
	})

	it("Mulai Laporan: template -> candidate search -> opens Draft report", async () => {
		callMock.mockImplementation((method) => {
			if (method.endsWith("get_my_tasks")) {
				return Promise.resolve({ enabled: true, count: 0, tasks: [] })
			}
			if (method.endsWith("get_manual_templates")) {
				return Promise.resolve([
					{ name: "TPL-UNBOX", template_name: "Unboxing Barang" },
				])
			}
			if (method.endsWith("get_reference_candidates")) {
				return Promise.resolve([
					{
						doctype: "Purchase Receipt",
						name: "PR-001",
						posting_date: "2026-11-06",
						sender: "Supplier A",
						item_count: 3,
					},
				])
			}
			if (method.endsWith("start_manual_report")) {
				return Promise.resolve({
					name: "OM-MAN-001",
					template: "Unboxing Barang",
					status: "Draft",
					checkpoints: [],
					company: "OUTLET",
					pos_profile: "POS-1",
					operational_date: "2026-11-06",
				})
			}
			if (method.endsWith("get_report")) {
				return Promise.resolve({
					name: "OM-MAN-001",
					template: "Unboxing Barang",
					status: "Draft",
					checkpoints: [],
				})
			}
			return Promise.resolve(null)
		})

		const wrapper = mount(OutletMonitoringDialog, {
			props: { show: true, embedded: true, posProfile: "POS-1" },
			global: {
				config: { globalProperties: { __: globalThis.__ } },
			},
		})

		await flushPromises()

		const startBtn = wrapper.find('[data-testid="start-manual-btn"]')
		expect(startBtn.exists()).toBe(true)
		await startBtn.trigger("click")
		await flushPromises()

		const tplBtn = wrapper.find('[data-testid="manual-template-btn"]')
		expect(tplBtn.exists()).toBe(true)
		await tplBtn.trigger("click")
		await flushPromises()

		expect(callMock).toHaveBeenCalledWith(
			"pos_next.api.outlet_monitoring.get_reference_candidates",
			expect.objectContaining({ template: "TPL-UNBOX" }),
		)

		const search = wrapper.find('[data-testid="candidate-search"]')
		expect(search.exists()).toBe(true)
		await search.setValue("PR-00")
		await flushPromises()

		const candBtn = wrapper.find('[data-testid="candidate-btn"]')
		expect(candBtn.exists()).toBe(true)
		expect(candBtn.text()).toContain("PR-001")
		await candBtn.trigger("click")
		await flushPromises()

		expect(callMock).toHaveBeenCalledWith(
			"pos_next.api.outlet_monitoring.start_manual_report",
			{
				pos_profile: "POS-1",
				template: "TPL-UNBOX",
				reference_doctype: "Purchase Receipt",
				reference_name: "PR-001",
			},
		)
		expect(wrapper.find('[data-testid="report-header"]').exists()).toBe(true)
		expect(wrapper.text()).toContain("Unboxing Barang")
	})

	it("candidate search shows empty state when no documents found", async () => {
		callMock.mockImplementation((method) => {
			if (method.endsWith("get_my_tasks")) {
				return Promise.resolve({ enabled: true, count: 0, tasks: [] })
			}
			if (method.endsWith("get_manual_templates")) {
				return Promise.resolve([
					{ name: "TPL-UNBOX", template_name: "Unboxing Barang" },
				])
			}
			if (method.endsWith("get_reference_candidates")) {
				return Promise.resolve([])
			}
			return Promise.resolve(null)
		})

		const wrapper = mount(OutletMonitoringDialog, {
			props: { show: true, embedded: true, posProfile: "POS-1" },
			global: {
				config: { globalProperties: { __: globalThis.__ } },
			},
		})

		await flushPromises()
		await wrapper.find('[data-testid="start-manual-btn"]').trigger("click")
		await flushPromises()
		await wrapper.find('[data-testid="manual-template-btn"]').trigger("click")
		await flushPromises()

		expect(wrapper.find('[data-testid="candidate-btn"]')).not.toBeNull()
		expect(wrapper.findAll('[data-testid="candidate-btn"]').length).toBe(0)
		expect(wrapper.text()).toContain("No documents found")
	})

	it("task before opens_at shows 'Starts {time}' and disables fill actions", async () => {
		callMock.mockImplementation((method) => {
			if (method.endsWith("get_my_tasks")) {
				return Promise.resolve({
					enabled: true,
					count: 2,
					server_now: "2026-11-07 08:00:00",
					tasks: [
						{
							name: "OM-REP-FUTURE",
							template: "Morning Check",
							required_done: 0,
							required_total: 1,
							opens_at: "2999-01-01 08:00:00",
						},
						{
							name: "OM-REP-OPEN",
							template: "Evening Check",
							required_done: 0,
							required_total: 1,
							opens_at: "2020-01-01 08:00:00",
						},
					],
				})
			}
			return Promise.resolve(null)
		})

		const wrapper = mount(OutletMonitoringDialog, {
			props: { show: true, embedded: true, posProfile: "POS-1" },
			global: {
				config: { globalProperties: { __: globalThis.__ } },
			},
		})

		await flushPromises()

		expect(wrapper.text()).toContain("Starts 08:00")
		const fillBtns = wrapper.findAll('[data-testid="fill-report-btn"]')
		expect(fillBtns.length).toBe(2)
		expect(fillBtns[0].attributes("disabled")).toBeDefined()
		expect(fillBtns[1].attributes("disabled")).toBeUndefined()
	})

	it("opens_at is compared in server time, not device time", async () => {
		// Site is UTC, device is New York. At the frozen instant the server is
		// already at/past opens_at (08:01 site) so the task is open, but the
		// device clock reads 03:01 — a device-clock compare of the site-local
		// string would read "08:01" as 08:01 EST and wrongly keep it locked.
		const realTz = process.env.TZ
		process.env.TZ = "America/New_York"
		try {
			callMock.mockImplementation((method) => {
				if (method.endsWith("get_my_tasks")) {
					return Promise.resolve({
						enabled: true,
						count: 1,
						server_now: "2026-11-07 08:01:00",
						tasks: [
							{
								name: "OM-REP-TZ",
								template: "Morning Check",
								required_done: 0,
								required_total: 1,
								opens_at: "2026-11-07 08:01:00",
							},
						],
					})
				}
				return Promise.resolve(null)
			})
			// 08:01 UTC == 03:01 EST, the wall time the device actually shows.
			vi.useFakeTimers({ toFake: ["Date"] })
			vi.setSystemTime(new Date("2026-11-07T08:01:00Z"))

			const wrapper = mount(OutletMonitoringDialog, {
				props: { show: true, embedded: true, posProfile: "POS-1" },
				global: { config: { globalProperties: { __: globalThis.__ } } },
			})
			await flushPromises()

			const fillBtn = wrapper.find('[data-testid="fill-report-btn"]')
			expect(fillBtn.attributes("disabled")).toBeUndefined()
			wrapper.unmount()
		} finally {
			vi.useRealTimers()
			if (realTz === undefined) Reflect.deleteProperty(process.env, "TZ")
			else process.env.TZ = realTz
		}
	})

	it("open dialog unlocks at opens_at without reopening", async () => {
		vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"] })
		try {
			// Server now 07:59:30, task opens 08:00:00 site time.
			vi.setSystemTime(new Date("2026-11-07T07:59:30Z"))
			callMock.mockImplementation((method) => {
				if (method.endsWith("get_my_tasks")) {
					return Promise.resolve({
						enabled: true,
						count: 1,
						server_now: "2026-11-07 07:59:30",
						tasks: [
							{
								name: "OM-REP-SOON",
								template: "Opening Check",
								required_done: 0,
								required_total: 1,
								opens_at: "2026-11-07 08:00:00",
							},
						],
					})
				}
				return Promise.resolve(null)
			})

			const wrapper = mount(OutletMonitoringDialog, {
				props: { show: true, embedded: true, posProfile: "POS-1" },
				global: { config: { globalProperties: { __: globalThis.__ } } },
			})
			await vi.advanceTimersByTimeAsync(0)
			await flushPromises()

			// Locked one minute before opens_at.
			expect(
				wrapper.find('[data-testid="fill-report-btn"]').attributes("disabled"),
			).toBeDefined()

			// Cross the boundary with the dialog still mounted: the tick lifts it.
			await vi.advanceTimersByTimeAsync(60_000)
			await flushPromises()
			expect(
				wrapper.find('[data-testid="fill-report-btn"]').attributes("disabled"),
			).toBeUndefined()
			wrapper.unmount()
		} finally {
			vi.useRealTimers()
		}
	})

	it.each([
		["missing", undefined],
		["invalid", "not-a-datetime"],
	])(
		"a %s server_now keeps timed tasks locked and offers a retry",
		async (_label, serverNow) => {
			callMock.mockImplementation((method) => {
				if (method.endsWith("get_my_tasks")) {
					return Promise.resolve({
						enabled: true,
						count: 1,
						server_now: serverNow,
						tasks: [
							{
								name: "OM-REP-NOANCHOR",
								template: "Opening Check",
								required_done: 0,
								required_total: 1,
								// Long past: only an anchored clock may unlock this.
								opens_at: "2020-01-01 08:00:00",
							},
						],
					})
				}
				return Promise.resolve(null)
			})

			const wrapper = mount(OutletMonitoringDialog, {
				props: { show: true, embedded: true, posProfile: "POS-1" },
				global: { config: { globalProperties: { __: globalThis.__ } } },
			})
			await flushPromises()

			expect(
				wrapper.find('[data-testid="fill-report-btn"]').attributes("disabled"),
			).toBeDefined()
			expect(wrapper.find('[data-testid="clock-unavailable"]').exists()).toBe(
				true,
			)

			callMock.mockClear()
			await wrapper.find('[data-testid="clock-retry-btn"]').trigger("click")
			await flushPromises()
			expect(callMock).toHaveBeenCalledWith(
				"pos_next.api.outlet_monitoring.get_my_tasks",
				{ pos_profile: "POS-1" },
			)
			wrapper.unmount()
		},
	)

	it("a valid anchor clears the retry notice and unlocks the task", async () => {
		callMock.mockImplementation((method) => {
			if (method.endsWith("get_my_tasks")) {
				return Promise.resolve({
					enabled: true,
					count: 1,
					server_now: "2026-11-07 08:00:00",
					tasks: [
						{
							name: "OM-REP-ANCHORED",
							template: "Opening Check",
							required_done: 0,
							required_total: 1,
							opens_at: "2026-11-07 08:00:00",
						},
					],
				})
			}
			return Promise.resolve(null)
		})

		const wrapper = mount(OutletMonitoringDialog, {
			props: { show: true, embedded: true, posProfile: "POS-1" },
			global: { config: { globalProperties: { __: globalThis.__ } } },
		})
		await flushPromises()

		expect(wrapper.find('[data-testid="clock-unavailable"]').exists()).toBe(
			false,
		)
		expect(
			wrapper.find('[data-testid="fill-report-btn"]').attributes("disabled"),
		).toBeUndefined()
		wrapper.unmount()
	})

	it("friendly error toast when the document was already reported", async () => {
		callMock.mockImplementation((method) => {
			if (method.endsWith("get_my_tasks")) {
				return Promise.resolve({ enabled: true, count: 0, tasks: [] })
			}
			if (method.endsWith("get_manual_templates")) {
				return Promise.resolve([
					{ name: "TPL-UNBOX", template_name: "Unboxing Barang" },
				])
			}
			if (method.endsWith("get_reference_candidates")) {
				return Promise.resolve([
					{
						doctype: "Delivery Note",
						name: "DN-001",
						posting_date: "2026-11-06",
						sender: "HQ",
						item_count: 5,
					},
				])
			}
			if (method.endsWith("start_manual_report")) {
				return Promise.reject(
					new Error("This document has already been reported"),
				)
			}
			return Promise.resolve(null)
		})

		const wrapper = mount(OutletMonitoringDialog, {
			props: { show: true, embedded: true, posProfile: "POS-1" },
			global: {
				config: { globalProperties: { __: globalThis.__ } },
			},
		})

		await flushPromises()
		await wrapper.find('[data-testid="start-manual-btn"]').trigger("click")
		await flushPromises()
		await wrapper.find('[data-testid="manual-template-btn"]').trigger("click")
		await flushPromises()
		await wrapper.find('[data-testid="candidate-btn"]').trigger("click")
		await flushPromises()

		expect(toastMock.showError).toHaveBeenCalledWith(
			"This document has already been reported",
		)
	})

	it("previews photo and video media in report detail modal without autoloading video in list", async () => {
		const reportWithMedia = {
			name: "OM-REP-PREVIEW",
			template: "Media Check",
			status: "Draft",
			checkpoints: [
				{
					name: "cp-photo",
					checkpoint: "CP-PHOTO",
					checkpoint_name: "Shelf Photo",
					media_type: "Photo",
					required: 1,
					min_count: 1,
					max_count: 1,
				},
				{
					name: "cp-video",
					checkpoint: "CP-VIDEO",
					checkpoint_name: "Walkthrough Video",
					media_type: "Video",
					required: 1,
					min_count: 1,
					max_count: 1,
				},
			],
			media: [
				{
					name: "med-photo-1",
					checkpoint_row: "cp-photo",
					file_url: "/private/files/shelf_full.jpg",
					thumbnail_url: "/private/files/shelf_thumb.jpg",
					media_type: "Photo",
					purged: 0,
				},
				{
					name: "med-video-1",
					checkpoint_row: "cp-video",
					file_url: "/private/files/walkthrough.mp4",
					thumbnail_url: null,
					media_type: "Video",
					purged: 0,
				},
			],
		}

		callMock.mockImplementation((method) => {
			if (method.endsWith("get_my_tasks")) {
				return Promise.resolve({
					enabled: true,
					count: 1,
					tasks: [{ name: "OM-REP-PREVIEW", template: "Media Check" }],
				})
			}
			if (method.endsWith("get_report")) {
				return Promise.resolve(reportWithMedia)
			}
			return Promise.resolve(null)
		})

		const wrapper = mount(OutletMonitoringDialog, {
			props: {
				show: true,
				embedded: true,
				posProfile: "POS-1",
			},
			global: {
				config: { globalProperties: { __: globalThis.__ } },
			},
		})

		await flushPromises()
		const fillBtn = wrapper.find('[data-testid="fill-report-btn"]')
		if (fillBtn.exists()) {
			await fillBtn.trigger("click")
			await flushPromises()
		}

		// In list, video must NOT be autoloaded as <video> tag
		const listVideos = wrapper.findAll("video")
		expect(listVideos.length).toBe(0)

		// Find preview buttons
		const previewBtns = wrapper.findAll('[data-testid="media-preview-btn"]')
		expect(previewBtns.length).toBe(2)
		expect(previewBtns[0].attributes("aria-label")).toBe("Preview Photo")
		expect(previewBtns[1].attributes("aria-label")).toBe("Preview Video")

		// Click photo preview
		await previewBtns[0].trigger("click")
		await flushPromises()

		const photoModal = document.querySelector(
			'[data-testid="outlet-media-preview-modal"]',
		)
		expect(photoModal).not.toBeNull()
		// Must stack above the management menu shell (z-[300], POSMenuDialog.vue).
		expect(photoModal.classList.contains("z-[600]")).toBe(true)
		const img = photoModal.querySelector('[data-testid="preview-image"]')
		expect(img).not.toBeNull()
		expect(img.getAttribute("src")).toBe("/private/files/shelf_full.jpg")

		// Close preview
		const closeBtn = photoModal.querySelector(
			'[data-testid="close-preview-btn"]',
		)
		expect(closeBtn).not.toBeNull()
		closeBtn.click()
		await flushPromises()
		expect(
			document.querySelector('[data-testid="outlet-media-preview-modal"]'),
		).toBeNull()

		// Click video preview
		await previewBtns[1].trigger("click")
		await flushPromises()

		const videoModal = document.querySelector(
			'[data-testid="outlet-media-preview-modal"]',
		)
		expect(videoModal).not.toBeNull()
		const video = videoModal.querySelector('[data-testid="preview-video"]')
		expect(video).not.toBeNull()
		expect(video.getAttribute("src")).toBe("/private/files/walkthrough.mp4")
		expect(video.getAttribute("controls")).not.toBeNull()
		expect(video.getAttribute("preload")).toBe("metadata")
	})
})
