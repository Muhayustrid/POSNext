import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

vi.hoisted(() => {
	globalThis.__ = (message, replacements = []) => {
		if (!Array.isArray(replacements) || !replacements.length) return message;
		let out = message;
		for (const [i, v] of replacements.entries())
			out = out.split(`{${i}}`).join(String(v));
		return out;
	};
});

vi.mock("@/utils/apiWrapper", () => ({ call: vi.fn(async () => ({ message: [] })) }));
vi.mock("@/utils/offline", () => ({ isOffline: () => false }));
const toastMock = {
	showError: vi.fn(),
	showWarning: vi.fn(),
	showSuccess: vi.fn(),
	showInfo: vi.fn(),
};
vi.mock("@/composables/useToast", () => ({
	useToast: () => toastMock,
}));
vi.mock("@/utils/offline/workerClient", () => ({
	offlineWorker: new Proxy(
		{},
		{
			get: (t, prop) => (prop in t ? t[prop] : vi.fn(async () => ({}))),
		}
	),
}));

vi.mock("@/utils/lowEndOptimizations", async (importOriginal) => {
	const actual = await importOriginal();
	return {
		...actual,
		createOptimizedClickHandler: (handler) => ({
			touchstart: () => {},
			touchmove: () => {},
			touchend: () => {},
			click: (e) => handler(e),
		}),
	};
});

vi.mock("frappe-ui", async () => {
	const { reactive } = await import("vue");
	const stub = { name: "FrappeUIStub", render: () => null };
	return {
		createResource: () =>
			reactive({ loading: false, data: null, error: null, reload: vi.fn(async () => {}) }),
		FeatherIcon: stub,
		Button: stub,
		Input: stub,
		TextInput: stub,
	};
});

import ItemsSelector from "./ItemsSelector.vue";
import { useItemSearchStore } from "@/stores/itemSearch";
import { usePOSSettingsStore } from "@/stores/posSettings";
import { call } from "@/utils/apiWrapper";

const VIEW_PREF_KEY = "pos_item_view_mode";

