// PERF-12: during search, totalPages returned 1 so the existing pagination
// controls were hidden and EVERY search hit (up to searchBatchSize = 500)
// rendered at once. Search results must now be sliced client-side into pages
// of 50 (SEARCH_PAGE_SIZE), reusing the existing pagination controls, while
// browse-mode server pagination (fetchPage) stays untouched.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

vi.hoisted(() => {
	// The app installs __() as a global property; some modules call it at
	// module-evaluation time, so this must be hoisted above the imports.
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

vi.mock("@/utils/offline/workerClient", () => ({
	// catch-all: every worker method becomes a resolved async no-op
	offlineWorker: new Proxy(
		{},
		{
			get: (t, prop) => (prop in t ? t[prop] : vi.fn(async () => ({}))),
		}
	),
}));

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
import { call } from "@/utils/apiWrapper";

function makeItems(count, prefix) {
	return Array.from({ length: count }, (_, i) => ({
		item_code: `${prefix}-${String(i + 1).padStart(3, "0")}`,
		item_name: `${prefix} Item ${i + 1}`,
		rate: 1000,
		uom: "Nos",
	}));
}

// Pin grid view via the localStorage preference so the item-count auto-switch
// (and the settings default) cannot flip the view under test.
const VIEW_PREF_KEY = "pos_item_view_mode";

async function mountSelector() {
	localStorage.setItem(VIEW_PREF_KEY, "grid");
	const pinia = createPinia();
	setActivePinia(pinia);
	const wrapper = mount(ItemsSelector, {
		props: { posProfile: "Kasir 1", cartItems: [], currency: "IDR" },
		global: {
			plugins: [pinia],
			config: { globalProperties: { __: globalThis.__ } },
			stubs: { LazyImage: true, WarehouseAvailabilityDialog: true },
		},
	});
	await flushPromises();
	return { wrapper, store: useItemSearchStore() };
}

// Search-mode item cards: children of the responsive grid container.
function itemCards(wrapper) {
	return wrapper.findAll("div.grid.grid-cols-2 > div");
}

function cardNames(wrapper) {
	return itemCards(wrapper).map((c) => c.find("h3").text());
}

describe("ItemsSelector search pagination (PERF-12)", () => {
	beforeEach(() => {
		localStorage.clear();
	});

	it("renders only the first 50 search results, with page controls visible", async () => {
		const { wrapper, store } = await mountSelector();
		store.searchTerm = "kopi";
		store.searchResults = makeItems(120, "AAA");
		await flushPromises();

		expect(itemCards(wrapper)).toHaveLength(50);
		expect(cardNames(wrapper)[0]).toContain("AAA Item 1");
		expect(wrapper.find('button[aria-label="Go to page 2"]').exists()).toBe(true);
		// Full result count stays visible; the slice is display-only.
		expect(wrapper.text()).toContain("120 items found");
		wrapper.unmount();
	});

	it("page 2 shows the next 50 results without calling fetchPage", async () => {
		const { wrapper, store } = await mountSelector();
		store.searchTerm = "kopi";
		store.searchResults = makeItems(120, "AAA");
		await flushPromises();
		const fetchPageSpy = vi.spyOn(store, "fetchPage");

		await wrapper.find('button[aria-label="Go to page 2"]').trigger("click");
		await flushPromises();

		expect(fetchPageSpy).not.toHaveBeenCalled();
		const names = cardNames(wrapper);
		expect(names).toHaveLength(50);
		expect(names[0]).toContain("AAA Item 51");
		expect(names[49]).toContain("AAA Item 100");
		wrapper.unmount();
	});

	it("last page shows only the remaining results", async () => {
		const { wrapper, store } = await mountSelector();
		store.searchTerm = "kopi";
		store.searchResults = makeItems(120, "AAA");
		await flushPromises();

		await wrapper.find('button[aria-label="Go to page 3"]').trigger("click");
		await flushPromises();

		const names = cardNames(wrapper);
		expect(names).toHaveLength(20);
		expect(names[0]).toContain("AAA Item 101");
		wrapper.unmount();
	});

	it("resets to page 1 when the search term changes", async () => {
		const { wrapper, store } = await mountSelector();
		store.searchTerm = "kopi";
		store.searchResults = makeItems(120, "AAA");
		await flushPromises();

		await wrapper.find('button[aria-label="Go to page 2"]').trigger("click");
		await flushPromises();
		expect(cardNames(wrapper)[0]).toContain("AAA Item 51");

		store.searchTerm = "gula";
		store.searchResults = makeItems(120, "BBB");
		await flushPromises();

		expect(cardNames(wrapper)[0]).toContain("BBB Item 1");
		expect(wrapper.find('button[aria-label="Go to page 2"]').exists()).toBe(true);
		wrapper.unmount();
	});
});

// FIX C: after a reload the catalog briefly showed EVERY item across POS
// Profiles while get_pos_profile_data was still in flight. The grid must stay
// held (spinner, no cards) until the profile data — including a profile with
// zero item-group filters — has loaded.
describe("ItemsSelector profile-ready gating (FIX C)", () => {
	beforeEach(() => {
		localStorage.clear();
		localStorage.setItem(VIEW_PREF_KEY, "grid");
		vi.mocked(call).mockReset();
		vi.mocked(call).mockImplementation(async () => ({ message: [] }));
	});

	async function mountWithProfile(profileResponse, itemResponse) {
		vi.mocked(call).mockImplementation(async (url) => {
			if (url === "pos_next.api.pos_profile.get_pos_profile_data") {
				return profileResponse;
			}
			if (url === "pos_next.api.items.get_items") {
				return { message: itemResponse };
			}
			return { message: [] };
		});
		const pinia = createPinia();
		setActivePinia(pinia);
		const wrapper = mount(ItemsSelector, {
			props: { posProfile: "Kasir 1", cartItems: [], currency: "IDR" },
			global: {
				plugins: [pinia],
				config: { globalProperties: { __: globalThis.__ } },
				stubs: { LazyImage: true, WarehouseAvailabilityDialog: true },
			},
		});
		await flushPromises();
		return { wrapper, store: useItemSearchStore() };
	}

	const beverage = (code, name) => ({
		item_code: code,
		item_name: name,
		item_group: "Beverages",
		rate: 1000,
		uom: "Nos",
	});

	it("renders no items while the profile data is still loading", async () => {
		// get_pos_profile_data never resolves (slow network simulation)
		vi.mocked(call).mockImplementation(
			async (url) =>
				url === "pos_next.api.pos_profile.get_pos_profile_data"
					? new Promise(() => {})
					: { message: [] }
		);
		const pinia = createPinia();
		setActivePinia(pinia);
		const wrapper = mount(ItemsSelector, {
			props: { posProfile: "Kasir 1", cartItems: [], currency: "IDR" },
			global: {
				plugins: [pinia],
				config: { globalProperties: { __: globalThis.__ } },
				stubs: { LazyImage: true, WarehouseAvailabilityDialog: true },
			},
		});
		await flushPromises();
		const store = useItemSearchStore();
		expect(store.profileLoaded).toBe(false);
		expect(store.loading).toBe(true);

		// Even with items forced into the store (stale cache scenario), the
		// catalog must not render them unfiltered.
		store.allItems = makeItems(5, "XXX");
		await flushPromises();

		expect(wrapper.find("div.grid.grid-cols-2").exists()).toBe(false);
		expect(itemCards(wrapper)).toHaveLength(0);
		expect(wrapper.text()).toContain("Loading items...");
		wrapper.unmount();
	});

	it("renders profile-filtered items once the profile has loaded", async () => {
		const { wrapper, store } = await mountWithProfile(
			{
				message: null,
				pos_profile: { item_groups: [{ item_group: "Beverages" }] },
				item_groups_hierarchy: [{ item_group: "Beverages" }],
			},
			[
				beverage("BEV-1", "Kopi Susu"),
				beverage("BEV-2", "Es Teh"),
				{ item_code: "OTH-1", item_name: "Other Item", item_group: "Other", rate: 1000, uom: "Nos" },
			]
		);

		expect(store.profileLoaded).toBe(true);
		expect(wrapper.find("div.grid.grid-cols-2").exists()).toBe(true);
		const names = cardNames(wrapper);
		expect(names).toHaveLength(2); // off-profile "Other Item" is filtered out
		expect(names.join(", ")).toContain("Kopi Susu");
		wrapper.unmount();
	});

	it("treats a profile with zero item groups as loaded, not pending", async () => {
		const { wrapper, store } = await mountWithProfile(
			{
				message: null,
				pos_profile: { item_groups: [] },
				item_groups_hierarchy: [],
			},
			[beverage("BEV-1", "Kopi Susu"), beverage("BEV-2", "Es Teh")]
		);

		expect(store.profileLoaded).toBe(true);
		expect(wrapper.find("div.grid.grid-cols-2").exists()).toBe(true);
		expect(cardNames(wrapper)).toHaveLength(2);
		wrapper.unmount();
	});
});
