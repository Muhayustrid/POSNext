// @vitest-environment jsdom
//
// COR-FE-04 UI contract: sync_failed queued invoices surface in
// OfflineInvoicesDialog with a clear marker plus manual Retry and Delete
// actions, instead of being silently retried forever.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

const mocks = vi.hoisted(() => ({
	retryOfflineInvoice: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("@/stores/posSync", () => ({
	usePOSSyncStore: () => ({ retryOfflineInvoice: mocks.retryOfflineInvoice }),
}));

vi.mock("frappe-ui", async () => {
	const { defineComponent } = await import("vue");
	const Button = defineComponent({
		name: "Button",
		emits: ["click"],
		template: `<button v-bind="$attrs" @click="$emit('click')"><slot /></button>`,
	});
	const Dialog = defineComponent({
		name: "Dialog",
		props: {
			modelValue: { type: Boolean, default: false },
			options: { type: Object, default: () => ({}) },
		},
		template: `<div><slot name="body-content" /><slot name="actions" /></div>`,
	});
	return { Button, Dialog };
});

import OfflineInvoicesDialog from "./OfflineInvoicesDialog.vue";

const failedInvoice = {
	id: 1,
	offline_id: "pos_offline_a",
	sync_failed: true,
	retry_count: 3,
	error: "Validation failed",
	timestamp: Date.now(),
	data: { customer: "Andi", items: [{ item_code: "ITEM-1", qty: 1 }], grand_total: 50000 },
};

const pendingInvoice = {
	id: 2,
	offline_id: "pos_offline_b",
	retry_count: 0,
	timestamp: Date.now(),
	data: { customer: "Budi", items: [], grand_total: 1000 },
};

const mountDialog = async () => {
	// The dialog populates its list when the model transitions to open,
	// mirroring how POSSale.vue opens it after loading the pending list.
	const wrapper = mount(OfflineInvoicesDialog, {
		props: { modelValue: false, pendingInvoices: [failedInvoice, pendingInvoice] },
		global: { config: { globalProperties: { __: globalThis.__ } } },
	});
	await wrapper.setProps({ modelValue: true });
	await flushPromises();
	return wrapper;
};

beforeEach(() => {
	vi.clearAllMocks();
});

describe("OfflineInvoicesDialog COR-FE-04", () => {
	it("marks sync_failed entries with a badge and the stored error message", async () => {
		const wrapper = await mountDialog();
		expect(wrapper.text()).toContain("Sync failed");
		expect(wrapper.text()).toContain("Validation failed");
	});

	it("offers Retry only on failed entries and routes it through the sync store", async () => {
		const wrapper = await mountDialog();

		const retryButtons = wrapper.findAll('button[title="Retry sync"]');
		expect(retryButtons).toHaveLength(1);

		await retryButtons[0].trigger("click");
		await flushPromises();

		expect(mocks.retryOfflineInvoice).toHaveBeenCalledTimes(1);
		expect(mocks.retryOfflineInvoice).toHaveBeenCalledWith(1);
	});

	it("keeps the destructive delete flow behind confirmation for failed entries", async () => {
		const wrapper = await mountDialog();

		const deleteButtons = wrapper.findAll('button[title="Delete"]');
		expect(deleteButtons.length).toBeGreaterThanOrEqual(1);

		await deleteButtons[0].trigger("click");
		await flushPromises();

		expect(wrapper.text()).toContain("Are you sure you want to delete this offline invoice?");
	});
});
