/** @vitest-environment jsdom */
import { flushPromises, mount } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import SpendDialog from "./SpendDialog.vue"

const { call, compressImage } = vi.hoisted(() => ({
	call: vi.fn(),
	compressImage: vi.fn(async (file) => file),
}))
vi.mock("@/utils/apiWrapper", () => ({ call }))
vi.mock("@/utils/compressImage", () => ({ compressImage }))
vi.mock("frappe-ui", () => ({
	Button: { template: "<button><slot /></button>" },
	FeatherIcon: { template: "<span />" },
}))

const templates = [
	{
		name: "electricity",
		title: "Electricity",
		category: "Utilities",
		document_type: "Journal Entry",
		payment_sources: [
			{ mode_of_payment: "Drawer", is_default: 1, is_shift_source: 1 },
		],
	},
	{
		name: "supplies",
		title: "Supplies",
		category: "Office",
		document_type: "Purchase Invoice",
		default_supplier: "Local Supplier",
		allow_supplier_change: 1,
		items: ["Paper", "Pen"],
		payment_sources: [
			{ mode_of_payment: "Bank", is_default: 1, is_shift_source: 0 },
		],
	},
]
let wrapper
const find = (id) => wrapper.get(`[data-test="${id}"]`)
async function open() {
	wrapper = mount(SpendDialog, {
		props: { company: "Alpha", posProfile: "Alpha POS" },
		global: { config: { globalProperties: { __: (s) => s } } },
	})
	await flushPromises()
}
async function fillJE() {
	await find("spend-template").setValue("electricity")
	await find("spend-description").setValue("Monthly bill")
	await find("spend-amount").setValue("12345")
}
async function submit() {
	await find("spend-form").trigger("submit")
	await flushPromises()
}
const submissions = () =>
	call.mock.calls
		.filter(([method]) => method.endsWith("submit_spend"))
		.map(([, args]) => JSON.parse(args.data))

beforeEach(() => {
	call.mockReset()
	compressImage.mockClear()
	call.mockImplementation(async (method) => {
		if (method.endsWith("get_spend_templates"))
			return structuredClone(templates)
		if (method.endsWith("get_spend_history")) return []
		return { doctype: "Journal Entry", name: "JE-1", amount: 12345 }
	})
})
afterEach(() => wrapper?.unmount())

