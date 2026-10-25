import { describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
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
			reactive({
				loading: false,
				data: null,
				error: null,
				reload: vi.fn(async () => {}),
			}),
	};
});

import { usePOSCartStore } from "@/stores/posCart";
import InvoiceCart from "./InvoiceCart.vue";

function mountCart(propsOverrides = {}) {
	const pinia = createPinia();
	setActivePinia(pinia);
	const defaultProps = {
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
	};

	const wrapper = mount(InvoiceCart, {
		props: defaultProps,
		global: {
			plugins: [pinia],
			config: { globalProperties: { __: globalThis.__ } },
		},
	});

	return { wrapper, cartStore: usePOSCartStore() };
}

describe("InvoiceCart UI Refinement", () => {
	describe("Hidden Customer Selection / Card Block", () => {
		it("hides customer card, avatar, and edit/create/remove actions when customer is set", () => {
			const { wrapper } = mountCart({
				customer: {
					name: "CUST-001",
					customer_name: "Walk-in Customer",
					mobile_no: "08123456789",
				},
			});

			// Customer name/info card should NOT be visible in cart header
			expect(wrapper.text()).not.toContain("Walk-in Customer");
			expect(wrapper.text()).not.toContain("08123456789");
			expect(wrapper.find('button[title="Edit customer details"]').exists()).toBe(false);
			expect(wrapper.find('button[title="Remove customer"]').exists()).toBe(false);
			expect(wrapper.find('button[title="Create new customer"]').exists()).toBe(false);

			// Optional buyer-name input MUST remain visible
			const buyerNameInput = wrapper.find("#cart-buyer-name");
			expect(buyerNameInput.exists()).toBe(true);
			wrapper.unmount();
		});

		it("hides customer search input and quick-create button when no customer is set", () => {
			const { wrapper } = mountCart({ customer: null });

			expect(wrapper.find("#cart-customer-search").exists()).toBe(false);
			expect(wrapper.find('button[aria-label="Create new customer"]').exists()).toBe(false);

			// Optional buyer-name input MUST remain visible
			const buyerNameInput = wrapper.find("#cart-buyer-name");
			expect(buyerNameInput.exists()).toBe(true);
			wrapper.unmount();
		});

		it("removes customer-create empty-cart quick action but preserves other 6 quick actions", () => {
			const { wrapper } = mountCart({ items: [] });

			// Create customer quick action must be absent
			expect(wrapper.find('button[title="Create new customer"]').exists()).toBe(false);

			// The other quick actions must exist
			expect(wrapper.find('button[title="View current shift details"]').exists()).toBe(true);
			expect(wrapper.find('button[title="View draft invoices"]').exists()).toBe(true);
			expect(wrapper.find('button[title="View invoice history"]').exists()).toBe(true);
			expect(wrapper.find('button[title="Process return invoice"]').exists()).toBe(true);
			expect(wrapper.find('button[title="Close current shift"]').exists()).toBe(true);
			expect(wrapper.find('button[title="View shift history"]').exists()).toBe(true);

			wrapper.unmount();
		});
	});

	describe("Tax Row Visibility", () => {
		const sampleItem = {
			item_code: "ITEM-1",
			item_name: "Roti Coklat",
			quantity: 2,
			rate: 10000,
			uom: "Pcs",
		};

		it("hides tax row when no tax is configured and taxAmount is 0", async () => {
			const { wrapper, cartStore } = mountCart({
				items: [sampleItem],
				subtotal: 20000,
				taxAmount: 0,
				grandTotal: 20000,
			});
			// Store has empty taxRules (no tax configured)
			if (cartStore.taxRules) cartStore.taxRules = [];
			await flushPromises();

			// Tax header/amount should not appear
			const text = wrapper.find(".border-t").text();
			expect(text).not.toContain("Tax");

			// Grid should use 2 columns instead of 3
			expect(wrapper.find(".grid-cols-2").exists()).toBe(true);
			expect(wrapper.find(".grid-cols-3").exists()).toBe(false);
			wrapper.unmount();
		});

		it("shows tax row when tax is configured with 0% rate even though taxAmount is 0", async () => {
			const { wrapper, cartStore } = mountCart({
				items: [sampleItem],
				subtotal: 20000,
				taxAmount: 0,
				grandTotal: 20000,
				taxRules: [{ rate: 0, account_head: "Tax 0% - R" }],
			});
			if (cartStore.taxRules) {
				cartStore.taxRules = [{ rate: 0, account_head: "Tax 0% - R" }];
			}
			await flushPromises();

			const text = wrapper.find(".border-t").text();
			expect(text).toContain("Tax");
			expect(wrapper.find(".grid-cols-3").exists()).toBe(true);
			wrapper.unmount();
		});

		it("shows tax row when tax is configured with positive rate", async () => {
			const { wrapper, cartStore } = mountCart({
				items: [sampleItem],
				subtotal: 20000,
				taxAmount: 2200,
				grandTotal: 22200,
				taxRules: [{ rate: 11, account_head: "PPN 11% - R" }],
			});
			if (cartStore.taxRules) {
				cartStore.taxRules = [{ rate: 11, account_head: "PPN 11% - R" }];
			}
			await flushPromises();

			const text = wrapper.find(".border-t").text();
			expect(text).toContain("Tax");
			expect(wrapper.find(".grid-cols-3").exists()).toBe(true);
			wrapper.unmount();
		});

		it("shows tax row as fallback when taxAmount is nonzero even if taxRules is not loaded", async () => {
			const { wrapper, cartStore } = mountCart({
				items: [sampleItem],
				subtotal: 20000,
				taxAmount: 1500,
				grandTotal: 21500,
			});
			if (cartStore.taxRules) cartStore.taxRules = [];
			await flushPromises();

			const text = wrapper.find(".border-t").text();
			expect(text).toContain("Tax");
			expect(wrapper.find(".grid-cols-3").exists()).toBe(true);
			wrapper.unmount();
		});
	});

	describe("Touch Targets & Tabular Numerals", () => {
		const sampleItem = {
			item_code: "ITEM-1",
			item_name: "Roti Coklat",
			quantity: 2,
			rate: 10000,
			uom: "Pcs",
		};

		it("ensures tabular-nums on all monetary and quantity figures", () => {
			const { wrapper } = mountCart({
				items: [sampleItem],
				subtotal: 20000,
				taxAmount: 0,
				discountAmount: 1000,
				grandTotal: 19000,
			});

			// Line rate and line total
			const itemRow = wrapper.find(".border-b");
			expect(wrapper.html()).toContain("tabular-nums");

			// Grand total
			const grandTotalEl = wrapper.find(".font-extrabold.text-blue-600");
			expect(grandTotalEl.classes()).toContain("tabular-nums");

			wrapper.unmount();
		});

		it("provides reachable Checkout button with min-h-[44px]", () => {
			const { wrapper } = mountCart({
				items: [sampleItem],
				subtotal: 20000,
				taxAmount: 0,
				grandTotal: 20000,
			});

			const checkoutBtn = wrapper.find('button[aria-label="Proceed to payment"]');
			expect(checkoutBtn.classes()).toContain("min-h-[44px]");
			wrapper.unmount();
		});
	});
});