describe("ItemsSelector stock blocking", () => {
	beforeEach(() => {
		localStorage.clear();
		localStorage.setItem(VIEW_PREF_KEY, "grid");
		vi.mocked(call).mockReset();
		toastMock.showError.mockClear();
		toastMock.showWarning.mockClear();
	});

	async function mountSelectorWithItems(
		items,
		settingsOverride = {},
		viewMode = "grid",
		shortageData = null
	) {
		localStorage.setItem(VIEW_PREF_KEY, viewMode);
		vi.mocked(call).mockImplementation(async (url) => {
			if (url === "pos_next.api.pos_profile.get_pos_profile_data") {
				return {
					message: null,
					pos_profile: { item_groups: [] },
					item_groups_hierarchy: [],
				};
			}
			if (url === "pos_next.api.items.get_items") {
				return { message: items };
			}
			if (url === "pos_next.api.items.get_bundle_shortages") {
				if (shortageData instanceof Error) throw shortageData;
				return shortageData !== null ? shortageData : {};
			}
			return { message: [] };
		});

		const pinia = createPinia();
		setActivePinia(pinia);

		const settingsStore = usePOSSettingsStore();
		settingsStore.settings.enabled = 1;
		Object.assign(settingsStore.settings, settingsOverride);

		const wrapper = mount(ItemsSelector, {
			props: { posProfile: "Kasir 1", cartItems: [], currency: "IDR" },
			global: {
				plugins: [pinia],
				config: { globalProperties: { __: globalThis.__ } },
				stubs: { LazyImage: true, WarehouseAvailabilityDialog: true },
			},
		});

		await flushPromises();
		return { wrapper, settingsStore, itemStore: useItemSearchStore() };
	}

	it("renders out-of-stock items as blocked and refuses selection when toggle is ON", async () => {
		const items = [
			{
				item_code: "ROTI-01",
				item_name: "Roti Coklat",
				rate: 10000,
				uom: "Pcs",
				is_stock_item: 1,
				actual_qty: 0,
			},
			{
				item_code: "ROTI-02",
				item_name: "Roti Keju",
				rate: 12000,
				uom: "Pcs",
				is_stock_item: 1,
				actual_qty: 5,
			},
		];

		const { wrapper } = await mountSelectorWithItems(items, { block_out_of_stock_items: 1 });
		const cards = wrapper.findAll("div.grid.grid-cols-2 > div");
		expect(cards).toHaveLength(2);

		// First item (stock 0): blocked
		const outOfStockCard = cards[0];
		expect(outOfStockCard.attributes("aria-disabled")).toBe("true");
		expect(outOfStockCard.classes()).toContain("cursor-not-allowed");
		expect(outOfStockCard.text()).toContain("Out of stock");

		// Click on blocked item should NOT emit item-selected
		await outOfStockCard.trigger("click");
		expect(wrapper.emitted("item-selected")).toBeUndefined();

		// Second item (stock 5): selectable
		const inStockCard = cards[1];
		expect(inStockCard.attributes("aria-disabled")).toBeUndefined();
		expect(inStockCard.classes()).toContain("cursor-pointer");
		expect(inStockCard.text()).not.toContain("Out of stock");

		await inStockCard.trigger("click");
		expect(wrapper.emitted("item-selected")).toHaveLength(1);
		expect(wrapper.emitted("item-selected")[0][0].item_code).toBe("ROTI-02");
	});

	it("blocks zero-stock product bundles when toggle is ON", async () => {
		const items = [
			{
				item_code: "BUNDLE-01",
				item_name: "Paket Sarapan",
				rate: 25000,
				uom: "Pcs",
				is_stock_item: 0,
				is_bundle: true,
				actual_qty: 0,
			},
		];

		const { wrapper } = await mountSelectorWithItems(items, { block_out_of_stock_items: 1 });
		const cards = wrapper.findAll("div.grid.grid-cols-2 > div");
		expect(cards).toHaveLength(1);

		const bundleCard = cards[0];
		expect(bundleCard.attributes("aria-disabled")).toBe("true");
		expect(bundleCard.classes()).toContain("cursor-not-allowed");
		expect(bundleCard.text()).toContain("Out of stock");

		await bundleCard.trigger("click");
		expect(wrapper.emitted("item-selected")).toBeUndefined();
	});

	it("allows selecting zero-stock items when block_out_of_stock_items is OFF", async () => {
		const items = [
			{
				item_code: "ROTI-01",
				item_name: "Roti Coklat",
				rate: 10000,
				uom: "Pcs",
				is_stock_item: 1,
				actual_qty: 0,
			},
		];

		const { wrapper } = await mountSelectorWithItems(items, { block_out_of_stock_items: 0 });
		const cards = wrapper.findAll("div.grid.grid-cols-2 > div");
		expect(cards).toHaveLength(1);

		const card = cards[0];
		expect(card.attributes("aria-disabled")).toBeUndefined();
		expect(card.classes()).toContain("cursor-pointer");
		expect(card.text()).not.toContain("Out of stock");

		await card.trigger("click");
		expect(wrapper.emitted("item-selected")).toHaveLength(1);
		expect(wrapper.emitted("item-selected")[0][0].item_code).toBe("ROTI-01");
	});

	it("blocks zero-stock items even if allow_negative_stock is 1", async () => {
		const items = [
			{
				item_code: "ROTI-01",
				item_name: "Roti Coklat",
				rate: 10000,
				uom: "Pcs",
				is_stock_item: 1,
				actual_qty: 0,
			},
		];

		// Toggle ON wins over allow_negative_stock
		const { wrapper } = await mountSelectorWithItems(items, {
			block_out_of_stock_items: 1,
			allow_negative_stock: 1,
		});
		const cards = wrapper.findAll("div.grid.grid-cols-2 > div");
		const card = cards[0];

		expect(card.attributes("aria-disabled")).toBe("true");
		expect(card.text()).toContain("Out of stock");

		await card.trigger("click");
		expect(wrapper.emitted("item-selected")).toBeUndefined();
	});

	it("renders out-of-stock label and aria-disabled in list view", async () => {
		const items = [
			{
				item_code: "ROTI-01",
				item_name: "Roti Coklat",
				rate: 10000,
				uom: "Pcs",
				is_stock_item: 1,
				actual_qty: 0,
			},
		];

		const { wrapper } = await mountSelectorWithItems(
			items,
			{ block_out_of_stock_items: 1 },
			"list"
		);
		const row = wrapper.find("tbody tr[aria-disabled='true']");
		expect(row.exists()).toBe(true);
		expect(row.classes()).toContain("cursor-not-allowed");
		expect(row.text()).toContain("Out of stock");

		await row.trigger("click");
		expect(wrapper.emitted("item-selected")).toBeUndefined();
	});

	it("shows short component name on bundle pill when shortage data is present", async () => {
		const items = [
			{
				item_code: "BUNDLE-01",
				item_name: "Paket Sarapan",
				rate: 25000,
				uom: "Pcs",
				is_stock_item: 0,
				is_bundle: true,
				actual_qty: 0,
			},
		];

		const shortageData = {
			"BUNDLE-01": [
				{
					item_code: "COMP-01",
					item_name: "Keju Cheddar",
					available_qty: 0,
					required_qty: 1,
				},
			],
		};

		const { wrapper } = await mountSelectorWithItems(
			items,
			{ block_out_of_stock_items: 1 },
			"grid",
			shortageData
		);

		await new Promise((r) => setTimeout(r, 350));
		await flushPromises();

		const bundleCard = wrapper.findAll("div.grid.grid-cols-2 > div")[0];
		expect(bundleCard.text()).toContain("Short: Keju Cheddar");
		expect(bundleCard.text()).not.toContain("Out of stock");
	});

	it("shows +N on bundle pill when multiple components are short", async () => {
		const items = [
			{
				item_code: "BUNDLE-01",
				item_name: "Paket Sarapan",
				rate: 25000,
				uom: "Pcs",
				is_stock_item: 0,
				is_bundle: true,
				actual_qty: 0,
			},
		];

		const shortageData = {
			"BUNDLE-01": [
				{
					item_code: "COMP-01",
					item_name: "Keju Cheddar",
					available_qty: 0,
					required_qty: 1,
				},
				{
					item_code: "COMP-02",
					item_name: "Susu UHT",
					available_qty: 0.5,
					required_qty: 2,
				},
				{
					item_code: "COMP-03",
					item_name: "Tepung",
					available_qty: 0,
					required_qty: 1,
				},
			],
		};

		const { wrapper } = await mountSelectorWithItems(
			items,
			{ block_out_of_stock_items: 1 },
			"grid",
			shortageData
		);

		await new Promise((r) => setTimeout(r, 350));
		await flushPromises();

		const bundleCard = wrapper.findAll("div.grid.grid-cols-2 > div")[0];
		expect(bundleCard.text()).toContain("Short: Keju Cheddar +2");
	});

	it("falls back to Out of stock when shortage data is absent or call errors", async () => {
		const items = [
			{
				item_code: "BUNDLE-01",
				item_name: "Paket Sarapan",
				rate: 25000,
				uom: "Pcs",
				is_stock_item: 0,
				is_bundle: true,
				actual_qty: 0,
			},
		];

		const { wrapper } = await mountSelectorWithItems(
			items,
			{ block_out_of_stock_items: 1 },
			"grid",
			new Error("Network error")
		);

		await new Promise((r) => setTimeout(r, 350));
		await flushPromises();

		const bundleCard = wrapper.findAll("div.grid.grid-cols-2 > div")[0];
		expect(bundleCard.text()).toContain("Out of stock");
	});

	it("shows component shortage pill in list view", async () => {
		const items = [
			{
				item_code: "BUNDLE-01",
				item_name: "Paket Sarapan",
				rate: 25000,
				uom: "Pcs",
				is_stock_item: 0,
				is_bundle: true,
				actual_qty: 0,
			},
		];

		const shortageData = {
			"BUNDLE-01": [
				{
					item_code: "COMP-01",
					item_name: "Keju Cheddar",
					available_qty: 0,
					required_qty: 1,
				},
			],
		};

		const { wrapper } = await mountSelectorWithItems(
			items,
			{ block_out_of_stock_items: 1 },
			"list",
			shortageData
		);

		await new Promise((r) => setTimeout(r, 350));
		await flushPromises();

		const row = wrapper.find("tbody tr[aria-disabled='true']");
		expect(row.text()).toContain("Short: Keju Cheddar");
		expect(row.text()).not.toContain("Out of stock");
	});

	it("displays toast with detailed shortages when blocked bundle is clicked", async () => {
		const items = [
			{
				item_code: "BUNDLE-01",
				item_name: "Paket Sarapan",
				rate: 25000,
				uom: "Pcs",
				is_stock_item: 0,
				is_bundle: true,
				actual_qty: 0,
			},
		];

		const shortageData = {
			"BUNDLE-01": [
				{
					item_code: "COMP-01",
					item_name: "Keju Cheddar",
					available_qty: 0,
					required_qty: 1,
				},
				{
					item_code: "COMP-02",
					item_name: "Susu UHT",
					available_qty: 0.5,
					required_qty: 2,
				},
			],
		};

		const { wrapper } = await mountSelectorWithItems(
			items,
			{ block_out_of_stock_items: 1 },
			"grid",
			shortageData
		);

		await new Promise((r) => setTimeout(r, 350));
		await flushPromises();

		const bundleCard = wrapper.findAll("div.grid.grid-cols-2 > div")[0];
		await bundleCard.trigger("click");

		expect(toastMock.showError).toHaveBeenCalledTimes(1);
		expect(toastMock.showError).toHaveBeenCalledWith(
			"Paket Sarapan is out of stock: Keju Cheddar 0 left, needs 1 per item, Susu UHT 0.5 left, needs 2 per item"
		);
	});
});
