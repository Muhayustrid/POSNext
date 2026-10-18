/**
 * @vitest-environment jsdom
 *
 * Covers the Purchase Receipt dialog: list render, row click/enter -> doc view,
 * header submit action by state, workflow action labels, submitted read-only state,
 * confirmation before submit, and attachments.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"

vi.hoisted(() => {
	globalThis.__ = (message, replacements = []) => {
		if (!Array.isArray(replacements) || !replacements.length) return message
		let out = message
		for (const [i, v] of replacements.entries())
			out = out.split(`{${i}}`).join(String(v))
		return out
	}
})

const mocks = vi.hoisted(() => ({
	call: vi.fn(),
	toast: { success: vi.fn(), error: vi.fn() },
	perms: { submit: true, create: true },
}))

vi.mock("@/utils/apiWrapper", async (importOriginal) => ({
	...(await importOriginal()),
	call: mocks.call,
}))

vi.mock("@/composables/useToast", () => ({
	useToast: () => ({
		showSuccess: mocks.toast.success,
		showError: mocks.toast.error,
		showWarning: vi.fn(),
		showInfo: vi.fn(),
	}),
}))

vi.mock("@/composables/usePermissions", async () => {
	const { computed } = await import("vue")
	return {
		usePermissions: () => ({
			usePermissionCheck: (_doctype, ptype) => ({
				hasPermission: computed(() => mocks.perms[ptype] ?? false),
			}),
		}),
	}
})

vi.mock("@/utils/attachments", async (importOriginal) => {
	const actual = await importOriginal()
	return {
		...actual,
		uploadPurchaseFiles: (doctype, name, files) =>
			mocks.call("pos_next.api.purchase_orders.attach_purchase_files", {
				doctype,
				name,
				files,
			}),
	}
})

vi.mock("frappe-ui", async () => {
	const { defineComponent, h } = await import("vue")
	const Button = defineComponent({
		name: "Button",
		props: ["variant", "theme", "loading", "disabled"],
		setup(props, { slots }) {
			return () =>
				h(
					"button",
					{ type: "button", disabled: !!props.disabled },
					slots.default?.(),
				)
		},
	})
	const Dialog = defineComponent({
		name: "Dialog",
		props: { modelValue: Boolean, options: Object },
		emits: ["update:modelValue", "close"],
		setup(props, { slots }) {
			return () =>
				props.modelValue !== false
					? h("div", { "data-test": "dialog" }, [
							slots["body-content"]?.(),
							slots.actions?.(),
						])
					: null
		},
	})
	const FeatherIcon = defineComponent({
		name: "FeatherIcon",
		props: ["name"],
		setup(props) {
			return () => h("span", { "data-icon": props.name })
		},
	})
	return { Button, Dialog, FeatherIcon }
})

import ConfirmDialog from "@/components/common/ConfirmDialog.vue"
import PurchaseDocView from "@/components/purchase/PurchaseDocView.vue"
import PurchaseReceiptDialog from "./PurchaseReceiptDialog.vue"

const PR_LIST_ROW = {
	name: "MAT-PRE-2026-00001",
	supplier: "SUP-1",
	supplier_name: "Internal Factory",
	posting_date: "2026-10-18",
	status: "Draft",
	docstatus: 0,
	workflow_state: null,
	grand_total: 250000,
	currency: "IDR",
}

const PR_DOC_DRAFT = {
	name: "MAT-PRE-2026-00001",
	supplier: "SUP-1",
	supplier_name: "Internal Factory",
	posting_date: "2026-10-18",
	set_warehouse: "Stores - TC",
	status: "Draft",
	docstatus: 0,
	workflow_state: null,
	editable: true,
	submit_actions: [],
	purchase_orders: ["PO-2026-00001"],
	inter_company_reference: "MAT-DN-2026-00001",
	grand_total: 250000,
	net_total: 250000,
	currency: "IDR",
	items: [
		{
			item_code: "ROTI-001",
			item_name: "Roti Manis",
			qty: 25,
			uom: "Nos",
			rate: 10000,
			amount: 250000,
			purchase_order: "PO-2026-00001",
		},
	],
	attachments: [],
}

const PR_DOC_SUBMITTED = {
	...PR_DOC_DRAFT,
	name: "MAT-PRE-2026-00002",
	status: "To Bill",
	docstatus: 1,
	editable: false,
	submit_actions: [],
}

function mountOpen() {
	return mount(PurchaseReceiptDialog, {
		props: {
			modelValue: true,
			posProfile: "POS-1",
		},
		global: { config: { globalProperties: { __: globalThis.__ } } },
	})
}

const confirmDialog = (wrapper) => wrapper.findComponent(ConfirmDialog)
async function clickConfirm(wrapper) {
	const buttons = confirmDialog(wrapper).findAll("button")
	await buttons[1].trigger("click")
	await flushPromises()
}

describe("PurchaseReceiptDialog", () => {
	beforeEach(() => {
		mocks.call.mockReset()
		mocks.perms.submit = true
		mocks.toast.success.mockReset()
		mocks.toast.error.mockReset()

		mocks.call.mockImplementation(async (method, params) => {
			if (method.includes("get_purchase_receipts")) {
				return { receipts: [PR_LIST_ROW] }
			}
			if (method.includes("get_purchase_receipt")) {
				return PR_DOC_DRAFT
			}
			if (method.includes("submit_purchase_receipt")) {
				return { name: params?.name || PR_DOC_DRAFT.name }
			}
			return null
		})
	})

	afterEach(() => {
		vi.restoreAllMocks()
	})

	it("row click opens doc view with items", async () => {
		const wrapper = mountOpen()
		await flushPromises()

		const row = wrapper.find(`[data-test="pr-${PR_LIST_ROW.name}"]`)
		expect(row.exists()).toBe(true)

		await row.trigger("click")
		await flushPromises()

		expect(mocks.call).toHaveBeenCalledWith(
			"pos_next.api.purchase_receipts.get_purchase_receipt",
			{ name: PR_LIST_ROW.name },
		)

		// doc view header and items table are rendered
		expect(wrapper.find('[data-testid="doc-header"]').exists()).toBe(true)
		expect(wrapper.find('[data-testid="doc-items"]').exists()).toBe(true)
		expect(wrapper.text()).toContain("Roti Manis")
		expect(wrapper.text()).toContain("MAT-PRE-2026-00001")
	})

	it("Back returns to list", async () => {
		const wrapper = mountOpen()
		await flushPromises()

		await wrapper.find(`[data-test="pr-${PR_LIST_ROW.name}"]`).trigger("click")
		await flushPromises()

		expect(wrapper.find('[data-testid="doc-header"]').exists()).toBe(true)

		await wrapper.find('[data-testid="doc-back"]').trigger("click")
		await flushPromises()

		// Returned to list view
		expect(wrapper.find('[data-testid="doc-header"]').exists()).toBe(false)
		expect(wrapper.find(`[data-test="pr-${PR_LIST_ROW.name}"]`).exists()).toBe(
			true,
		)
	})

	it("Draft shows Submit in doc-header and calls submit_purchase_receipt", async () => {
		const wrapper = mountOpen()
		await flushPromises()

		await wrapper.find(`[data-test="pr-${PR_LIST_ROW.name}"]`).trigger("click")
		await flushPromises()

		const primaryAction = wrapper.find('[data-testid="primary-action"]')
		expect(primaryAction.exists()).toBe(true)
		expect(primaryAction.text()).toBe("Submit")

		await primaryAction.trigger("click")
		await flushPromises()

		// Confirmation dialog opened
		expect(confirmDialog(wrapper).props("modelValue")).toBe(true)

		await clickConfirm(wrapper)

		expect(mocks.call).toHaveBeenCalledWith(
			"pos_next.api.purchase_receipts.submit_purchase_receipt",
			{ name: PR_DOC_DRAFT.name, action: undefined },
		)
		expect(mocks.toast.success).toHaveBeenCalled()
	})

	it("workflow action label replaces Submit", async () => {
		mocks.call.mockImplementation(async (method) => {
			if (method.includes("get_purchase_receipts")) {
				return { receipts: [PR_LIST_ROW] }
			}
			if (method.includes("get_purchase_receipt")) {
				return {
					...PR_DOC_DRAFT,
					workflow_state: "Draft",
					submit_actions: ["_PNXT Send"],
				}
			}
			if (method.includes("submit_purchase_receipt")) {
				return { name: PR_DOC_DRAFT.name }
			}
			return null
		})

		const wrapper = mountOpen()
		await flushPromises()

		await wrapper.find(`[data-test="pr-${PR_LIST_ROW.name}"]`).trigger("click")
		await flushPromises()

		const primaryAction = wrapper.find('[data-testid="primary-action"]')
		expect(primaryAction.exists()).toBe(true)
		expect(primaryAction.text()).toBe("_PNXT Send")

		await primaryAction.trigger("click")
		await flushPromises()

		await clickConfirm(wrapper)

		expect(mocks.call).toHaveBeenCalledWith(
			"pos_next.api.purchase_receipts.submit_purchase_receipt",
			{ name: PR_DOC_DRAFT.name, action: "_PNXT Send" },
		)
	})

	it("submitted receipt shows no primary action", async () => {
		mocks.call.mockImplementation(async (method) => {
			if (method.includes("get_purchase_receipts")) {
				return {
					receipts: [{ ...PR_LIST_ROW, status: "To Bill", docstatus: 1 }],
				}
			}
			if (method.includes("get_purchase_receipt")) {
				return PR_DOC_SUBMITTED
			}
			return null
		})

		const wrapper = mountOpen()
		await flushPromises()

		await wrapper.find(`[data-test="pr-${PR_LIST_ROW.name}"]`).trigger("click")
		await flushPromises()

		expect(wrapper.find('[data-testid="doc-header"]').exists()).toBe(true)
		expect(wrapper.find('[data-testid="primary-action"]').exists()).toBe(false)
	})

	it("no Save Draft text rendered", async () => {
		const wrapper = mountOpen()
		await flushPromises()

		expect(wrapper.text()).not.toContain("Save Draft")

		await wrapper.find(`[data-test="pr-${PR_LIST_ROW.name}"]`).trigger("click")
		await flushPromises()

		expect(wrapper.text()).not.toContain("Save Draft")
	})

	it("Enter key on row opens doc view", async () => {
		const wrapper = mountOpen()
		await flushPromises()

		await wrapper
			.find(`[data-test="pr-${PR_LIST_ROW.name}"]`)
			.trigger("keydown.enter")
		await flushPromises()

		expect(wrapper.find('[data-testid="doc-header"]').exists()).toBe(true)
	})

	it("multiple submit actions render Actions dropdown", async () => {
		mocks.call.mockImplementation(async (method) => {
			if (method.includes("get_purchase_receipts")) {
				return { receipts: [PR_LIST_ROW] }
			}
			if (method.includes("get_purchase_receipt")) {
				return {
					...PR_DOC_DRAFT,
					submit_actions: ["Approve", "Reject"],
				}
			}
			if (method.includes("submit_purchase_receipt")) {
				return { name: PR_DOC_DRAFT.name }
			}
			return null
		})

		const wrapper = mountOpen()
		await flushPromises()

		await wrapper.find(`[data-test="pr-${PR_LIST_ROW.name}"]`).trigger("click")
		await flushPromises()

		const primaryAction = wrapper.find('[data-testid="primary-action"]')
		expect(primaryAction.text()).toBe("Actions")

		await primaryAction.trigger("click")
		await flushPromises()

		const menu = wrapper.find('[data-testid="actions-menu"]')
		expect(menu.exists()).toBe(true)
		const actionButtons = menu.findAll("button")
		expect(actionButtons.map((b) => b.text())).toEqual(["Approve", "Reject"])

		await actionButtons[0].trigger("click")
		await flushPromises()

		await clickConfirm(wrapper)

		expect(mocks.call).toHaveBeenCalledWith(
			"pos_next.api.purchase_receipts.submit_purchase_receipt",
			{ name: PR_DOC_DRAFT.name, action: "Approve" },
		)
	})

	it("list row shows workflow_state over status", async () => {
		mocks.call.mockImplementation(async (method) => {
			if (method.includes("get_purchase_receipts")) {
				return {
					receipts: [
						{
							...PR_LIST_ROW,
							status: "Draft",
							workflow_state: "Pending Manager",
						},
					],
				}
			}
			return null
		})

		const wrapper = mountOpen()
		await flushPromises()

		expect(wrapper.text()).toContain("Pending Manager")
	})

	it("attachments can be added on draft receipt", async () => {
		const attachMock = vi.fn().mockResolvedValue([])
		mocks.call.mockImplementation(async (method, params) => {
			if (method.includes("get_purchase_receipts")) {
				return { receipts: [PR_LIST_ROW] }
			}
			if (method.includes("get_purchase_receipt")) {
				return PR_DOC_DRAFT
			}
			if (method.includes("attach_purchase_files")) {
				return attachMock(params)
			}
			return null
		})

		const wrapper = mountOpen()
		await flushPromises()

		await wrapper.find(`[data-test="pr-${PR_LIST_ROW.name}"]`).trigger("click")
		await flushPromises()

		const docView = wrapper.findComponent(PurchaseDocView)
		expect(docView.exists()).toBe(true)
		expect(docView.props("canAttach")).toBe(true)

		const file = new File(["test-content"], "invoice.pdf", {
			type: "application/pdf",
		})
		docView.vm.$emit("attach-add", [file])
		await flushPromises()

		expect(attachMock).toHaveBeenCalled()
		expect(mocks.toast.success).toHaveBeenCalled()
	})

	it("Open in ERPNext in menu opens ERPNext URL", async () => {
		const openSpy = vi.spyOn(window, "open").mockImplementation(() => null)
		const wrapper = mountOpen()
		await flushPromises()

		await wrapper.find(`[data-test="pr-${PR_LIST_ROW.name}"]`).trigger("click")
		await flushPromises()

		await wrapper.find('[data-testid="doc-menu-button"]').trigger("click")
		await flushPromises()

		const menuOpen = wrapper.find('[data-testid="doc-menu-open-erpnext"]')
		expect(menuOpen.exists()).toBe(true)
		await menuOpen.trigger("click")

		expect(openSpy).toHaveBeenCalledWith(
			`/app/purchase-receipt/${PR_DOC_DRAFT.name}`,
			"_blank",
		)
	})

	it("hides Grand Total header and column when cashier has no price permission", async () => {
		const unpricedReceipt = {
			name: "MAT-PRE-2026-00001",
			supplier: "SUP-1",
			supplier_name: "Internal Factory",
			posting_date: "2026-10-18",
			status: "Draft",
			docstatus: 0,
			workflow_state: null,
		}
		mocks.call.mockImplementation(async (method) => {
			if (method.includes("get_purchase_receipts")) {
				return { receipts: [unpricedReceipt] }
			}
			return null
		})

		const wrapper = mountOpen()
		await flushPromises()

		expect(wrapper.text()).not.toContain("Grand Total")
	})
})
