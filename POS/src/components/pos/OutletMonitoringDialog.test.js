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

		const submitBtn = wrapper.find('[data-testid="submit-report-btn"]')
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

	it("shows assessment badge, resolved custom label, and assessment note on reviewed report detail", async () => {
		const reviewedReport = {
			name: "OM-REP-REV-DETAIL",
			template: "Daily Closing",
			status: "Reviewed",
			review_result: "Fail",
			correction_requested: 1,
			review_note: "Please clean area",
			operational_date: "2026-11-04",
			checkpoints: [
				{
					name: "cp-1",
					checkpoint: "CP-1",
					checkpoint_name: "Cash Drawer",
					pass_label: "Sesuai Standar",
					fail_label: "Tidak Standar",
					assessment: "Pass",
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
					pass_label: "Bersih",
					fail_label: "Kotor / Perlu Tindakan",
					assessment: "Fail",
					assessment_note: "Area kasir berdebu dan sampah berserakan",
					required: 1,
					min_count: 1,
					max_count: 1,
					media_type: "Photo",
				},
			],
			media: [
				{
					name: "med-1",
					checkpoint_row: "cp-1",
					file_url: "/private/files/drawer.jpg",
					media_type: "Photo",
					purged: 0,
				},
				{
					name: "med-2",
					checkpoint_row: "cp-2",
					file_url: "/private/files/floor.jpg",
					media_type: "Photo",
					purged: 0,
				},
			],
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
							review_result: "Fail",
							correction_requested: 1,
							review_note: "Please clean area",
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

		const badges = wrapper.findAll(
			'[data-testid="checkpoint-assessment-badge"]',
		)
		expect(badges.length).toBe(2)
		expect(badges[0].text()).toBe("Sesuai Standar")
		expect(badges[0].classes()).toContain("bg-green-50")
		expect(badges[1].text()).toBe("Kotor / Perlu Tindakan")
		expect(badges[1].classes()).toContain("bg-red-50")

		const note = wrapper.find('[data-testid="checkpoint-assessment-note"]')
		expect(note.exists()).toBe(true)
		expect(note.text()).toContain("Area kasir berdebu dan sampah berserakan")
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
