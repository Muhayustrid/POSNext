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

vi.mock("frappe-ui", async () => {
	const { defineComponent, h } = await import("vue")
	const Button = defineComponent({
		name: "Button",
		props: ["variant", "theme", "loading"],
		setup(_, { slots }) {
			return () => h("button", { type: "button" }, slots.default?.())
		},
	})
	const Dialog = defineComponent({
		name: "Dialog",
		props: { modelValue: Boolean, options: Object },
		setup(_, { slots }) {
			return () =>
				h("div", { "data-test": "dialog" }, [
					slots["body-content"]?.(),
					slots.actions?.(),
				])
		},
	})
	return { Button, Dialog }
})

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

const RECEIPT = {
	name: "PR-2026-00001",
	supplier: "SUP-1",
	supplier_name: "Roti Ltd",
	posting_date: "2026-09-19",
	status: "To Bill",
	docstatus: 1,
	grand_total: 150000,
	currency: "IDR",
	per_billed: 40,
	company: "Test Co",
	modified: "2026-09-19 10:00:00",
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

// Fill the form until it is saveable: supplier selected + one item row at qty 3.
async function fillForm(wrapper) {
	mocks.call.mockImplementation(async (method) => {
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
				conversion_factor: 1,
				price_list_rate: 100,
				rate: 90,
				warehouse: "WH-1",
			}
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

	it("New button switches to the form view", async () => {
		mocks.call.mockResolvedValue({ orders: [DRAFT_ORDER] })
		const wrapper = mountOpen()
		await flushPromises()
		expect(wrapper.text()).toContain("PO-2026-00001")

		await button(wrapper, "New").trigger("click")
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

		await button(wrapper, "New").trigger("click")
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

		await button(wrapper, "New").trigger("click")
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
		await button(wrapper, "Save Draft").trigger("click")
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
		await button(wrapper, "New").trigger("click")

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

	it("picking an item prices it and amount follows qty * rate", async () => {
		mocks.call.mockResolvedValue({ orders: [] })
		const wrapper = mountOpen()
		await flushPromises()
		await button(wrapper, "New").trigger("click")
		await fillForm(wrapper)

		const table = wrapper.find('[data-test="items-table"]')
		expect(table.text()).toContain("Roti Tawar")
		// price_list_rate (100) wins over rate (90); qty edited to 3 -> 300
		expect(mocks.call).toHaveBeenCalledWith(
			"pos_next.api.purchase_orders.get_purchase_item_details",
			expect.objectContaining({
				item_code: "ITEM-1",
				supplier: "SUP-1",
				warehouse: "WH-1",
			}),
		)
		expect(table.text()).toContain("300")
	})

	it("Save Draft posts the full payload with submit: 0", async () => {
		mocks.call.mockImplementation((method) => {
			if (method.includes("get_po_defaults"))
				return Promise.resolve({ supplier: null, supplier_name: null, warehouse: "WH-1" })
			return Promise.resolve({ orders: [] })
		})
		const wrapper = mountOpen()
		await flushPromises()
		await button(wrapper, "New").trigger("click")
		await fillForm(wrapper)

		mocks.call.mockImplementation(async (method) =>
			method.includes("save_purchase_order") ? { name: "PO-2026-00002" } : {},
		)
		await button(wrapper, "Save Draft").trigger("click")
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
		await button(wrapper, "New").trigger("click")
		await fillForm(wrapper)

		mocks.call.mockImplementation(async (method) => {
			if (method.includes("save_purchase_order"))
				throw Object.assign(new Error("Request failed"), {
					messages: ["Invalid warehouse WH-X in item row"],
					httpStatus: 417,
				})
			return {}
		})
		await button(wrapper, "Save Draft").trigger("click")
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
		mocks.call.mockResolvedValue({ orders: [DRAFT_ORDER] })
		const wrapper = mountOpen()
		await flushPromises()

		// list: draft row has no Submit action
		expect(button(wrapper, "Submit")).toBeUndefined()

		await button(wrapper, "New").trigger("click")
		// form: no Save & Submit in the footer
		expect(button(wrapper, "Save & Submit")).toBeUndefined()
		expect(button(wrapper, "Save Draft")).toBeDefined()

		// with the permission the buttons appear
		mocks.perms.submit = true
		await wrapper.setProps({ modelValue: false })
		await flushPromises()
		await wrapper.setProps({ modelValue: true })
		await flushPromises()
		expect(button(wrapper, "Submit")).toBeDefined()
		await button(wrapper, "New").trigger("click")
		expect(button(wrapper, "Save & Submit")).toBeDefined()
	})

	// ---------- purchase receipts ----------

	function mockPrFlow() {
		mocks.call.mockImplementation(async (method) => {
			if (method.includes("get_purchase_orders")) return { orders: [SUBMITTED_ORDER] }
			if (method.includes("get_purchase_receipt_draft")) return PR_DRAFT
			return {}
		})
	}

	async function openReceiveView(wrapper) {
		await wrapper.find('[data-test="receive-button"]').trigger("click")
		await flushPromises()
	}

	it("shows Receive only on partially received submitted POs, gated by PR create permission", async () => {
		mocks.call.mockResolvedValue({
			orders: [DRAFT_ORDER, SUBMITTED_ORDER, FULLY_RECEIVED_ORDER],
		})
		const wrapper = mountOpen()
		await flushPromises()

		// one button: on the submitted PO with per_received < 100 — not on the
		// draft, not on the fully received one (gating is per_received, not status)
		const buttons = wrapper.findAll('[data-test="receive-button"]')
		expect(buttons).toHaveLength(1)
		expect(wrapper.text()).toContain("PO-2026-00002")

		mocks.perms.create = false
		await wrapper.setProps({ modelValue: false })
		await flushPromises()
		await wrapper.setProps({ modelValue: true })
		await flushPromises()
		expect(wrapper.findAll('[data-test="receive-button"]')).toHaveLength(0)
	})

	it("badges internal supplier POs once the factory SO exists", async () => {
		mocks.call.mockResolvedValue({
			orders: [SUBMITTED_ORDER, INTERNAL_ORDER],
		})
		const wrapper = mountOpen()
		await flushPromises()

		// internal POs keep their Receive button too — only the draft source
		// differs (see the routing test below)
		expect(wrapper.findAll('[data-test="receive-button"]')).toHaveLength(2)
		expect(wrapper.text()).toContain("Factory SO")

		// an internal PO whose factory SO hasn't been made yet: no badge
		const notYetLinked = { ...INTERNAL_ORDER, name: "PO-2026-00005", inter_company_order_reference: null }
		mocks.call.mockResolvedValue({ orders: [SUBMITTED_ORDER, notYetLinked] })
		await wrapper.setProps({ modelValue: false })
		await flushPromises()
		await wrapper.setProps({ modelValue: true })
		await flushPromises()
		expect(wrapper.findAll('[data-test="receive-button"]')).toHaveLength(2)
		expect(wrapper.text()).not.toContain("Factory SO")
	})

	it("routes internal POs to the Delivery Note draft and submits the DN links", async () => {
		mocks.call.mockImplementation(async (method) => {
			if (method.includes("get_purchase_orders"))
				return { orders: [INTERNAL_ORDER] }
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

		await wrapper.find('[data-test="receive-button"]').trigger("click")
		await flushPromises()

		expect(mocks.call).toHaveBeenCalledWith(
			"pos_next.api.purchase_receipts.get_intercompany_receipt_draft",
			{ po_name: "PO-2026-00004" },
		)
		expect(wrapper.text()).toContain("From Delivery Note: MAT-DN-2026-00001")

		mocks.call.mockImplementation(async (method) =>
			method.includes("save_purchase_receipt") ? { name: "PR-2026-00009" } : {},
		)
		await button(wrapper, "Submit Receipt").trigger("click")
		await flushPromises()

		const saveCall = mocks.call.mock.calls.find(([method]) => method.includes("save_purchase_receipt"))
		const data = JSON.parse(saveCall[1].data)
		// the DN reference and row links ride the payload so the PR keeps the
		// DN <-> PR cross links
		expect(data.inter_company_reference).toBe("MAT-DN-2026-00001")
		expect(data.items[0].delivery_note_item).toBe("DNROW-1")
	})

	it("Receive loads the draft and prefills qty with the pending qty", async () => {
		mockPrFlow()
		const wrapper = mountOpen()
		await flushPromises()
		await openReceiveView(wrapper)

		expect(mocks.call).toHaveBeenCalledWith(
			"pos_next.api.purchase_receipts.get_purchase_receipt_draft",
			{ po_name: "PO-2026-00002" },
		)
		expect(wrapper.find('[data-test="receive-table"]').exists()).toBe(true)
		const qtyInputs = wrapper.findAll('[data-test="receive-qty"]')
		expect(qtyInputs).toHaveLength(2)
		expect(qtyInputs[0].element.value).toBe("6")
		expect(qtyInputs[1].element.value).toBe("5")
		expect(wrapper.find('[data-test="receive-posting-date"]').element.value).toBe(
			"2026-09-19",
		)
	})

	it("submits only rows with qty > 0 carrying the PO links, with the submit flag", async () => {
		mockPrFlow()
		const wrapper = mountOpen()
		await flushPromises()
		await openReceiveView(wrapper)

		const qtyInputs = wrapper.findAll('[data-test="receive-qty"]')
		await qtyInputs[0].setValue(3)
		await qtyInputs[1].setValue(0) // not receiving this row now

		mocks.call.mockImplementation(async (method) =>
			method.includes("save_purchase_receipt") ? { name: "PR-2026-00009" } : {},
		)
		await button(wrapper, "Submit Receipt").trigger("click")
		await flushPromises()

		const saveCall = mocks.call.mock.calls.find(([m]) =>
			m.includes("save_purchase_receipt"),
		)
		expect(saveCall[0]).toBe(
			"pos_next.api.purchase_receipts.save_purchase_receipt",
		)
		expect(saveCall[1]).toEqual(expect.objectContaining({ submit: 1 }))
		const data = JSON.parse(saveCall[1].data)
		expect(data.supplier).toBe("SUP-1")
		expect(data.posting_date).toBe("2026-09-19")
		expect(data.items).toEqual([
			{
				item_code: "ITEM-1",
				qty: 3,
				rate: 100,
				uom: "Nos",
				warehouse: "WH-1",
				purchase_order: "PO-2026-00002",
				purchase_order_item: "POI-1",
			},
		])
		expect(mocks.toast.success).toHaveBeenCalledWith(
			"Purchase Receipt PR-2026-00009 submitted",
		)
		// back on the PO list, refreshed
		expect(mocks.call).toHaveBeenCalledWith(
			"pos_next.api.purchase_orders.get_purchase_orders",
			expect.anything(),
		)
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
		await button(wrapper, "Save Draft").trigger("click")
		await flushPromises()

		expect(mocks.toast.error).toHaveBeenCalledWith(
			"Cannot receive more than ordered quantity",
		)
		expect(mocks.toast.success).not.toHaveBeenCalled()
	})

	it("Receipts segment lists receipts and gates Cancel behind the PR cancel permission", async () => {
		const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true)
		mocks.call.mockImplementation(async (method) => {
			if (method.includes("get_purchase_orders")) return { orders: [] }
			if (method.includes("get_purchase_receipts")) return { receipts: [RECEIPT] }
			return {}
		})
		const wrapper = mountOpen()
		await flushPromises()

		await wrapper.find('[data-test="receipts-toggle"]').trigger("click")
		await flushPromises()

		expect(mocks.call).toHaveBeenCalledWith(
			"pos_next.api.purchase_receipts.get_purchase_receipts",
			expect.objectContaining({ pos_profile: "POS-1" }),
		)
		expect(wrapper.text()).toContain("PR-2026-00001")
		expect(wrapper.text()).toContain("Billed 40%")
		expect(wrapper.find('[data-test="cancel-receipt"]').exists()).toBe(true)

		await wrapper.find('[data-test="cancel-receipt"]').trigger("click")
		await flushPromises()
		expect(confirmSpy).toHaveBeenCalled()
		expect(mocks.call).toHaveBeenCalledWith(
			"pos_next.api.purchase_receipts.cancel_purchase_receipt",
			{ name: "PR-2026-00001" },
		)

		// without the cancel permission the button is gone
		mocks.perms.cancel = false
		await wrapper.setProps({ modelValue: false })
		await flushPromises()
		await wrapper.setProps({ modelValue: true })
		await flushPromises()
		await wrapper.find('[data-test="receipts-toggle"]').trigger("click")
		await flushPromises()
		expect(wrapper.find('[data-test="cancel-receipt"]').exists()).toBe(false)
		confirmSpy.mockRestore()
	})
})
