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
	const { defineComponent, reactive } = await import("vue");
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
		template: `<div><slot name="body" /><slot name="body-content" /><slot name="actions" /></div>`,
	});
	const stub = defineComponent({ name: "FrappeUIStub", render: () => null });
	return {
		Button,
		Dialog,
		FeatherIcon: stub,
		Input: stub,
		TextInput: stub,
		createResource: () =>
			reactive({
				loading: false,
				data: null,
				error: null,
				reload: vi.fn(async () => {}),
			}),
	};
});

vi.mock("reka-ui", () => ({
	DialogTitle: { name: "DialogTitle", template: "<h2><slot /></h2>" },
}));

import InvoiceCart from "./InvoiceCart.vue";
import { useCustomerSearchStore } from "@/stores/customerSearch";

function mountCart(propsOverrides = {}) {
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
			...propsOverrides,
		},
		global: {
			plugins: [createPinia()],
			config: { globalProperties: { __: globalThis.__ } },
		},
	});
}

describe("customer search in InvoiceCart (Customer selection hidden per profile default)", () => {
	it("does not render customer search input or quick-add customer button", async () => {
		const wrapper = mountCart({ customer: null });
		await flushPromises();

		expect(wrapper.find("#cart-customer-search").exists()).toBe(false);
		expect(wrapper.find('button[aria-label="Create new customer"]').exists()).toBe(false);
		wrapper.unmount();
	});

	it("preserves the optional buyer name input and binds to buyerName", async () => {
		const wrapper = mountCart();
		await flushPromises();

		const buyerNameInput = wrapper.find("#cart-buyer-name");
		expect(buyerNameInput.exists()).toBe(true);

		await buyerNameInput.setValue("Budi Walkin");
		expect(buyerNameInput.element.value).toBe("Budi Walkin");

		wrapper.unmount();
	});

	it("does not render customer card or customer action buttons when customer prop is provided", async () => {
		const wrapper = mountCart({
			customer: {
				name: "CUST-001",
				customer_name: "Walk-in Customer",
				mobile_no: "08123456789",
			},
		});
		await flushPromises();

		expect(wrapper.text()).not.toContain("Walk-in Customer");
		expect(wrapper.find('button[title="Edit customer details"]').exists()).toBe(false);
		expect(wrapper.find('button[title="Remove customer"]').exists()).toBe(false);

		wrapper.unmount();
	});

	it("retains helper functions in component instance to avoid breaking external callers", () => {
		const wrapper = mountCart();
		expect(typeof wrapper.vm.handleSearchInput).toBe("function");
		expect(typeof wrapper.vm.selectCustomer).toBe("function");
		expect(typeof wrapper.vm.createNewCustomer).toBe("function");
		wrapper.unmount();
	});
});
