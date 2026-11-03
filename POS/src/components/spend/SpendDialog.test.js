/** @vitest-environment jsdom */
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SpendDialog from "./SpendDialog.vue";

const { call, compressImage } = vi.hoisted(() => ({
	call: vi.fn(),
	compressImage: vi.fn(async (file) => file),
}));
vi.mock("@/utils/apiWrapper", () => ({ call }));
vi.mock("@/utils/compressImage", () => ({ compressImage }));
vi.mock("frappe-ui", () => ({
	Button: { template: "<button><slot /></button>" },
	FeatherIcon: { template: "<span />" },
	Dialog: {
		props: ["modelValue"],
		template:
			'<div v-if="modelValue"><slot name="body-content" /><slot name="actions" /></div>',
	},
}));

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
		item_details: [
			{ item_code: "Paper", item_name: "Printing paper", stock_uom: "Pack" },
			{ item_code: "Pen", item_name: "Blue pen", stock_uom: "Nos" },
		],
		default_supplier_name: "Local stationery shop",
		payment_sources: [
			{ mode_of_payment: "Bank", is_default: 1, is_shift_source: 0 },
		],
	},
];
let wrapper;
const find = (id) => wrapper.get(`[data-test="${id}"]`);
async function open(form = true) {
	wrapper = mount(SpendDialog, {
		props: { company: "Alpha", posProfile: "Alpha POS" },
		global: { config: { globalProperties: { __: (s) => s } } },
	});
	await flushPromises();
	if (form) {
		await find("spend-new-tab").trigger("click");
		await flushPromises();
	}
}
async function selectTemplate(value) {
 const picker = wrapper.findAllComponents({ name: "AutocompleteSelect" }).find((c) => c.attributes("data-test") === "spend-template");
 picker.vm.$emit("update:modelValue", value);
 await flushPromises();
}
async function fillJE() {
	if (!wrapper.find('[data-test="spend-form"]').exists())
		await find("spend-new-tab").trigger("click");

	await selectTemplate("electricity");
	await find("spend-description").setValue("Monthly bill");
	await find("spend-amount").setValue("12345");
}
async function submit() {
	await find("spend-form").trigger("submit");
	await flushPromises();
}
const submissions = () =>
	call.mock.calls
		.filter(([method]) => method.endsWith("submit_spend"))
		.map(([, args]) => JSON.parse(args.data));

beforeEach(() => {
	call.mockReset();
	compressImage.mockClear();
	call.mockImplementation(async (method) => {
		if (method.endsWith("get_spend_templates"))
			return structuredClone(templates);
		if (method.endsWith("get_spend_history")) return [];
		return { doctype: "Journal Entry", name: "JE-1", amount: 12345 };
	});
});
afterEach(() => wrapper?.unmount());

