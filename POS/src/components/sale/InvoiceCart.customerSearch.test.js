// PERF-04: the customer dropdown re-filtered the full customer array on
// every keystroke (main-thread block at 50k rows). These pin the debounced
// contract: results only apply after typing pauses for >=250 ms, rapid
// keystrokes collapse into the final query, and clearing the field still
// updates instantly (clear/empty-state behavior unchanged).
import { afterEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia } from "pinia";

// The app installs __() as a global property; templates need it before the
// component module is evaluated.
vi.hoisted(() => {
	globalThis.__ = (message, replacements = []) => {
		if (!Array.isArray(replacements) || !replacements.length) return message;
		let out = message;
		for (const [i, v] of replacements.entries())
			out = out.split(`{${i}}`).join(String(v));
		return out;
	};
});

const mocks = vi.hoisted(() => ({ call: vi.fn() }));

vi.mock("@/utils/apiWrapper", () => ({ call: mocks.call }));

vi.mock("@/utils/offline", () => ({ isOffline: () => false }));

vi.mock("@/utils/offline/workerClient", () => ({
	// catch-all: every worker method becomes a rejected async no-op
	offlineWorker: new Proxy(
		{},
		{
			get: (t, prop) =>
				prop in t ? t[prop] : vi.fn().mockRejectedValue(new Error("offline")),
		},
	),
}));

vi.mock("@/composables/useFormatters", () => ({
	useFormatters: () => ({
		formatCurrency: (v) => String(v ?? ""),
		formatQuantity: (v) => String(v ?? ""),
		formatDate: (v) => String(v ?? ""),
		formatTime: (v) => String(v ?? ""),
		formatDateTime: (v) => String(v ?? ""),
	}),
}));

vi.mock("frappe-ui", async () => {
	const { defineComponent, reactive } = await import("vue")
	const Button = defineComponent({
		name: "Button",
		emits: ["click"],
		template: `<button v-bind="$attrs" @click="$emit('click')"><slot /></button>`,
	})
	const Dialog = defineComponent({
		name: "Dialog",
		props: {
			modelValue: { type: Boolean, default: false },
			options: { type: Object, default: () => ({}) },
		},
		template: `<div><slot name="body" /><slot name="body-content" /><slot name="actions" /></div>`,
	})
	const stub = defineComponent({ name: "FrappeUIStub", render: () => null })
	return {
		Button,
		Dialog,
		FeatherIcon: stub,
		Input: stub,
		TextInput: stub,
		createResource: () =>
			reactive({ loading: false, data: null, error: null, reload: vi.fn(async () => {}) }),
	}
})

vi.mock("reka-ui", () => ({
	DialogTitle: { name: "DialogTitle", template: "<h2><slot /></h2>" },
}))

import InvoiceCart from "./InvoiceCart.vue";
import { useCustomerSearchStore } from "@/stores/customerSearch";

const CUSTOMERS = [
	{ name: "CUST-1", customer_name: "Budi Santoso", mobile_no: "0811", email_id: "" },
	{ name: "CUST-2", customer_name: "Budi Hartono", mobile_no: "", email_id: "" },
	{ name: "CUST-3", customer_name: "budiwijaya", mobile_no: "", email_id: "" },
	{ name: "CUST-4", customer_name: "Andi Pratama", mobile_no: "0812", email_id: "" },
	{ name: "CUST-5", customer_name: "Citra Lestari", mobile_no: "0813", email_id: "" },
	{ name: "CUST-6", customer_name: "Dewi Anggraini", mobile_no: "", email_id: "" },
	{ name: "CUST-7", customer_name: "Eko Prasetyo", mobile_no: "", email_id: "" },
	{ name: "CUST-8", customer_name: "Fitri Handayani", mobile_no: "", email_id: "" },
	{ name: "CUST-9", customer_name: "Gita Savitri", mobile_no: "", email_id: "" },
	{ name: "CUST-10", customer_name: "Hadi Wijaya", mobile_no: "", email_id: "" },
	{ name: "CUST-11", customer_name: "Indah Permata", mobile_no: "", email_id: "" },
	{ name: "CUST-12", customer_name: "Joko Susilo", mobile_no: "", email_id: "" },
	{ name: "CUST-13", customer_name: "Kartika Sari", mobile_no: "", email_id: "" },
	{ name: "CUST-14", customer_name: "Lestari Ningsih", mobile_no: "", email_id: "" },
	{ name: "CUST-15", customer_name: "Maya Puspita", mobile_no: "", email_id: "" },
];

