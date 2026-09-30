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
