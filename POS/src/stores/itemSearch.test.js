// FIX C: after a reload, filteredItems fell through to the unfiltered branch
// while get_pos_profile_data was still in flight (profileItemGroups still []),
// so the catalog flashed every item across POS Profiles. An explicit
// profileLoaded flag now gates the catalog: not ready = empty, never unfiltered.
//
// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

vi.hoisted(() => {
	globalThis.__ = (message) => message;
});

vi.mock("@/utils/apiWrapper", () => ({ call: vi.fn(async () => ({ message: [] })) }));

vi.mock("@/utils/offline", () => ({ isOffline: () => false }));

vi.mock("@/utils/offline/workerClient", () => ({
	offlineWorker: new Proxy(
		{},
		{
			get: (t, prop) => (prop in t ? t[prop] : vi.fn(async () => ({}))),
		}
	),
}));

vi.mock("frappe-ui", async () => {
	const { reactive } = await import("vue");
	return {
		createResource: () =>
			reactive({ loading: false, data: null, error: null, reload: vi.fn(async () => {}) }),
	};
});

import { call } from "@/utils/apiWrapper";
import { useItemSearchStore } from "./itemSearch";
import { usePOSPackagesStore } from "./posPackages";

const PROFILE_URL = "pos_next.api.pos_profile.get_pos_profile_data";
const ITEMS_URL = "pos_next.api.items.get_items";

function makeItem(code, name, group) {
	return { item_code: code, item_name: name, item_group: group, rate: 1000, uom: "Nos" };
}

describe("itemSearch profileLoaded gating (FIX C)", () => {
	beforeEach(() => {
		setActivePinia(createPinia());
		sessionStorage.clear();
		vi.mocked(call).mockReset();
		vi.mocked(call).mockImplementation(async () => ({ message: [] }));
	});

	it("returns empty filteredItems while the profile data is in flight", async () => {
		vi.mocked(call).mockImplementation(
			async (url) => (url === PROFILE_URL ? new Promise(() => {}) : { message: [] })
		);
		const store = useItemSearchStore();
		store.allItems = [makeItem("OTH-1", "Other Profile Item", "Other")];

		store.setPosProfile("Kasir 1"); // intentionally not awaited — hangs

		expect(store.profileLoaded).toBe(false);
		expect(store.loading).toBe(true);
		// The core regression: unfiltered items must never leak out.
		expect(store.filteredItems).toEqual([]);
	});

	it("skips loadAllItems entirely while the profile is not loaded", async () => {
		const store = useItemSearchStore();

		await store.loadAllItems("Kasir 1");

		expect(store.loading).toBe(false);
		expect(vi.mocked(call)).not.toHaveBeenCalled();
		expect(store.allItems).toEqual([]);
	});

	it("loads and filters items once the profile is ready", async () => {
		vi.mocked(call).mockImplementation(async (url) => {
			if (url === PROFILE_URL) {
				return {
					message: null,
					pos_profile: { item_groups: [{ item_group: "Beverages" }] },
					item_groups_hierarchy: [{ item_group: "Beverages" }],
				};
			}
			if (url === ITEMS_URL) {
				return {
					message: [
						makeItem("BEV-1", "Kopi Susu", "Beverages"),
						makeItem("BEV-2", "Es Teh", "Beverages"),
						makeItem("OTH-1", "Other Item", "Other"),
					],
				};
			}
			return { message: [] };
		});

		const store = useItemSearchStore();
		await store.setPosProfile("Kasir 1");
		await flushPromises();

		expect(store.profileLoaded).toBe(true);
		expect(store.loading).toBe(false);
		const names = store.filteredItems.map((i) => i.item_name);
		expect(names).toContain("Kopi Susu");
		expect(names).toContain("Es Teh");
		expect(names).not.toContain("Other Item");
	});

	it("treats a profile with zero item groups as loaded (legit unfiltered profile)", async () => {
		vi.mocked(call).mockImplementation(async (url) => {
			if (url === PROFILE_URL) {
				return {
					message: null,
					pos_profile: { item_groups: [] },
					item_groups_hierarchy: [],
				};
			}
			if (url === ITEMS_URL) {
				return {
					message: [makeItem("BEV-1", "Kopi Susu", "Beverages"), makeItem("BEV-2", "Es Teh", "Beverages")],
				};
			}
			return { message: [] };
		});

		const store = useItemSearchStore();
		await store.setPosProfile("Kasir 1");
		await flushPromises();

		// 0 groups after a successful load is a ready profile, not "pending".
		expect(store.profileLoaded).toBe(true);
		expect(store.filteredItems).toHaveLength(2);
	});

	it("resets safely when the profile fetch fails without a session cache", async () => {
		vi.mocked(call).mockImplementation(async (url) => {
			if (url === PROFILE_URL) throw new Error("network down");
			return { message: [] };
		});

		const store = useItemSearchStore();
		await store.setPosProfile("Kasir 1");

		// Fail open (no permanent loading hold), catalog stays empty because
		// no items were loaded.
		expect(store.profileLoaded).toBe(true);
		expect(store.loading).toBe(false);
		expect(store.filteredItems).toEqual([]);
	});
});

