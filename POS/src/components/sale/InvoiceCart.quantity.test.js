// FIX H3: when the store rejects a typed quantity (stock validation toasts and
// keeps the committed quantity), the input field kept showing the rejected
// number because the :value binding never rewrites the DOM when the bound
// value didn't change. The input must snap back to the committed quantity.
import { describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import { defineComponent, reactive } from "vue";

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
	const stub = defineComponent({ name: "FrappeUIStub", render: () => null });
	return {
		FeatherIcon: stub,
		Button: stub,
		Input: stub,
		TextInput: stub,
		createResource: () =>
			reactive({ loading: false, data: null, error: null, reload: vi.fn(async () => {}) }),
	};
});

import InvoiceCart from "./InvoiceCart.vue";

const qtyInput = (wrapper) => wrapper.find('input[aria-label="Quantity"]');

// Parent mimics POSSale: @update-quantity -> cartStore.updateItemQuantity,
// which enforces stock and REJECTS quantities above MAX_QTY (only toasts —
// the committed item.quantity stays unchanged).
function mountCartWithStockLimit(maxQty) {
	const items = reactive([
		{
			item_code: "ITEM-1",
			item_name: "Barang Satu",
			quantity: 3,
			rate: 1000,
			uom: "Nos",
		},
	]);
	const emitted = [];
	const Host = defineComponent({
		components: { InvoiceCart },
		setup() {
			const updateQuantity = (itemCode, qty, uom) => {
				emitted.push({ itemCode, qty, uom });
				const item = items.find((i) => i.item_code === itemCode);
				if (item && qty <= maxQty) item.quantity = qty; // else: stock reject, no commit
			};
			return { items, updateQuantity };
		},
		template: `
			<InvoiceCart
				:items="items"
				:customer="null"
				:subtotal="0"
				:tax-amount="0"
				:discount-amount="0"
				:grand-total="0"
				pos-profile="Kasir 1"
				currency="IDR"
				:applied-offers="[]"
				:warehouses="[]"
				@update-quantity="updateQuantity"
			/>
		`,
	});
	const wrapper = mount(Host, {
		global: {
			plugins: [createPinia()],
			config: { globalProperties: { __: globalThis.__ } },
		},
	});
	return { wrapper, items, emitted };
}

describe("quantity input snap-back on stock rejection in InvoiceCart (FIX H3)", () => {
	it("snaps the input back to the committed quantity when the store rejects", async () => {
		const { wrapper, items, emitted } = mountCartWithStockLimit(10);

		expect(qtyInput(wrapper).element.value).toBe("3");

		await qtyInput(wrapper).setValue("9999");

		// Store was asked, refused, committed quantity stays 3 — and the field
		// must not keep showing 9999.
		expect(emitted).toHaveLength(1);
		expect(items[0].quantity).toBe(3);
		expect(qtyInput(wrapper).element.value).toBe("3");
		wrapper.unmount();
	});

	it("keeps the typed value when the store accepts it", async () => {
		const { wrapper, items } = mountCartWithStockLimit(10);

		await qtyInput(wrapper).setValue("5");

		expect(items[0].quantity).toBe(5);
		expect(qtyInput(wrapper).element.value).toBe("5");
		wrapper.unmount();
	});

	it("snaps a cleared field back to the committed quantity on blur", async () => {
		const { wrapper, items } = mountCartWithStockLimit(10);

		await qtyInput(wrapper).setValue("");
		expect(qtyInput(wrapper).element.value).toBe("");

		await qtyInput(wrapper).trigger("blur");

		expect(items[0].quantity).toBe(3);
		expect(qtyInput(wrapper).element.value).toBe("3");
		wrapper.unmount();
	});
});