describe("SpendDialog", () => {
	it("renders templates grouped by category", async () => {
		await open()
		expect(
			find("spend-template")
				.findAll("optgroup")
				.map((g) => g.attributes("label")),
		).toEqual(["Utilities", "Office"])
	})

	it("retry after network error reuses client_request_id and original payload", async () => {
		await open()
		await fillJE()
		let attempts = 0
		call.mockImplementation(async (method) => {
			if (method.endsWith("submit_spend") && ++attempts === 1)
				throw new Error("Network error")
			return { name: "JE-1" }
		})
		await submit()
		expect(find("spend-error").text()).toContain("Network error")
		await submit()
		expect(submissions()).toHaveLength(2)
		expect(submissions()[0].client_request_id).toBeTruthy()
		expect(submissions()[1]).toEqual(submissions()[0])
	})

	it("new form after success gets a new id", async () => {
		await open()
		await fillJE()
		await submit()
		await fillJE()
		await submit()
		expect(submissions()).toHaveLength(2)
		expect(submissions()[1].client_request_id).not.toBe(
			submissions()[0].client_request_id,
		)
	})

	it("je template shows amount lines, pi template shows item lines", async () => {
		await open()
		await fillJE()
		expect(wrapper.find('[data-test="spend-amount"]').exists()).toBe(true)
		expect(wrapper.find('[data-test="spend-item"]').exists()).toBe(false)
		await find("spend-template").setValue("supplies")
		expect(wrapper.find('[data-test="spend-description"]').exists()).toBe(false)
		await find("spend-item").setValue("Paper")
		await find("spend-qty").setValue(2)
		await find("spend-rate").setValue(5000)
		expect(find("spend-total").text()).toMatch(/Rp\s*10[.,]000/)
		await submit()
		expect(submissions()[0]).toMatchObject({
			company: "Alpha",
			pos_profile: "Alpha POS",
			supplier: "Local Supplier",
			mode_of_payment: "Bank",
			lines: [{ item_code: "Paper", qty: 2, rate: 5000 }],
		})
	})

	it("shift source badge shown only for selected shift source", async () => {
		await open()
		await fillJE()
		expect(find("spend-shift-source").text()).toBe("Shift drawer")
		await find("spend-template").setValue("supplies")
		expect(wrapper.find('[data-test="spend-shift-source"]').exists()).toBe(
			false,
		)
	})

	it("cancel hidden when can_cancel false and asks reason when allowed", async () => {
		call.mockImplementation(async (method) => {
			if (method.endsWith("get_spend_templates")) return templates
			if (method.endsWith("get_spend_history"))
				return [
					{
						doctype: "Journal Entry",
						name: "JE-closed",
						status: "Submitted",
						docstatus: 1,
						amount: 100,
						can_cancel: false,
					},
					{
						doctype: "Journal Entry",
						name: "JE-open",
						status: "Submitted",
						docstatus: 1,
						amount: 200,
						can_cancel: true,
					},
				]
			return { name: "JE-open" }
		})
		await open()
		await find("spend-history-tab").trigger("click")
		await flushPromises()
		const rows = wrapper.findAll('[data-test="spend-history-row"]')
		expect(rows[0].find('[data-test="spend-cancel"]').exists()).toBe(false)
		await rows[1].get('[data-test="spend-cancel"]').trigger("click")
		expect(wrapper.find('[data-test="spend-cancel-reason"]').exists()).toBe(
			true,
		)
		expect(find("spend-cancel-confirm").attributes("disabled")).toBeDefined()
		await find("spend-cancel-reason").setValue("Wrong bill")
		await find("spend-cancel-form").trigger("submit")
		await flushPromises()
		expect(call).toHaveBeenCalledWith("pos_next.api.spend.cancel_spend", {
			doctype: "Journal Entry",
			name: "JE-open",
			reason: "Wrong bill",
		})
	})

	it("disables submit while in flight and sends only once", async () => {
		await open()
		await fillJE()
		let finish
		call.mockImplementation(
			() =>
				new Promise((resolve) => {
					finish = resolve
				}),
		)
		await find("spend-form").trigger("submit")
		await flushPromises()
		expect(find("spend-submit").attributes("disabled")).toBeDefined()
		await find("spend-form").trigger("submit")
		expect(submissions()).toHaveLength(1)
		finish({ name: "JE-1" })
		await flushPromises()
	})

	it("compresses and sends attachments in the same submit transaction", async () => {
		await open()
		await fillJE()
		const file = new File(["receipt"], "receipt.jpg", { type: "image/jpeg" })
		wrapper
			.findComponent({ name: "PurchaseAttachments" })
			.vm.$emit("add", [file])
		await flushPromises()
		await submit()
		expect(compressImage).toHaveBeenCalledWith(file)
		await vi.waitFor(() => expect(submissions()).toHaveLength(1))
		expect(submissions()[0].files).toEqual([
			{ file_name: "receipt.jpg", filedata: "cmVjZWlwdA==" },
		])
	})

	it("removes the attachment emitted by PurchaseAttachments", async () => {
		await open()
		await fillJE()
		const first = new File(["one"], "one.pdf", { type: "application/pdf" })
		const second = new File(["two"], "two.pdf", { type: "application/pdf" })
		const attachments = wrapper.findComponent({ name: "PurchaseAttachments" })
		attachments.vm.$emit("add", [first, second])
		await flushPromises()
		attachments.vm.$emit("remove", first)
		await flushPromises()
		expect(attachments.props("pending")).toEqual([second])
	})

	it("displays humanized server errors as text", async () => {
		await open()
		await fillJE()
		call.mockRejectedValue({
			message: "technical exception",
			messages: ["<b>Shift is closed</b>"],
		})
		await submit()
		expect(find("spend-error").text()).toBe("Shift is closed")
		expect(find("spend-error").find("b").exists()).toBe(false)
	})

	it("ignores stale template responses after changing company", async () => {
		await open()
		let finish
		call.mockImplementationOnce(
			() =>
				new Promise((resolve) => {
					finish = resolve
				}),
		)
		await wrapper.setProps({ company: "Beta", posProfile: "Beta POS" })
		await wrapper.setProps({ company: "Gamma", posProfile: "Gamma POS" })
		await flushPromises()
		finish([{ ...templates[0], name: "stale", title: "Stale" }])
		await flushPromises()
		expect(find("spend-template").text()).not.toContain("Stale")
	})
})
