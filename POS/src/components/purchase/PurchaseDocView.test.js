/**
 * @vitest-environment jsdom
 */
import { describe, expect, it, vi } from "vitest"
import { mount } from "@vue/test-utils"
import PurchaseDocView from "./PurchaseDocView.vue"
import { ATTACHMENT_ACCEPT } from "@/utils/attachments"

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
	const FeatherIcon = defineComponent({
		name: "FeatherIcon",
		props: ["name"],
		setup(props) {
			return () => h("span", { class: `feather feather-${props.name}` })
		},
	})
	return { Button, FeatherIcon }
})

describe("PurchaseDocView.vue", () => {
	function mountView(options = {}) {
		return mount(PurchaseDocView, {
			...options,
			global: {
				...options.global,
				config: {
					globalProperties: { __: globalThis.__ },
				},
			},
		})
	}

	const baseDoc = {
		name: "PO-2026-00001",
		supplier: "SUPP-001",
		supplier_name: "Internal Bakery Supply",
		status: "Draft",
		workflow_state: null,
		transaction_date: "2026-10-18",
		schedule_date: "2026-10-20",
		set_warehouse: "Stores - WP",
		currency: "Rp",
		items: [
			{
				item_code: "FLOUR-01",
				item_name: "Wheat Flour",
				qty: 10,
				uom: "Kg",
			},
		],
		attachments: [],
	}

	it("renders header with name and workflow_state over status", () => {
		const docWithWf = {
			...baseDoc,
			status: "Draft",
			workflow_state: "Pending Approval",
		}
		const wrapper = mountView({
			props: {
				title: "Purchase Order",
				doc: docWithWf,
				kind: "po",
			},
		})

		const header = wrapper.find('[data-testid="doc-header"]')
		expect(header.exists()).toBe(true)
		expect(header.text()).toContain("Purchase Order")
		expect(header.text()).toContain("PO-2026-00001")
		expect(header.text()).toContain("Pending Approval")
		// workflow_state takes precedence over status
		expect(header.find('[data-testid="status-dot"]').exists()).toBe(true)
	})

	it("hides rate column and totals when price keys are absent", () => {
		const wrapper = mountView({
			props: {
				title: "Purchase Order",
				doc: baseDoc,
				kind: "po",
			},
		})

		const itemsTable = wrapper.find('[data-testid="doc-items"]')
		expect(itemsTable.exists()).toBe(true)
		expect(itemsTable.text()).not.toContain("Rate")
		expect(itemsTable.text()).not.toContain("Amount")
		expect(wrapper.find('[data-testid="doc-totals"]').exists()).toBe(false)
	})

	it("shows rate column and grand total when present", () => {
		const pricedDoc = {
			...baseDoc,
			net_total: 50000,
			grand_total: 55000,
			items: [
				{
					item_code: "FLOUR-01",
					item_name: "Wheat Flour",
					qty: 10,
					uom: "Kg",
					rate: 5000,
					amount: 50000,
				},
			],
		}
		const wrapper = mountView({
			props: {
				title: "Purchase Order",
				doc: pricedDoc,
				kind: "po",
			},
		})

		const itemsTable = wrapper.find('[data-testid="doc-items"]')
		expect(itemsTable.text()).toContain("Rate")
		expect(itemsTable.text()).toContain("Amount")
		const totals = wrapper.find('[data-testid="doc-totals"]')
		expect(totals.exists()).toBe(true)
		expect(totals.text()).toContain("Grand Total")
		expect(totals.text()).toContain("55.000")
	})

	it("emits back on arrow click", async () => {
		const wrapper = mountView({
			props: {
				title: "Purchase Order",
				doc: baseDoc,
				kind: "po",
			},
		})

		const backBtn = wrapper.find('[data-testid="doc-back"]')
		expect(backBtn.exists()).toBe(true)
		await backBtn.trigger("click")
		expect(wrapper.emitted("back")).toBeTruthy()
	})

	it("renders actions slot inside doc-header", () => {
		const wrapper = mountView({
			props: {
				title: "Purchase Order",
				doc: baseDoc,
				kind: "po",
			},
			slots: {
				actions: '<button data-testid="custom-action">Submit</button>',
			},
		})

		const header = wrapper.find('[data-testid="doc-header"]')
		const customAction = header.find('[data-testid="custom-action"]')
		expect(customAction.exists()).toBe(true)
		expect(customAction.text()).toBe("Submit")
	})

	it("renders after-items slot", () => {
		const wrapper = mountView({
			props: {
				title: "Purchase Order",
				doc: baseDoc,
				kind: "po",
			},
			slots: {
				"after-items": '<div data-testid="linked-receipts">Receipts List</div>',
			},
		})

		expect(wrapper.find('[data-testid="linked-receipts"]').exists()).toBe(true)
	})

	it("renders Factory SO badge when inter_company_order_reference is present", () => {
		const docWithFactorySO = {
			...baseDoc,
			inter_company_order_reference: "SO-2026-00042",
		}
		const wrapper = mountView({
			props: {
				title: "Purchase Order",
				doc: docWithFactorySO,
				kind: "po",
			},
		})

		const badge = wrapper.find('[data-testid="factory-so-badge"]')
		expect(badge.exists()).toBe(true)
		expect(badge.text()).toContain("Factory SO")
	})

	it("emits open-erpnext on click", async () => {
		const wrapper = mountView({
			props: {
				title: "Purchase Order",
				doc: baseDoc,
				kind: "po",
			},
		})

		const erpBtn = wrapper.find('[data-testid="open-erpnext"]')
		expect(erpBtn.exists()).toBe(true)
		await erpBtn.trigger("click")
		expect(wrapper.emitted("open-erpnext")).toBeTruthy()
	})

	it("renders PR details including purchase_orders and delivery note", () => {
		const prDoc = {
			name: "PR-2026-00001",
			supplier: "SUPP-001",
			supplier_name: "Internal Bakery Supply",
			status: "To Bill",
			posting_date: "2026-10-18",
			set_warehouse: "Stores - WP",
			purchase_orders: ["PO-2026-00001", "PO-2026-00002"],
			inter_company_reference: "DN-2026-00015",
			remarks: "Internal transfer batch 1",
			items: [
				{
					item_code: "FLOUR-01",
					item_name: "Wheat Flour",
					qty: 10,
					uom: "Kg",
					purchase_order: "PO-2026-00001",
				},
			],
		}
		const wrapper = mountView({
			props: {
				title: "Purchase Receipt",
				doc: prDoc,
				kind: "pr",
			},
		})

		const details = wrapper.find('[data-testid="doc-details"]')
		expect(details.text()).toContain("PO-2026-00001, PO-2026-00002")
		expect(details.text()).toContain("DN-2026-00015")
		expect(details.text()).toContain("Internal transfer batch 1")

		const items = wrapper.find('[data-testid="doc-items"]')
		expect(items.text()).toContain("PO: PO-2026-00001")
	})

	it("shows received_qty column for PO when present", () => {
		const poWithReceived = {
			...baseDoc,
			items: [
				{
					item_code: "FLOUR-01",
					item_name: "Wheat Flour",
					qty: 10,
					received_qty: 6,
					uom: "Kg",
				},
			],
		}
		const wrapper = mountView({
			props: {
				title: "Purchase Order",
				doc: poWithReceived,
				kind: "po",
			},
		})

		const items = wrapper.find('[data-testid="doc-items"]')
		expect(items.text()).toContain("Ordered")
		expect(items.text()).toContain("Received")
		expect(items.text()).toContain("6")
	})

	it("renders loading state when loading prop is true", () => {
		const wrapper = mountView({
			props: {
				title: "Purchase Order",
				doc: baseDoc,
				kind: "po",
				loading: true,
			},
		})

		expect(wrapper.find('[data-testid="doc-loading"]').exists()).toBe(true)
		expect(wrapper.find('[data-testid="doc-details"]').exists()).toBe(false)
	})

	it("handles attachments and emits attach-add and attach-remove", async () => {
		const file = new File(["test content"], "note.xlsx", {
			type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
		})
		const wrapper = mountView({
			props: {
				title: "Purchase Order",
				doc: {
					...baseDoc,
					attachments: [
						{
							file_name: "invoice.pdf",
							file_size: 1024,
							file_url: "/files/invoice.pdf",
						},
					],
				},
				kind: "po",
				canAttach: true,
				pendingFiles: [file],
			},
		})

		const attachmentsSection = wrapper.find('[data-testid="doc-attachments"]')
		expect(attachmentsSection.exists()).toBe(true)
		expect(attachmentsSection.text()).toContain("invoice.pdf")
		expect(attachmentsSection.text()).toContain("note.xlsx")

		// Trigger remove
		const removeBtn = attachmentsSection.find('button[aria-label="Remove"]')
		expect(removeBtn.exists()).toBe(true)
		await removeBtn.trigger("click")
		expect(wrapper.emitted("attach-remove")).toBeTruthy()
		expect(wrapper.emitted("attach-remove")[0][0]).toBe(0) // index 0 in pendingFiles
	})

	it("ATTACHMENT_ACCEPT includes .csv and .xlsx", () => {
		expect(ATTACHMENT_ACCEPT).toContain(".csv")
		expect(ATTACHMENT_ACCEPT).toContain(".xlsx")
	})
})