// The customer dropdown container; distinct from the UOM dropdown (no
// max-h-48) so the selector only matches customer results.
const dropdown = (wrapper) => wrapper.find("div.z-50.max-h-48");
const resultButtons = (wrapper) => wrapper.findAll("div.z-50 .overflow-y-auto button");

function mountCart() {
	return mount(InvoiceCart, {
		props: {
			items: [],
			customer: null,
			subtotal: 0,
			taxAmount: 0,
			discountAmount: 0,
			grandTotal: 0,
			posProfile: "Kasir 1",
			currency: "IDR",
			appliedOffers: [],
			warehouses: [],
		},
		global: {
			plugins: [createPinia()],
			config: { globalProperties: { __: globalThis.__ } },
		},
	});
}

async function mountCartWithCustomers() {
	const wrapper = mountCart();
	await flushPromises();

	const store = useCustomerSearchStore();
	store.allCustomers = CUSTOMERS;
	await flushPromises();

	const input = wrapper.find("#cart-customer-search");
	return { wrapper, store, input };
}

describe("customer search debounce in InvoiceCart (PERF-04)", () => {
	afterEach(() => {
		vi.useRealTimers();
	});

	it("does not apply the query until typing pauses for 250 ms", async () => {
		vi.useFakeTimers();
		const { wrapper, input } = await mountCartWithCustomers();

		await input.setValue("budi");

		// Old behavior filtered instantly; the query must not be applied yet.
		expect(dropdown(wrapper).exists()).toBe(false);
		expect(resultButtons(wrapper)).toHaveLength(0);

		await vi.advanceTimersByTime(300);
		await flushPromises();

		expect(dropdown(wrapper).exists()).toBe(true);
		const buttons = resultButtons(wrapper);
		expect(buttons).toHaveLength(3);
		expect(wrapper.text()).toContain("Budi Santoso");
		expect(wrapper.text()).toContain("budiwijaya");

		wrapper.unmount();
	});

	it("collapses rapid keystrokes into the final query", async () => {
		vi.useFakeTimers();
		const { wrapper, input } = await mountCartWithCustomers();

		await input.setValue("b");
		await vi.advanceTimersByTime(100);
		await input.setValue("bu");
		await vi.advanceTimersByTime(100);
		await input.setValue("budi");
		await vi.advanceTimersByTime(100);

		// Still inside the debounce window of the last keystroke.
		expect(dropdown(wrapper).exists()).toBe(false);
		expect(resultButtons(wrapper)).toHaveLength(0);

		await vi.advanceTimersByTime(200);
		await flushPromises();

		expect(resultButtons(wrapper)).toHaveLength(3);

		wrapper.unmount();
	});

	it("still shows results when the field is not focused (term-only dropdown)", async () => {
		vi.useFakeTimers();
		const { wrapper, input } = await mountCartWithCustomers();

		await input.setValue("citra");
		await vi.advanceTimersByTime(300);
		await flushPromises();

		const buttons = resultButtons(wrapper);
		expect(buttons).toHaveLength(1);
		expect(wrapper.text()).toContain("Citra Lestari");

		wrapper.unmount();
	});

	it("clears the dropdown immediately when the field is emptied", async () => {
		vi.useFakeTimers();
		const { wrapper, input } = await mountCartWithCustomers();

		await input.setValue("budi");
		await vi.advanceTimersByTime(300);
		await flushPromises();
		expect(resultButtons(wrapper)).toHaveLength(3);

		// No waiting for the debounce: clearing is instant.
		await input.setValue("");
		await flushPromises();
		expect(resultButtons(wrapper)).toHaveLength(0);

		wrapper.unmount();
	});
});
