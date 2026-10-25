/**
 * @vitest-environment jsdom
 *
 * Covers the Purchase Order dialog: list render, New -> form, supplier/item
 * search wiring, item pricing, save payload, error surfacing, and the
 * permission gating of the submit actions.
 */
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"

// The app installs __() as a global property; templates need it, and it must
// exist before the component module is evaluated.
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
	perms: { submit: true, cancel: true, create: true },
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

// Minimal stand-in: input emits @search (server search), $emit selects.
vi.mock("@/components/common/AutocompleteSelect.vue", async () => {
	const { defineComponent } = await import("vue")
	return {
		default: defineComponent({
			name: "AutocompleteSelect",
			props: {
				modelValue: { type: [String, Number], default: "" },
				options: { type: Array, default: () => [] },
				placeholder: { type: String, default: "" },
				loading: { type: Boolean, default: false },
			},
			emits: ["update:modelValue", "search"],
			template: `<div><input :placeholder="placeholder" :value="modelValue" @input="$emit('search', $event.target.value)" /></div>`,
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
import PurchaseOrderDialog from "./PurchaseOrderDialog.vue"

const DRAFT_ORDER = {
	name: "PO-2026-00001",
	supplier: "SUP-1",
	supplier_name: "Roti Ltd",
	transaction_date: "2026-09-19",
	grand_total: 150000,
	currency: "IDR",
	status: "Draft",
	docstatus: 0,
}

const SUBMITTED_ORDER = {
	...DRAFT_ORDER,
	name: "PO-2026-00002",
	status: "To Receive and Bill",
	docstatus: 1,
	per_received: 0,
}

const FULLY_RECEIVED_ORDER = {
	...SUBMITTED_ORDER,
	name: "PO-2026-00003",
	status: "To Bill",
	per_received: 100,
}

const INTERNAL_ORDER = {
	...SUBMITTED_ORDER,
	name: "PO-2026-00004",
	is_internal_supplier: 1,
	inter_company_order_reference: "SO-2026-00001",
}

const PR_DRAFT = {
	supplier: "SUP-1",
	supplier_name: "Roti Ltd",
	posting_date: "2026-09-19",
	company: "Test Co",
	currency: "IDR",
	set_warehouse: "WH-1",
	items: [
		{
			item_code: "ITEM-1",
			item_name: "Roti Tawar",
			purchase_order: "PO-2026-00002",
			purchase_order_item: "POI-1",
			ordered_qty: 10,
			received_qty: 4,
			pending_qty: 6,
			uom: "Nos",
			rate: 100,
			warehouse: "WH-1",
		},
		{
			item_code: "ITEM-2",
			item_name: "Brownis",
			purchase_order: "PO-2026-00002",
			purchase_order_item: "POI-2",
			ordered_qty: 5,
			received_qty: 0,
			pending_qty: 5,
			uom: "Nos",
			rate: 50,
			warehouse: "WH-1",
		},
	],
}

function mountOpen() {
	return mount(PurchaseOrderDialog, {
		props: {
			modelValue: true,
			posProfile: "POS-1",
			company: "Test Co",
			warehouse: "WH-1",
			currency: "IDR",
		},
		global: { config: { globalProperties: { __: globalThis.__ } } },
	})
}

function autocomplete(wrapper, placeholder) {
	return wrapper
		.findAllComponents({ name: "AutocompleteSelect" })
		.find((c) => c.props("placeholder") === placeholder)
}

function button(wrapper, text) {
	return wrapper.findAll("button").find((b) => b.text() === text)
}

function confirmDialog(wrapper) {
	return wrapper.findComponent(ConfirmDialog)
}

async function clickConfirm(wrapper) {
	confirmDialog(wrapper).vm.$emit("confirm")
	await flushPromises()
}

// Fill the form until it is saveable: supplier selected + one item row at qty 3.
async function fillForm(wrapper) {
	const prevImpl = mocks.call.getMockImplementation()
	mocks.call.mockImplementation(async (method, params) => {
		if (method.includes("get_supplier_details"))
			return {
				supplier_name: "Roti Ltd",
				currency: "IDR",
				buying_price_list: null,
				taxes_and_charges: null,
			}
		if (method.includes("get_purchase_item_details"))
			return {
				item_code: "ITEM-1",
				item_name: "Roti Tawar",
				uom: "Nos",
				stock_uom: "Nos",
				default_uom: "Nos",
				uoms: [
					{ uom: "Nos", conversion_factor: 1 },
					{ uom: "Box", conversion_factor: 10 },
				],
				conversion_factor: 1,
				price_list_rate: 100,
				rate: 90,
				warehouse: "WH-1",
			}
		if (prevImpl) return prevImpl(method, params)
		return {}
	})
	await autocomplete(wrapper, "Search supplier...").vm.$emit(
		"update:modelValue",
		"SUP-1",
	)
	await flushPromises()
	await autocomplete(wrapper, "Search item...").vm.$emit(
		"update:modelValue",
		"ITEM-1",
	)
	await flushPromises()
	await wrapper.find('[data-test="item-qty"]').setValue(3)
}

describe("PurchaseOrderDialog", () => {
	beforeEach(() => {
		mocks.call.mockReset()
		mocks.toast.success.mockClear()
		mocks.toast.error.mockClear()
		mocks.perms.submit = true
		mocks.perms.cancel = true
		mocks.perms.create = true
		localStorage.clear()
	})

	afterEach(() => {
		vi.useRealTimers()
	})

	it("mounts, loads the list and shows the empty state", async () => {
		mocks.call.mockResolvedValue({ orders: [] })
		const wrapper = mountOpen()
		await flushPromises()

		expect(mocks.call).toHaveBeenCalledWith(
			"pos_next.api.purchase_orders.get_purchase_orders",
			expect.objectContaining({ pos_profile: "POS-1", status: null }),
		)
		expect(wrapper.text()).toContain("No purchase orders")

		// status chip re-queries with the exact status
		mocks.call.mockClear()
		await button(wrapper, "Draft").trigger("click")
		await flushPromises()
		expect(mocks.call).toHaveBeenCalledWith(
			"pos_next.api.purchase_orders.get_purchase_orders",
			expect.objectContaining({ status: "Draft" }),
		)
	})

	it("row click opens PO doc with items and linked receipts", async () => {
		mocks.call.mockImplementation(async (method) => {
			if (method.includes("get_purchase_orders"))
				return { orders: [SUBMITTED_ORDER] }
			if (method.includes("get_purchase_order"))
				return {
					name: "PO-2026-00002",
					supplier: "SUP-1",
					supplier_name: "Roti Ltd",
					status: "To Receive and Bill",
					docstatus: 1,
					items: [
						{
							name: "row-1",
							item_code: "ITEM-1",
							item_name: "Roti Tawar",
							qty: 3,
							uom: "Pcs",
						},
						{
							name: "row-2",
							item_code: "ITEM-2",
							item_name: "Bolu Pandan",
							qty: 2,
							uom: "Box",
						},
					],
					receipts: [
						{
							name: "PR-2026-00001",
							posting_date: "2026-09-20",
							status: "Draft",
						},
					],
				}
			return {}
		})
		const wrapper = mountOpen()
		await flushPromises()

		expect(wrapper.find('[data-testid="doc-header"]').exists()).toBe(false)
		await wrapper.find('[data-test="po-PO-2026-00002"]').trigger("click")
		await flushPromises()

		expect(mocks.call).toHaveBeenCalledWith(
			"pos_next.api.purchase_orders.get_purchase_order",
			{ name: "PO-2026-00002" },
		)
		expect(wrapper.find('[data-testid="doc-header"]').exists()).toBe(true)
		expect(wrapper.text()).toContain("Roti Tawar")
		expect(wrapper.text()).toContain("Bolu Pandan")
		expect(wrapper.find('[data-testid="linked-receipts"]').exists()).toBe(true)
		expect(wrapper.text()).toContain("PR-2026-00001")
	})

	it("attaching on a submitted To Receive PO uploads and reloads", async () => {
		mocks.call.mockImplementation(async (method) => {
			if (method.includes("get_purchase_orders"))
				return { orders: [SUBMITTED_ORDER] }
			if (method.includes("get_purchase_order"))
				return {
					name: "PO-2026-00002",
					status: "To Receive and Bill",
					docstatus: 1,
					items: [],
					attachments: [],
				}
			if (method.includes("attach_purchase_files"))
				return [{ name: "f1", file_url: "/files/f1.pdf" }]
			return {}
		})
		const wrapper = mountOpen()
		await flushPromises()
		await wrapper.find('[data-test="po-PO-2026-00002"]').trigger("click")
		await flushPromises()

		const docView = wrapper.findComponent(PurchaseDocView)
		expect(docView.props("canAttach")).toBe(true)
		await docView.vm.$emit("attach-add", [
			new File(["dummy"], "note.pdf", { type: "application/pdf" }),
		])
		await flushPromises()

		expect(mocks.call).toHaveBeenCalledWith(
			"pos_next.api.purchase_orders.attach_purchase_files",
			expect.objectContaining({
				doctype: "Purchase Order",
				name: "PO-2026-00002",
			}),
		)
		expect(mocks.toast.success).toHaveBeenCalledWith("1 attachment(s) uploaded")
	})

	it("Draft doc shows Edit and Submit in doc-header", async () => {
		mocks.call.mockImplementation(async (method) => {
			if (method.includes("get_purchase_orders"))
				return { orders: [DRAFT_ORDER] }
			if (method.includes("get_purchase_order"))
				return {
					...DRAFT_ORDER,
					items: [
						{
							item_code: "ITEM-1",
							item_name: "Roti Tawar",
							qty: 2,
							uom: "Nos",
						},
					],
				}
			return {}
		})
		const wrapper = mountOpen()
		await flushPromises()
		await wrapper.find(`[data-test="po-${DRAFT_ORDER.name}"]`).trigger("click")
		await flushPromises()

		expect(wrapper.find('[data-testid="doc-edit"]').exists()).toBe(true)
		const primary = wrapper.find('[data-testid="primary-action"]')
		expect(primary.exists()).toBe(true)
		expect(primary.text()).toBe("Submit")

		await wrapper.find('[data-testid="doc-edit"]').trigger("click")
		await flushPromises()
		expect(
			wrapper.find('input[placeholder="Search supplier..."]').exists(),
		).toBe(true)
		expect(wrapper.text()).toContain(DRAFT_ORDER.name)
	})

	it("new form primary reads Save; after save view is doc and primary reads Submit", async () => {
		mocks.call.mockImplementation(async (method) => {
			if (method.includes("get_purchase_orders")) return { orders: [] }
			if (method.includes("save_purchase_order"))
				return { name: "PO-2026-00099" }
			if (method.includes("get_purchase_order"))
				return {
					name: "PO-2026-00099",
					status: "Draft",
					docstatus: 0,
					supplier: "SUP-1",
					supplier_name: "Roti Ltd",
					items: [
						{
							item_code: "ITEM-1",
							item_name: "Roti Tawar",
							qty: 3,
							uom: "Nos",
						},
					],
				}
			return {}
		})
		const wrapper = mountOpen()
		await flushPromises()
		await button(wrapper, "New Purchase Order").trigger("click")
		await flushPromises()

		const primary = wrapper.find('[data-testid="primary-action"]')
		expect(primary.text()).toBe("Save")

		await fillForm(wrapper)
		await primary.trigger("click")
		await flushPromises()

		expect(mocks.call).toHaveBeenCalledWith(
			"pos_next.api.purchase_orders.save_purchase_order",
			expect.objectContaining({ submit: 0 }),
		)
		expect(wrapper.find('[data-testid="doc-header"]').exists()).toBe(true)
		expect(wrapper.find('[data-testid="primary-action"]').text()).toBe("Submit")
	})

	it("editing a saved Draft flips primary back to Save", async () => {
		mocks.call.mockImplementation(async (method) => {
			if (method.includes("get_purchase_orders"))
				return { orders: [DRAFT_ORDER] }
			if (method.includes("get_purchase_order"))
				return {
					...DRAFT_ORDER,
					items: [
						{
							item_code: "ITEM-1",
							item_name: "Roti Tawar",
							qty: 2,
							uom: "Nos",
						},
					],
				}
			return {}
		})
		const wrapper = mountOpen()
		await flushPromises()
		await wrapper.find(`[data-test="po-${DRAFT_ORDER.name}"]`).trigger("click")
		await flushPromises()
		await wrapper.find('[data-testid="doc-edit"]').trigger("click")
		await flushPromises()

		// Clean form: primary action is Submit
		expect(wrapper.find('[data-testid="primary-action"]').text()).toBe("Submit")

		// Modify qty -> dirty -> primary flips to Save
		await wrapper.find('[data-test="item-qty"]').setValue(5)
		await flushPromises()
		expect(wrapper.find('[data-testid="primary-action"]').text()).toBe("Save")
	})

	it("removing tax template on clean Draft flips primary back to Save", async () => {
		mocks.call.mockImplementation(async (method) => {
			if (method.includes("get_purchase_orders"))
				return { orders: [DRAFT_ORDER] }
			if (method.includes("get_purchase_order"))
				return {
					...DRAFT_ORDER,
					taxes_and_charges: "Standard Tax - TC",
					items: [
						{
							item_code: "ITEM-1",
							item_name: "Roti Tawar",
							qty: 2,
							uom: "Nos",
						},
					],
				}
			return {}
		})
		const wrapper = mountOpen()
		await flushPromises()
		await wrapper.find(`[data-test="po-${DRAFT_ORDER.name}"]`).trigger("click")
		await flushPromises()
		await wrapper.find('[data-testid="doc-edit"]').trigger("click")
		await flushPromises()

		// Clean form with tax template: primary action is Submit
		expect(wrapper.find('[data-testid="primary-action"]').text()).toBe("Submit")
		expect(wrapper.text()).toContain("Standard Tax - TC")

		// Remove tax template -> dirty -> primary flips to Save
		const removeBtn = wrapper
			.findAll("button")
			.find((b) => b.text() === "Remove")
		expect(removeBtn).toBeDefined()
		await removeBtn.trigger("click")
		await flushPromises()
		expect(wrapper.find('[data-testid="primary-action"]').text()).toBe("Save")
	})

	it("Back from dirty form shows last saved doc without calling save", async () => {
		mocks.call.mockImplementation(async (method) => {
			if (method.includes("get_purchase_orders"))
				return { orders: [DRAFT_ORDER] }
			if (method.includes("get_purchase_order"))
				return {
					...DRAFT_ORDER,
					items: [
						{
							item_code: "ITEM-1",
							item_name: "Roti Tawar",
							qty: 2,
							uom: "Nos",
						},
					],
				}
			return {}
		})
		const wrapper = mountOpen()
		await flushPromises()
		await wrapper.find(`[data-test="po-${DRAFT_ORDER.name}"]`).trigger("click")
		await flushPromises()
		await wrapper.find('[data-testid="doc-edit"]').trigger("click")
		await flushPromises()

		await wrapper.find('[data-test="item-qty"]').setValue(9)
		await flushPromises()

		mocks.call.mockClear()
		await wrapper.find('[data-testid="doc-back"]').trigger("click")
		await flushPromises()

		expect(mocks.call).not.toHaveBeenCalledWith(
			"pos_next.api.purchase_orders.save_purchase_order",
			expect.anything(),
		)
		expect(wrapper.find('[data-testid="doc-header"]').exists()).toBe(true)
		expect(wrapper.text()).toContain(DRAFT_ORDER.name)
	})

	it("clicking a linked receipt opens the receipt view", async () => {
		mocks.call.mockImplementation(async (method) => {
			if (method.includes("get_purchase_orders"))
				return { orders: [SUBMITTED_ORDER] }
			if (method.includes("get_purchase_order"))
				return {
					name: "PO-2026-00002",
					status: "To Receive and Bill",
					docstatus: 1,
					items: [],
					receipts: [
						{
							name: "PR-2026-00001",
							posting_date: "2026-09-20",
							status: "Draft",
							docstatus: 0,
						},
					],
				}
			if (method.includes("get_purchase_receipt"))
				return {
					name: "PR-2026-00001",
					posting_date: "2026-09-20",
					status: "Draft",
					docstatus: 0,
					editable: true,
					items: [
						{
							item_code: "ITEM-1",
							item_name: "Roti Tawar",
							qty: 3,
							uom: "Nos",
						},
					],
				}
			return {}
		})
		const wrapper = mountOpen()
		await flushPromises()
		await wrapper.find('[data-test="po-PO-2026-00002"]').trigger("click")
		await flushPromises()

		await wrapper.find('[data-testid="linked-receipt-row"]').trigger("click")
		await flushPromises()

		expect(mocks.call).toHaveBeenCalledWith(
			"pos_next.api.purchase_receipts.get_purchase_receipt",
			{ name: "PR-2026-00001" },
		)
		expect(wrapper.text()).toContain("PR-2026-00001")
		// Back returns to PO doc view
		await wrapper.find('[data-testid="doc-back"]').trigger("click")
		await flushPromises()
		expect(wrapper.text()).toContain("PO-2026-00002")
	})

	it("PO with a Draft linked receipt still shows Receive and lists the draft with a Draft pill", async () => {
		mocks.call.mockImplementation(async (method) => {
			if (method.includes("get_purchase_orders"))
				return { orders: [SUBMITTED_ORDER] }
			if (method.includes("get_purchase_order"))
				return {
					name: "PO-2026-00002",
					status: "To Receive and Bill",
					docstatus: 1,
					per_received: 50,
					items: [],
					receipts: [
						{
							name: "PR-2026-00001",
							posting_date: "2026-09-20",
							status: "Draft",
							docstatus: 0,
						},
					],
				}
			return {}
		})
		const wrapper = mountOpen()
		await flushPromises()
		await wrapper.find('[data-test="po-PO-2026-00002"]').trigger("click")
		await flushPromises()

		// Primary Receive button is still visible
		const receiveBtn = wrapper.find('[data-testid="primary-action"]')
		expect(receiveBtn.exists()).toBe(true)
		expect(receiveBtn.text()).toBe("Receive")

		// Linked receipts shows the draft PR with Draft text
		const linked = wrapper.find('[data-testid="linked-receipts"]')
		expect(linked.exists()).toBe(true)
		expect(linked.text()).toContain("Draft")
	})

	it("no Save Draft text rendered", async () => {
		mocks.call.mockResolvedValue({ orders: [DRAFT_ORDER] })
		const wrapper = mountOpen()
		await flushPromises()
		expect(wrapper.text()).not.toContain("Save Draft")

		await button(wrapper, "New Purchase Order").trigger("click")
		await flushPromises()
		expect(wrapper.text()).not.toContain("Save Draft")
	})

	it("Cancel hidden for factory-linked PO", async () => {
		mocks.call.mockImplementation(async (method) => {
			if (method.includes("get_purchase_orders"))
				return { orders: [INTERNAL_ORDER, SUBMITTED_ORDER] }
			if (method.includes("get_purchase_order"))
				return {
					...INTERNAL_ORDER,
					items: [],
				}
			return {}
		})
		const wrapper = mountOpen()
		await flushPromises()
		await wrapper
			.find(`[data-test="po-${INTERNAL_ORDER.name}"]`)
			.trigger("click")
		await flushPromises()

		// no cancel and no ERPNext Desk link -> no more-actions menu at all
		expect(wrapper.find('[data-testid="doc-menu-button"]').exists()).toBe(false)
		expect(wrapper.text()).not.toContain("Open in ERPNext")
	})

	it("New button switches to the form view", async () => {
		mocks.call.mockResolvedValue({ orders: [DRAFT_ORDER] })
		const wrapper = mountOpen()
		await flushPromises()
		expect(wrapper.text()).toContain("PO-2026-00001")

		await button(wrapper, "New Purchase Order").trigger("click")
		expect(
			wrapper.find('input[placeholder="Search supplier..."]').exists(),
		).toBe(true)
		expect(wrapper.text()).toContain("Transaction Date")
	})

	it("entering the form preloads supplier and item options", async () => {
		vi.useFakeTimers()
		mocks.call.mockResolvedValue({
			suppliers: [],
			items: [],
		})
		const wrapper = mountOpen()
		await flushPromises()

		await button(wrapper, "New Purchase Order").trigger("click")
		await vi.advanceTimersByTimeAsync(400)

		expect(mocks.call).toHaveBeenCalledWith(
			"pos_next.api.purchase_orders.search_suppliers",
			expect.objectContaining({ search_term: null }),
		)
		expect(mocks.call).toHaveBeenCalledWith(
			"pos_next.api.purchase_orders.search_purchase_items",
			expect.objectContaining({ search_term: null }),
		)
		vi.useRealTimers()
	})

	it("New pre-fills the POS Settings default supplier and warehouse", async () => {
		mocks.call.mockImplementation((method) => {
			if (method.endsWith("get_po_defaults"))
				return Promise.resolve({
					supplier: "SUP-DEFAULT",
					supplier_name: "Default Supplier",
					warehouse: "WH-PURCH",
				})
			if (method.endsWith("get_supplier_details"))
				return Promise.resolve({
					supplier_name: "Default Supplier",
					currency: "IDR",
					buying_price_list: null,
					taxes_and_charges: null,
				})
			if (method.endsWith("get_purchase_orders"))
				return Promise.resolve({ orders: [DRAFT_ORDER] })
			return Promise.resolve({ suppliers: [], items: [] })
		})
		const wrapper = mountOpen()
		await flushPromises()

		await button(wrapper, "New Purchase Order").trigger("click")
		await flushPromises()

		const supplierInput = wrapper
			.findAll("input")
			.find((i) => i.attributes("placeholder") === "Search supplier...")
		expect(supplierInput.element.value).toBe("SUP-DEFAULT")

		// the caption is gone; the warehouse default rides the save payload instead
		await fillForm(wrapper)
		mocks.call.mockImplementation(async (method) =>
			method.includes("save_purchase_order") ? { name: "PO-2026-00003" } : {},
		)
		await button(wrapper, "Save").trigger("click")
		await flushPromises()

		const saveCall = mocks.call.mock.calls.find(([m]) =>
			m.includes("save_purchase_order"),
		)
		const data = JSON.parse(saveCall[1].data)
		expect(data.set_warehouse).toBe("WH-PURCH")
	})

	it("supplier search calls search_suppliers with the term after debounce", async () => {
		vi.useFakeTimers()
		mocks.call.mockResolvedValue({ suppliers: [] })
		const wrapper = mountOpen()
		await flushPromises()
		await button(wrapper, "New Purchase Order").trigger("click")

		const input = wrapper
			.findAll("input")
			.find((i) => i.attributes("placeholder") === "Search supplier...")
		await input.setValue("roti")
		expect(mocks.call).not.toHaveBeenCalledWith(
			"pos_next.api.purchase_orders.search_suppliers",
			expect.anything(),
		)
		await vi.advanceTimersByTimeAsync(300)
		expect(mocks.call).toHaveBeenCalledWith(
			"pos_next.api.purchase_orders.search_suppliers",
			expect.objectContaining({ search_term: "roti" }),
		)
	})

	it("picking an item prices it silently — qty and UOM only, no money shown", async () => {
		mocks.call.mockResolvedValue({ orders: [] })
		const wrapper = mountOpen()
		await flushPromises()
		await button(wrapper, "New Purchase Order").trigger("click")
		await fillForm(wrapper)

		const table = wrapper.find('[data-test="items-table"]')
		expect(table.text()).toContain("Roti Tawar")
		expect(mocks.call).toHaveBeenCalledWith(
			"pos_next.api.purchase_orders.get_purchase_item_details",
			expect.objectContaining({
				item_code: "ITEM-1",
				supplier: "SUP-1",
				warehouse: "WH-1",
			}),
		)
		// rate still rides the row state (sent on save) but nothing money-ish
		// is rendered — no rate input, no amount, no total
		expect(wrapper.find('[data-test="item-rate"]').exists()).toBe(false)
		expect(table.text()).not.toContain("300")
		expect(wrapper.text()).not.toContain("Grand Total")
		expect(wrapper.text()).not.toContain("Subtotal")
		// the summary counts quantities only
		const summary = wrapper.find('[data-test="order-summary"]').text()
		expect(summary).toContain("Total Items")
		expect(summary).toContain("3 Nos")

		// the row UOM is a selector over the item's UOMs, defaulting to the
		// backend's default UOM
		const uomSelect = wrapper.find('[data-test="item-uom"]')
		expect(uomSelect.exists()).toBe(true)
		expect(uomSelect.element.value).toBe("Nos")
		expect([...uomSelect.element.options].map((o) => o.value)).toEqual([
			"Nos",
			"Box",
		])
	})

	it("changing the UOM re-prices in it and remembers the pick per item", async () => {
		mocks.call.mockResolvedValue({ orders: [] })
		const wrapper = mountOpen()
		await flushPromises()
		await button(wrapper, "New Purchase Order").trigger("click")
		await fillForm(wrapper)

		const uomSelect = wrapper.find('[data-test="item-uom"]')
		await uomSelect.setValue("Box")
		await flushPromises()

		// re-fetch prices the row in the chosen UOM…
		expect(mocks.call).toHaveBeenCalledWith(
			"pos_next.api.purchase_orders.get_purchase_item_details",
			expect.objectContaining({ item_code: "ITEM-1", uom: "Box" }),
		)
		// …and the pick is remembered in this browser for the next PO
		expect(JSON.parse(localStorage.getItem("posNext.poUom"))).toEqual({
			"ITEM-1": "Box",
		})

		// a fresh pick for the same item starts from the remembered UOM
		await wrapper
			.find('[data-test="items-table"] button[aria-label="Remove"]')
			.trigger("click")
		await wrapper.find('[data-test="add-item"]').trigger("click")
		await autocomplete(wrapper, "Search item...").vm.$emit(
			"update:modelValue",
			"ITEM-1",
		)
		await flushPromises()
		const lastFetch = mocks.call.mock.calls
			.filter(([m]) => m.includes("get_purchase_item_details"))
			.at(-1)
		expect(lastFetch[1]).toEqual(expect.objectContaining({ uom: "Box" }))
	})

	it("Add Row appends a row; re-picking the same item bumps its qty instead", async () => {
		mocks.call.mockResolvedValue({ orders: [] })
		const wrapper = mountOpen()
		await flushPromises()
		await button(wrapper, "New Purchase Order").trigger("click")
		await fillForm(wrapper)

		await wrapper.find('[data-test="add-item"]').trigger("click")
		await autocomplete(wrapper, "Search item...").vm.$emit(
			"update:modelValue",
			"ITEM-1",
		)
		await flushPromises()

		const qty = wrapper.findAll('[data-test="item-qty"]')
		expect(qty).toHaveLength(1)
		expect(Number(qty[0].element.value)).toBe(4)
	})

	it("clicking a picked item reopens the search to change it in place", async () => {
		mocks.call.mockResolvedValue({ orders: [] })
		const wrapper = mountOpen()
		await flushPromises()
		await button(wrapper, "New Purchase Order").trigger("click")
		await fillForm(wrapper)

		// leaving the search without a pick keeps the original item
		await wrapper.find('[data-testid="row-item-edit"]').trigger("click")
		expect(autocomplete(wrapper, "Search item...")).toBeTruthy()
		await wrapper.find('[data-cell="0-item"]').trigger("keydown", { key: "Escape" })
		expect(wrapper.find('[data-testid="row-item-edit"]').text()).toContain("Roti Tawar")

		const prevImpl = mocks.call.getMockImplementation()
		mocks.call.mockImplementation(async (method, params) => {
			if (method.includes("get_purchase_item_details"))
				return { item_code: params.item_code, item_name: "Roti Manis", uom: "Nos", uoms: [{ uom: "Nos" }] }
			return prevImpl(method, params)
		})
		await wrapper.find('[data-testid="row-item-edit"]').trigger("click")
		await autocomplete(wrapper, "Search item...").vm.$emit("update:modelValue", "ITEM-2")
		await flushPromises()
		const rows = wrapper.findAll('[data-testid="row-item-edit"]')
		expect(rows).toHaveLength(1)
		expect(rows[0].text()).toContain("Roti Manis")
	})

	it("a row being changed keeps its item and is not reused by Add Row", async () => {
		mocks.call.mockResolvedValue({ orders: [] })
		const wrapper = mountOpen()
		await flushPromises()
		await button(wrapper, "New Purchase Order").trigger("click")
		await fillForm(wrapper)

		await wrapper.find('[data-testid="row-item-edit"]').trigger("click")
		// Save mid-edit must still count the original item
		expect(wrapper.find('[data-test="order-summary"]').text()).toContain("3 Nos")
		await wrapper.find('[data-test="add-item"]').trigger("click")
		expect(wrapper.findAll('[data-testid="row-checkbox"]')).toHaveLength(2)
	})

	it("select-all + Delete (n) removes the checked rows", async () => {
		mocks.call.mockResolvedValue({ orders: [] })
		const wrapper = mountOpen()
		await flushPromises()
		await button(wrapper, "New Purchase Order").trigger("click")
		await fillForm(wrapper)
		await wrapper.find('[data-test="add-item"]').trigger("click")
		expect(wrapper.findAll('[data-testid="row-checkbox"]')).toHaveLength(2)

		expect(wrapper.find('[data-testid="delete-selected-rows"]').exists()).toBe(false)
		await wrapper.find('[data-testid="select-all-rows"]').setValue(true)
		const del = wrapper.find('[data-testid="delete-selected-rows"]')
		expect(del.text()).toContain("(2)")
		await del.trigger("click")
		expect(wrapper.findAll('[data-testid="row-checkbox"]')).toHaveLength(0)
	})

	it("blocks save with inline errors for zero qty and Required By before the order date", async () => {
		mocks.call.mockResolvedValue({ orders: [] })
		const wrapper = mountOpen()
		await flushPromises()
		await button(wrapper, "New Purchase Order").trigger("click")
		await fillForm(wrapper)
		await wrapper.find('[data-test="item-qty"]').setValue(0)

		await button(wrapper, "Save").trigger("click")
		await flushPromises()
		expect(mocks.toast.error).toHaveBeenCalledWith(
			"Quantity must be greater than 0",
		)

		await wrapper.find('[data-test="item-qty"]').setValue(2)
		await wrapper.find("#po-transaction-date").setValue("2026-10-08")
		await wrapper.find("#po-schedule-date").setValue("2026-10-01")
		await button(wrapper, "Save").trigger("click")
		await flushPromises()
		expect(wrapper.text()).toContain(
			"Required By cannot be before Transaction Date",
		)
		expect(mocks.call).not.toHaveBeenCalledWith(
			"pos_next.api.purchase_orders.save_purchase_order",
			expect.anything(),
		)
	})

	it("the POS Settings price list rides the item fetch and the save payload", async () => {
		mocks.call.mockImplementation((method) => {
			if (method.includes("get_po_defaults"))
				return Promise.resolve({
					supplier: null,
					supplier_name: null,
					warehouse: "WH-1",
					price_list: "PL-IC",
				})
			return Promise.resolve({ orders: [] })
		})
		const wrapper = mountOpen()
		await flushPromises()
		await button(wrapper, "New Purchase Order").trigger("click")
		await fillForm(wrapper)

		expect(mocks.call).toHaveBeenCalledWith(
			"pos_next.api.purchase_orders.get_purchase_item_details",
			expect.objectContaining({ price_list: "PL-IC" }),
		)

		mocks.call.mockImplementation(async (method) =>
			method.includes("save_purchase_order") ? { name: "PO-2026-00004" } : {},
		)
		await button(wrapper, "Save").trigger("click")
		await flushPromises()

		const saveCall = mocks.call.mock.calls.find(([m]) =>
			m.includes("save_purchase_order"),
		)
		expect(JSON.parse(saveCall[1].data).buying_price_list).toBe("PL-IC")
	})

	it("Save posts the full payload with submit: 0", async () => {
		mocks.call.mockImplementation((method) => {
			if (method.includes("get_po_defaults"))
				return Promise.resolve({
					supplier: null,
					supplier_name: null,
					warehouse: "WH-1",
				})
			return Promise.resolve({ orders: [] })
		})
		const wrapper = mountOpen()
		await flushPromises()
		await button(wrapper, "New Purchase Order").trigger("click")
		await fillForm(wrapper)

		mocks.call.mockImplementation(async (method) =>
			method.includes("save_purchase_order") ? { name: "PO-2026-00002" } : {},
		)
		await button(wrapper, "Save").trigger("click")
		await flushPromises()

		const saveCall = mocks.call.mock.calls.find(([method]) =>
			method.includes("save_purchase_order"),
		)
		expect(saveCall[0]).toBe("pos_next.api.purchase_orders.save_purchase_order")
		expect(saveCall[1]).toEqual(
			expect.objectContaining({ pos_profile: "POS-1", submit: 0 }),
		)
		const data = JSON.parse(saveCall[1].data)
		expect(data.supplier).toBe("SUP-1")
		expect(data.company).toBe("Test Co")
		expect(data.set_warehouse).toBe("WH-1")
		// no POS Settings price list configured -> null (supplier default applies)
		expect(data.buying_price_list).toBeNull()
		expect(data.name).toBeUndefined()
		expect(data.items).toEqual([
			{ item_code: "ITEM-1", qty: 3, rate: 100, uom: "Nos" },
		])
		expect(mocks.toast.success).toHaveBeenCalledWith(
			"Purchase Order PO-2026-00002 saved",
		)
	})

	it("surfaces the original backend message on failure", async () => {
		mocks.call.mockResolvedValue({ orders: [] })
		const wrapper = mountOpen()
		await flushPromises()
		await button(wrapper, "New Purchase Order").trigger("click")
		await fillForm(wrapper)

		mocks.call.mockImplementation(async (method) => {
			if (method.includes("save_purchase_order"))
				throw Object.assign(new Error("Request failed"), {
					messages: ["Invalid warehouse WH-X in item row"],
					httpStatus: 417,
				})
			return {}
		})
		await button(wrapper, "Save").trigger("click")
		await flushPromises()

		expect(mocks.toast.error).toHaveBeenCalledWith(
			"Invalid warehouse WH-X in item row",
		)
		expect(mocks.toast.success).not.toHaveBeenCalled()
	})

	it("Edit prefills the tax template from the loaded order", async () => {
		mocks.call.mockImplementation(async (method) => {
			if (method.endsWith("get_purchase_order"))
				return {
					...DRAFT_ORDER,
					name: "PO-2026-00001",
					supplier: "SUP-1",
					transaction_date: "2026-09-19",
					schedule_date: "2026-09-20",
					currency: "IDR",
					taxes_and_charges: "T1",
					remarks: "",
					items: [],
				}
			return { orders: [DRAFT_ORDER] }
		})
		const wrapper = mountOpen()
		await flushPromises()

		await wrapper.find('[data-test="po-PO-2026-00001"]').trigger("click")
		await flushPromises()

		await button(wrapper, "Edit").trigger("click")
		await flushPromises()

		expect(mocks.call).toHaveBeenCalledWith(
			"pos_next.api.purchase_orders.get_purchase_order",
			{ name: "PO-2026-00001" },
		)
		// the loaded template must prefill the form, or the full-payload save
		// would clear it
		expect(wrapper.text()).toContain("Tax Template: T1")
	})

	it("gates the submit actions behind the submit permission", async () => {
		mocks.perms.submit = false
		mocks.call.mockImplementation(async (method, params) => {
			if (method.includes("get_purchase_orders"))
				return { orders: [DRAFT_ORDER] }
			if (method.includes("get_purchase_order"))
				return { ...DRAFT_ORDER, name: params?.name || DRAFT_ORDER.name }
			return {}
		})
		const wrapper = mountOpen()
		await flushPromises()

		// list: draft row has no Submit action
		expect(button(wrapper, "Submit")).toBeUndefined()

		// doc view: draft order has no Submit button when perms.submit = false
		await wrapper.find('[data-test="po-PO-2026-00001"]').trigger("click")
		await flushPromises()
		expect(button(wrapper, "Submit")).toBeUndefined()

		await wrapper.find('[data-testid="doc-back"]').trigger("click")
		await flushPromises()

		await button(wrapper, "New Purchase Order").trigger("click")
		// form: primary action is Save; no Submit
		expect(button(wrapper, "Submit")).toBeUndefined()
		expect(button(wrapper, "Save")).toBeDefined()

		// with the permission the buttons appear
		mocks.perms.submit = true
		await wrapper.setProps({ modelValue: false })
		await flushPromises()
		await wrapper.setProps({ modelValue: true })
		await flushPromises()
		await wrapper.find('[data-test="po-PO-2026-00001"]').trigger("click")
		await flushPromises()
		expect(button(wrapper, "Submit")).toBeDefined()
		await wrapper.find('[data-testid="doc-back"]').trigger("click")
		await flushPromises()
		await button(wrapper, "New Purchase Order").trigger("click")
		expect(button(wrapper, "Save")).toBeDefined()
	})

	// ---------- purchase receipts ----------

	function mockPrFlow() {
		mocks.call.mockImplementation(async (method, params) => {
			if (method.includes("get_purchase_orders"))
				return { orders: [SUBMITTED_ORDER] }
			if (method.includes("get_purchase_order"))
				return { ...SUBMITTED_ORDER, name: params?.name || SUBMITTED_ORDER.name }
			if (method.includes("get_intercompany_receipt_draft")) return PR_DRAFT
			if (method.includes("get_purchase_receipt_draft")) return PR_DRAFT
			return {}
		})
	}

	async function openReceiveView(wrapper, poName = "PO-2026-00002") {
		await wrapper.find(`[data-test="po-${poName}"]`).trigger("click")
		await flushPromises()
		await wrapper.find('[data-test="receive-button"]').trigger("click")
		await flushPromises()
	}

	it("shows Receive only on partially received submitted POs in doc view, gated by PR create permission", async () => {
		mocks.call.mockImplementation(async (method, params) => {
			if (method.includes("get_purchase_orders"))
				return {
					orders: [DRAFT_ORDER, SUBMITTED_ORDER, FULLY_RECEIVED_ORDER],
				}
			if (method.includes("get_purchase_order")) {
				if (params?.name === "PO-2026-00001") return DRAFT_ORDER
				if (params?.name === "PO-2026-00002") return SUBMITTED_ORDER
				if (params?.name === "PO-2026-00003") return FULLY_RECEIVED_ORDER
			}
			return {}
		})
		const wrapper = mountOpen()
		await flushPromises()

		// List view has no receive buttons (actions column removed)
		expect(wrapper.findAll('[data-test="receive-button"]')).toHaveLength(0)

		// Submitted PO with per_received < 100 shows Receive in doc view
		await wrapper.find('[data-test="po-PO-2026-00002"]').trigger("click")
		await flushPromises()
		expect(wrapper.find('[data-test="receive-button"]').exists()).toBe(true)

		// Draft PO doc view does not show Receive
		await wrapper.find('[data-testid="doc-back"]').trigger("click")
		await flushPromises()
		await wrapper.find('[data-test="po-PO-2026-00001"]').trigger("click")
		await flushPromises()
		expect(wrapper.find('[data-test="receive-button"]').exists()).toBe(false)

		// Fully received PO doc view does not show Receive
		await wrapper.find('[data-testid="doc-back"]').trigger("click")
		await flushPromises()
		await wrapper.find('[data-test="po-PO-2026-00003"]').trigger("click")
		await flushPromises()
		expect(wrapper.find('[data-test="receive-button"]').exists()).toBe(false)

		// When PR create permission is false: no Receive button in doc view
		mocks.perms.create = false
		await wrapper.find('[data-testid="doc-back"]').trigger("click")
		await flushPromises()
		await wrapper.find('[data-test="po-PO-2026-00002"]').trigger("click")
		await flushPromises()
		expect(wrapper.find('[data-test="receive-button"]').exists()).toBe(false)
	})

	it("removes Factory SO badge from list but keeps it in doc view", async () => {
		mocks.call.mockImplementation(async (method, params) => {
			if (method.includes("get_purchase_orders"))
				return { orders: [SUBMITTED_ORDER, INTERNAL_ORDER] }
			if (method.includes("get_purchase_order")) {
				if (params?.name === "PO-2026-00004") return INTERNAL_ORDER
				if (params?.name === "PO-2026-00005") {
					return {
						...INTERNAL_ORDER,
						name: "PO-2026-00005",
						inter_company_order_reference: null,
					}
				}
				return SUBMITTED_ORDER
			}
			return {}
		})
		const wrapper = mountOpen()
		await flushPromises()

		// List view does NOT show Factory SO badge
		const listRow = wrapper.find(`[data-test="po-${INTERNAL_ORDER.name}"]`)
		expect(listRow.text()).not.toContain("Factory SO")

		// Open doc view: doc view renders the Factory SO badge
		await listRow.trigger("click")
		await flushPromises()
		expect(wrapper.find('[data-testid="factory-so-badge"]').exists()).toBe(true)
		expect(wrapper.find('[data-testid="factory-so-badge"]').text()).toContain("Factory SO")

		// Return to list and test internal PO without factory SO
		await wrapper.find('[data-testid="doc-back"]').trigger("click")
		await flushPromises()

		const notYetLinked = {
			...INTERNAL_ORDER,
			name: "PO-2026-00005",
			inter_company_order_reference: null,
		}
		mocks.call.mockImplementation(async (method, params) => {
			if (method.includes("get_purchase_orders"))
				return { orders: [SUBMITTED_ORDER, notYetLinked] }
			if (method.includes("get_purchase_order")) {
				if (params?.name === "PO-2026-00005") return notYetLinked
				return SUBMITTED_ORDER
			}
			return {}
		})
		await wrapper.setProps({ modelValue: false })
		await flushPromises()
		await wrapper.setProps({ modelValue: true })
		await flushPromises()

		expect(wrapper.text()).not.toContain("Factory SO")
		await wrapper.find('[data-test="po-PO-2026-00005"]').trigger("click")
		await flushPromises()
		expect(wrapper.find('[data-testid="factory-so-badge"]').exists()).toBe(false)
	})

	it("hides Receive until a Delivery Note exists when the POS Settings gate is on", async () => {
		mocks.call.mockImplementation(async (method, params) => {
			if (method.includes("get_po_defaults"))
				return {
					supplier: null,
					supplier_name: null,
					warehouse: "WH-1",
					receive_requires_delivery_note: 1,
				}
			if (method.includes("get_purchase_orders"))
				return {
					orders: [
						{ ...SUBMITTED_ORDER, name: "PO-NO-DN" },
						{ ...SUBMITTED_ORDER, name: "PO-WITH-DN", delivery_ready: true },
					],
				}
			if (method.includes("get_purchase_order")) {
				if (params?.name === "PO-NO-DN")
					return { ...SUBMITTED_ORDER, name: "PO-NO-DN" }
				if (params?.name === "PO-WITH-DN")
					return { ...SUBMITTED_ORDER, name: "PO-WITH-DN", delivery_ready: true }
			}
			return {}
		})
		const wrapper = mountOpen()
		await flushPromises()

		// PO-NO-DN doc view hides Receive
		await wrapper.find('[data-test="po-PO-NO-DN"]').trigger("click")
		await flushPromises()
		expect(wrapper.find('[data-test="receive-button"]').exists()).toBe(false)

		// PO-WITH-DN doc view offers Receive
		await wrapper.find('[data-testid="doc-back"]').trigger("click")
		await flushPromises()
		await wrapper.find('[data-test="po-PO-WITH-DN"]').trigger("click")
		await flushPromises()
		expect(wrapper.find('[data-test="receive-button"]').exists()).toBe(true)
		expect(wrapper.find('[data-test="receive-button"]').text()).toBe("Receive")
	})

	it("Receive always calls get_intercompany_receipt_draft", async () => {
		mockPrFlow()
		const wrapper = mountOpen()
		await flushPromises()
		await openReceiveView(wrapper)

		expect(mocks.call).toHaveBeenCalledWith(
			"pos_next.api.purchase_receipts.get_intercompany_receipt_draft",
			{ po_name: "PO-2026-00002" },
		)
	})

	it("supplier name rendered once", async () => {
		mockPrFlow()
		const wrapper = mountOpen()
		await flushPromises()
		await openReceiveView(wrapper)

		const supplierEl = wrapper.find('[data-testid="receive-supplier"]')
		expect(supplierEl.exists()).toBe(true)
		expect(supplierEl.text()).toBe("Roti Ltd")
		expect(wrapper.find('[data-testid="receive-supplier-code"]').exists()).toBe(
			false,
		)
	})

	it("renders delivery note reference without double label in receive info", async () => {
		mocks.call.mockImplementation(async (method, params) => {
			if (method.includes("get_purchase_orders"))
				return { orders: [SUBMITTED_ORDER] }
			if (method.includes("get_purchase_order"))
				return { ...SUBMITTED_ORDER, name: params?.name || SUBMITTED_ORDER.name }
			if (method.includes("get_intercompany_receipt_draft"))
				return { ...PR_DRAFT, inter_company_reference: "MAT-DN-2026-00001" }
			return {}
		})
		const wrapper = mountOpen()
		await flushPromises()
		await openReceiveView(wrapper)

		expect(wrapper.text()).toContain("Delivery Note")
		expect(wrapper.text()).toContain("MAT-DN-2026-00001")
		expect(wrapper.text()).not.toContain("From Delivery Note:")
	})

	it("Fill remaining sets every qty to pending, Clear sets 0", async () => {
		mockPrFlow()
		const wrapper = mountOpen()
		await flushPromises()
		await openReceiveView(wrapper)

		const qtyInputs = wrapper.findAll('[data-test="receive-qty"]')
		expect(qtyInputs[0].element.value).toBe("6")
		expect(qtyInputs[1].element.value).toBe("5")

		await wrapper.find('[data-testid="clear-qtys"]').trigger("click")
		expect(qtyInputs[0].element.value).toBe("0")
		expect(qtyInputs[1].element.value).toBe("0")

		await wrapper.find('[data-testid="fill-remaining"]').trigger("click")
		expect(qtyInputs[0].element.value).toBe("6")
		expect(qtyInputs[1].element.value).toBe("5")
	})

	it("qty over remaining shows warning and blocks Save", async () => {
		mockPrFlow()
		const wrapper = mountOpen()
		await flushPromises()
		await openReceiveView(wrapper)

		const saveBtn = wrapper.find('[data-testid="receive-save"]')
		expect(saveBtn.attributes("disabled")).toBeUndefined()

		const qtyInputs = wrapper.findAll('[data-test="receive-qty"]')
		await qtyInputs[0].setValue(10)

		expect(wrapper.find('[data-testid="excess-warning"]').exists()).toBe(true)
		expect(wrapper.find('[data-testid="excess-warning"]').text()).toContain(
			"More than remaining (6)",
		)
		expect(saveBtn.attributes("disabled")).toBeDefined()

		await qtyInputs[0].setValue(6)
		expect(wrapper.find('[data-testid="excess-warning"]').exists()).toBe(false)
		expect(saveBtn.attributes("disabled")).toBeUndefined()
	})

	it("Save posts submit 0 without rate and opens the receipt view with Submit", async () => {
		mocks.call.mockImplementation(async (method, params) => {
			if (method.includes("get_purchase_orders"))
				return { orders: [SUBMITTED_ORDER] }
			if (method.includes("get_purchase_order"))
				return { ...SUBMITTED_ORDER, name: params?.name || SUBMITTED_ORDER.name }
			if (method.includes("get_intercompany_receipt_draft")) return PR_DRAFT
			if (method.includes("save_purchase_receipt"))
				return { name: "PR-2026-00009" }
			if (method.includes("get_purchase_receipt"))
				return {
					name: "PR-2026-00009",
					status: "Draft",
					docstatus: 0,
					supplier: "SUP-1",
					supplier_name: "Roti Ltd",
					editable: true,
					submit_actions: [],
					items: [
						{
							item_code: "ITEM-1",
							item_name: "Roti Tawar",
							qty: 3,
							uom: "Nos",
						},
					],
				}
			return {}
		})
		const wrapper = mountOpen()
		await flushPromises()
		await openReceiveView(wrapper)

		const qtyInputs = wrapper.findAll('[data-test="receive-qty"]')
		await qtyInputs[0].setValue(3)
		await qtyInputs[1].setValue(0)

		await wrapper.find('[data-testid="receive-save"]').trigger("click")
		await flushPromises()

		const saveCall = mocks.call.mock.calls.find(([m]) =>
			m.includes("save_purchase_receipt"),
		)
		expect(saveCall[0]).toBe(
			"pos_next.api.purchase_receipts.save_purchase_receipt",
		)
		expect(saveCall[1]).toEqual(expect.objectContaining({ submit: 0 }))
		const data = JSON.parse(saveCall[1].data)
		expect(data.supplier).toBe("SUP-1")
		expect(data.posting_date).toBe("2026-09-19")
		expect(data.items).toEqual([
			{
				item_code: "ITEM-1",
				qty: 3,
				uom: "Nos",
				warehouse: "WH-1",
				purchase_order: "PO-2026-00002",
				purchase_order_item: "POI-1",
				delivery_note_item: undefined,
			},
		])
		expect("rate" in data.items[0]).toBe(false)
		expect(mocks.toast.success).toHaveBeenCalledWith(
			"Purchase Receipt PR-2026-00009 saved",
		)
		expect(wrapper.find('[data-testid="doc-header"]').exists()).toBe(true)
		expect(wrapper.find('[data-testid="primary-action"]').text()).toBe("Submit")
	})

	it("routes internal POs to the Delivery Note draft and submits the DN links", async () => {
		mocks.call.mockImplementation(async (method, params) => {
			if (method.includes("get_purchase_orders"))
				return { orders: [INTERNAL_ORDER] }
			if (method.includes("get_purchase_order"))
				return { ...INTERNAL_ORDER, name: params?.name || INTERNAL_ORDER.name }
			if (method.includes("get_intercompany_receipt_draft"))
				return {
					...PR_DRAFT,
					inter_company_reference: "MAT-DN-2026-00001",
					items: [
						{
							...PR_DRAFT.items[0],
							purchase_order: "PO-2026-00004",
							delivery_note_item: "DNROW-1",
						},
					],
				}
			return {}
		})
		const wrapper = mountOpen()
		await flushPromises()

		await openReceiveView(wrapper, "PO-2026-00004")

		expect(mocks.call).toHaveBeenCalledWith(
			"pos_next.api.purchase_receipts.get_intercompany_receipt_draft",
			{ po_name: "PO-2026-00004" },
		)
		expect(wrapper.text()).toContain("MAT-DN-2026-00001")

		mocks.call.mockImplementation(async (method) =>
			method.includes("save_purchase_receipt") ? { name: "PR-2026-00009" } : {},
		)
		await wrapper.find('[data-testid="receive-save"]').trigger("click")
		await flushPromises()

		const saveCall = mocks.call.mock.calls.find(([method]) =>
			method.includes("save_purchase_receipt"),
		)
		const data = JSON.parse(saveCall[1].data)
		expect(data.inter_company_reference).toBe("MAT-DN-2026-00001")
		expect(data.items[0].delivery_note_item).toBe("DNROW-1")
	})

	it("surfaces the backend PR save error verbatim", async () => {
		mockPrFlow()
		const wrapper = mountOpen()
		await flushPromises()
		await openReceiveView(wrapper)

		mocks.call.mockImplementation(async (method) => {
			if (method.includes("save_purchase_receipt"))
				throw Object.assign(new Error("Request failed"), {
					messages: ["Cannot receive more than ordered quantity"],
					httpStatus: 417,
				})
			return {}
		})
		await wrapper.find('[data-testid="receive-save"]').trigger("click")
		await flushPromises()

		expect(mocks.toast.error).toHaveBeenCalledWith(
			"Cannot receive more than ordered quantity",
		)
		expect(mocks.toast.success).not.toHaveBeenCalled()
	})

	// ---------- purchase list refinement & shipping status ----------

	it("renders Shipping status column in header instead of Actions", async () => {
		mocks.call.mockResolvedValue({ orders: [SUBMITTED_ORDER] })
		const wrapper = mountOpen()
		await flushPromises()

		expect(wrapper.text()).toContain("Shipping status")
		expect(wrapper.text()).not.toContain("Actions")
	})

	it("renders no row chevron in PO list", async () => {
		mocks.call.mockResolvedValue({ orders: [SUBMITTED_ORDER] })
		const wrapper = mountOpen()
		await flushPromises()

		const row = wrapper.find(`[data-test="po-${SUBMITTED_ORDER.name}"]`)
		expect(row.exists()).toBe(true)
		expect(row.find('[data-icon="chevron-right"]').exists()).toBe(false)
		expect(row.text()).not.toContain(">")
	})

	it("renders shipping status pills for preparing, in_transit, and received", async () => {
		const poPreparing = {
			...SUBMITTED_ORDER,
			name: "PO-PREP",
			shipping_status: "preparing",
		}
		const poInTransit = {
			...SUBMITTED_ORDER,
			name: "PO-TRANSIT",
			shipping_status: "in_transit",
		}
		const poReceived = {
			...SUBMITTED_ORDER,
			name: "PO-RECV",
			shipping_status: "received",
		}

		mocks.call.mockResolvedValue({
			orders: [poPreparing, poInTransit, poReceived],
		})
		const wrapper = mountOpen()
		await flushPromises()

		const rowPrep = wrapper.find('[data-test="po-PO-PREP"]')
		expect(rowPrep.text()).toContain("Goods being prepared")

		const rowTransit = wrapper.find('[data-test="po-PO-TRANSIT"]')
		expect(rowTransit.text()).toContain("In transit")

		const rowRecv = wrapper.find('[data-test="po-PO-RECV"]')
		expect(rowRecv.text()).toContain("Received")
	})

	it("renders em dash fallback for missing/null/unknown shipping status without inferring from delivery_ready", async () => {
		const poNull = {
			...SUBMITTED_ORDER,
			name: "PO-NULL",
			shipping_status: null,
			delivery_ready: true,
		}
		const poUndefined = {
			...SUBMITTED_ORDER,
			name: "PO-UNDEF",
			shipping_status: undefined,
			delivery_ready: true,
		}
		const poUnknown = {
			...SUBMITTED_ORDER,
			name: "PO-UNKNOWN",
			shipping_status: "on_the_way",
			delivery_ready: true,
		}

		mocks.call.mockResolvedValue({
			orders: [poNull, poUndefined, poUnknown],
		})
		const wrapper = mountOpen()
		await flushPromises()

		const rowNull = wrapper.find('[data-test="po-PO-NULL"]')
		expect(rowNull.text()).toContain("—")
		expect(rowNull.text()).not.toContain("In transit")

		const rowUndef = wrapper.find('[data-test="po-PO-UNDEF"]')
		expect(rowUndef.text()).toContain("—")

		const rowUnknown = wrapper.find('[data-test="po-PO-UNKNOWN"]')
		expect(rowUnknown.text()).toContain("—")
	})

	it("renders shipping status label for mobile view", async () => {
		const poPreparing = {
			...SUBMITTED_ORDER,
			name: "PO-PREP",
			shipping_status: "preparing",
		}
		mocks.call.mockResolvedValue({ orders: [poPreparing] })
		const wrapper = mountOpen()
		await flushPromises()

		const rowPrep = wrapper.find('[data-test="po-PO-PREP"]')
		expect(rowPrep.text()).toContain("Shipping status:")
	})
})