describe("SpendDialog", () => {
	it("starts on the purchase-style history list and opens a separate form", async () => {
		await open(false);
		expect(wrapper.find('[data-test="spend-form"]').exists()).toBe(false);
		expect(wrapper.text()).toContain("No purchases or expenses recorded yet.");
		await find("spend-new-tab").trigger("click");
		expect(find("spend-form").exists()).toBe(true);
	});

	it("shows human item names and unit context while retaining codes", async () => {
		await open();
		await selectTemplate("supplies");
		const item = wrapper
			.findAllComponents({ name: "AutocompleteSelect" })
			.find((c) => c.attributes("data-test") === "spend-item");
		await item.get("input").trigger("focus");
		expect(item.text()).toContain("Printing paper");
		expect(item.text()).toContain("Paper · Pack");
		await item.get("input").setValue("Printing");
		await item
			.findAll(".dropdown-item")
			.find((row) => row.text().includes("Printing paper"))
			.trigger("click");
		await flushPromises();
		expect(wrapper.text()).toContain("Printing paper");
		expect(wrapper.text()).toContain("Paper · Pack");
	});

	it("retains unsaved form when discard is cancelled", async () => {
		await open();
		await fillJE();
		await find("spend-back").trigger("click");
		const confirm = wrapper.findComponent({ name: "ConfirmDialog" });
		expect(confirm.props("modelValue")).toBe(true);
		confirm.vm.$emit("update:modelValue", false);
		await flushPromises();
		expect(find("spend-description").element.value).toBe("Monthly bill");
	});

	it("cannot discard an uncertain attempt via back or reset", async () => {
		await open();
		await fillJE();
		call.mockRejectedValue(new Error("Lost response"));
		await submit();
		expect(find("spend-back").attributes("disabled")).toBeDefined();
		await find("spend-back").trigger("click");
		expect(find("spend-form").exists()).toBe(true);
		await submit();
		expect(submissions()[1]).toEqual(submissions()[0]);
	});

	it("uses purchase-style information, picker and notes-summary sections", async () => {
		await open();
		const picker = wrapper.findAllComponents({ name: "AutocompleteSelect" }).find((c) => c.attributes("data-test") === "spend-template");
		expect(picker).toBeTruthy();
		expect(picker.props("options")).toEqual(expect.arrayContaining([{ value: "electricity", label: "Electricity", subtitle: "Utilities" }]));
		expect(wrapper.find('[data-test="spend-information"]').exists()).toBe(true);
		await selectTemplate("supplies");
		expect(wrapper.find('[data-test="spend-notes"]').exists()).toBe(true);
		expect(wrapper.find('[data-test="spend-summary"]').exists()).toBe(true);
		expect(find("spend-qty").attributes("aria-label")).toBeTruthy();
	});

	it("retry after network error reuses client_request_id and original payload", async () => {
		await open();
		await fillJE();
		let attempts = 0;
		call.mockImplementation(async (method) => {
			if (method.endsWith("get_spend_history")) return [];
			if (method.endsWith("submit_spend") && ++attempts === 1)
				throw new Error("Network error");
			return { name: "JE-1" };
		});
		await submit();
		expect(find("spend-error").text()).toContain("Network error");
		await submit();
		expect(submissions()).toHaveLength(2);
		expect(submissions()[0].client_request_id).toBeTruthy();
		expect(submissions()[1]).toEqual(submissions()[0]);
	});

	it("new form after success gets a new id", async () => {
		await open();
		await fillJE();
		await submit();
		await fillJE();
		await submit();
		expect(submissions()).toHaveLength(2);
		expect(submissions()[1].client_request_id).not.toBe(
			submissions()[0].client_request_id,
		);
	});

	it("je template shows amount lines, pi template shows item lines", async () => {
		await open();
		await fillJE();
		expect(wrapper.find('[data-test="spend-amount"]').exists()).toBe(true);
		expect(wrapper.find('[data-test="spend-item"]').exists()).toBe(false);
		await selectTemplate("supplies");
		expect(wrapper.find('[data-test="spend-description"]').exists()).toBe(
			false,
		);
		wrapper
			.findAllComponents({ name: "AutocompleteSelect" })
			.find((c) => c.attributes("data-test") === "spend-item")
			.vm.$emit("update:modelValue", "Paper");
		await flushPromises();
		await find("spend-qty").setValue(2);
		await find("spend-rate").setValue(5000);
		expect(find("spend-total").text()).toMatch(/Rp\s*10[.,]000/);
		await submit();
		expect(submissions()[0]).toMatchObject({
			company: "Alpha",
			pos_profile: "Alpha POS",
			supplier: "Local Supplier",
			mode_of_payment: "Bank",
			lines: [{ item_code: "Paper", qty: 2, rate: 5000 }],
		});
	});

	it("shift source badge shown only for selected shift source", async () => {
		await open();
		await fillJE();
		expect(find("spend-shift-source").text()).toBe("Shift drawer");
		await selectTemplate("supplies");
		expect(wrapper.find('[data-test="spend-shift-source"]').exists()).toBe(
			false,
		);
	});

	it("cancel hidden when can_cancel false and asks reason when allowed", async () => {
		call.mockImplementation(async (method) => {
			if (method.endsWith("get_spend_templates")) return templates;
			if (method.endsWith("get_spend_detail")) return { doctype: "Journal Entry", name: "JE-open", can_cancel: true, items: [], amount: 200, status: "Submitted" };
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
				];
			return { name: "JE-open" };
		});
		await open(false);
		await find("spend-history-tab").trigger("click");
		await flushPromises();
		const rows = wrapper.findAll('[data-test="spend-history-row"]');
		expect(rows[0].find('[data-test="spend-cancel"]').exists()).toBe(false);
		expect(wrapper.find('[data-test="spend-cancel"]').exists()).toBe(false);
		await rows[1].trigger("click");
		await flushPromises();
		expect(wrapper.find('[data-test="spend-detail"]').exists()).toBe(true);
		expect(wrapper.find('[data-test="spend-history-row"]').exists()).toBe(false);
		await find("spend-cancel").trigger("click");
		expect(wrapper.find('[data-test="spend-cancel-reason"]').exists()).toBe(
			true,
		);
		expect(find("spend-cancel-confirm").attributes("disabled")).toBeDefined();
		await find("spend-cancel-reason").setValue("Wrong bill");
		await find("spend-cancel-form").trigger("submit");
		await flushPromises();
		expect(call).toHaveBeenCalledWith("pos_next.api.spend.cancel_spend", {
			doctype: "Journal Entry",
			name: "JE-open",
			reason: "Wrong bill",
		});
	});

	it("disables submit while in flight and sends only once", async () => {
		await open();
		await fillJE();
		let finish;
		call.mockImplementation(
			() =>
				new Promise((resolve) => {
					finish = resolve;
				}),
		);
		await find("spend-form").trigger("submit");
		await flushPromises();
		expect(find("spend-submit").attributes("disabled")).toBeDefined();
		await find("spend-form").trigger("submit");
		expect(submissions()).toHaveLength(1);
		finish({ name: "JE-1" });
		await flushPromises();
	});

	it("compresses and sends attachments in the same submit transaction", async () => {
		await open();
		await fillJE();
		const file = new File(["receipt"], "receipt.jpg", { type: "image/jpeg" });
		wrapper
			.findComponent({ name: "PurchaseAttachments" })
			.vm.$emit("add", [file]);
		await flushPromises();
		await submit();
		expect(compressImage).toHaveBeenCalledWith(file);
		await vi.waitFor(() => expect(submissions()).toHaveLength(1));
		expect(submissions()[0].files).toEqual([
			{ file_name: "receipt.jpg", filedata: "cmVjZWlwdA==" },
		]);
	});

	it("removes the attachment emitted by PurchaseAttachments", async () => {
		await open();
		await fillJE();
		const first = new File(["one"], "one.pdf", { type: "application/pdf" });
		const second = new File(["two"], "two.pdf", { type: "application/pdf" });
		const attachments = wrapper.findComponent({ name: "PurchaseAttachments" });
		attachments.vm.$emit("add", [first, second]);
		await flushPromises();
		attachments.vm.$emit("remove", first);
		await flushPromises();
		expect(attachments.props("pending")).toEqual([second]);
	});

	it("displays humanized server errors as text", async () => {
		await open();
		await fillJE();
		call.mockRejectedValue({
			message: "technical exception",
			messages: ["<b>Shift is closed</b>"],
		});
		await submit();
		expect(find("spend-error").text()).toBe("Shift is closed");
		expect(find("spend-error").find("b").exists()).toBe(false);
	});

	it("ignores stale template responses after changing company", async () => {
		await open();
		let finish;
		call.mockImplementationOnce(
			() =>
				new Promise((resolve) => {
					finish = resolve;
				}),
		);
		await wrapper.setProps({ company: "Beta", posProfile: "Beta POS" });
		await wrapper.setProps({ company: "Gamma", posProfile: "Gamma POS" });
		await flushPromises();
		finish([{ ...templates[0], name: "stale", title: "Stale" }]);
		await flushPromises();
		expect(find("spend-template").text()).not.toContain("Stale");
	});
});