describe("itemSearch package parent handling", () => {
	beforeEach(() => {
		setActivePinia(createPinia());
		sessionStorage.clear();
		vi.mocked(call).mockReset();
		vi.mocked(call).mockImplementation(async () => ({ message: [] }));
	});

	// Unfiltered profile (zero item groups) so filteredItems passes every
	// catalog row through and only the package-parent filter decides.
	async function bootStore(items) {
		vi.mocked(call).mockImplementation(async (url) => {
			if (url === PROFILE_URL) {
				return { message: null, pos_profile: { item_groups: [] }, item_groups_hierarchy: [] };
			}
			if (url === ITEMS_URL) return { message: items };
			return { message: [] };
		});
		const store = useItemSearchStore();
		await store.setPosProfile("Kasir 1");
		await flushPromises();
		return store;
	}

	function parentItem() {
		return {
			item_code: "PKG-1",
			item_name: "Paket 1",
			item_group: "Beverages",
			rate: 50000,
			uom: "Nos",
			is_package_parent: true,
		};
	}

	it("hides a package parent whose package is not purchasable on the profile", async () => {
		const store = await bootStore([
			parentItem(),
			makeItem("ROTI-1", "Ropi Coklat", "Beverages"),
		]);

		const codes = store.filteredItems.map((i) => i.item_code);
		expect(codes).toEqual(["ROTI-1"]);
	});

	it("keeps the parent visible once its package is eligible", async () => {
		const store = await bootStore([
			parentItem(),
			makeItem("ROTI-1", "Ropi Coklat", "Beverages"),
		]);
		const packages = usePOSPackagesStore();
		packages.setPackages([{ name: "PKG-DOC-1", parent_item: "PKG-1" }]);
		packages.fetchedProfile = "Kasir 1";

		const codes = store.filteredItems.map((i) => i.item_code);
		expect(codes).toContain("PKG-1");
		expect(codes).toContain("ROTI-1");
	});

		it("re-evaluates an already-rendered catalog when packages load (cache key)", async () => {
		// Packages arrive AFTER the items (they load in parallel). The first
		// render hides the parent; the eligible set landing must un-hide it
		// without any item reload — this fails if filteredItemsCache keeps
		// serving the pre-packages list.
		const store = await bootStore([parentItem()]);
		expect(store.filteredItems.map((i) => i.item_code)).toEqual([]);

		const packages = usePOSPackagesStore();
		packages.setPackages([{ name: "PKG-DOC-1", parent_item: "PKG-1" }]);
		packages.fetchedProfile = "Kasir 1";

		expect(store.filteredItems.map((i) => i.item_code)).toEqual(["PKG-1"]);
	});

	it("appendAllItems dedupes re-delivered rows by item_code", () => {
		const store = useItemSearchStore();
		store.allItems = [makeItem("ROTI-1", "Ropi Coklat", "Beverages")];

		// Pagination drift redelivers ROTI-1 alongside a genuinely new row.
		store.appendAllItems([
			makeItem("ROTI-1", "Ropi Coklat", "Beverages"),
			makeItem("TEH-1", "Es Teh", "Beverages"),
		]);

		expect(store.allItems.map((i) => i.item_code)).toEqual(["ROTI-1", "TEH-1"]);
	});
});
