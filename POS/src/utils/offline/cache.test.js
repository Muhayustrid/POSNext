// PERF-03: customer sync used to pull the whole customer table in one
// `limit: 0` request (50k+ rows hung the tab). These pin the batched paging
// contract: 500-row pages, offset stepping, stop on the first short page,
// and the `modified_since` delta whenever the local cache has a last-sync
// marker (disabled rows returned by the delta must be purged, not upserted).
import { beforeEach, describe, expect, it, vi } from "vitest";

const dbMock = vi.hoisted(() => ({
	items: { count: vi.fn(async () => 0) },
	customers: {
		count: vi.fn(async () => 0),
		bulkDelete: vi.fn(async () => {}),
	},
}));

vi.mock("@/utils/offline/db", () => ({
	db: dbMock,
	getSetting: vi.fn(async (_key, fallback) => fallback),
	setSetting: vi.fn(async () => {}),
}));

vi.mock("@/utils/apiWrapper", () => ({ call: vi.fn() }));

vi.mock("@/utils/offline/offlineState", () => ({
	offlineState: {
		manualOffline: false,
		setManualOffline: vi.fn(),
		toggleManualOffline: vi.fn(),
		subscribe: vi.fn(),
	},
}));

import { call } from "@/utils/apiWrapper";
import { cacheCustomersFromServer, memory } from "@/utils/offline/cache";

const BATCH = 500;

const customer = (i, extra = {}) => ({
	name: `CUST-${String(i).padStart(5, "0")}`,
	customer_name: `Customer ${i}`,
	mobile_no: "",
	email_id: "",
	disabled: 0,
	...extra,
});

const page = (from, to) =>
	Array.from({ length: to - from }, (_, k) => customer(from + k));

beforeEach(() => {
	call.mockReset();
	dbMock.customers.bulkDelete.mockClear();
	memory.customers_last_sync = null;
});

describe("cacheCustomersFromServer pagination (PERF-03)", () => {
	it("fetches 800 customers as two 500-row pages and stops on the short page", async () => {
		call
			.mockResolvedValueOnce({ message: page(0, 500) })
			.mockResolvedValueOnce({ message: page(500, 800) });

		const result = await cacheCustomersFromServer("POS-1");

		expect(call).toHaveBeenCalledTimes(2);
		expect(call.mock.calls[0][0]).toBe("pos_next.api.customers.get_customers");
		expect(call.mock.calls[0][1]).toMatchObject({ pos_profile: "POS-1", start: 0, limit: BATCH });
		expect(call.mock.calls[1][1]).toMatchObject({ pos_profile: "POS-1", start: BATCH, limit: BATCH });

		// Order preserved: the caller upserts this straight into IndexedDB.
		expect(result.customers).toHaveLength(800);
		expect(result.customers[0].name).toBe("CUST-00000");
		expect(result.customers[799].name).toBe("CUST-00799");
	});

	it("never asks the server for an unbounded pull", async () => {
		call.mockResolvedValue({ message: page(0, 10) });

		await cacheCustomersFromServer("POS-1");

		expect(call).toHaveBeenCalledTimes(1);
		for (const [, params] of call.mock.calls) {
			expect(params.limit).not.toBe(0);
			expect(params.limit).toBeLessThanOrEqual(BATCH);
		}
	});

	it("does not send modified_since when the cache has no last-sync marker", async () => {
		call.mockResolvedValue({ message: page(0, 10) });

		await cacheCustomersFromServer("POS-1");

		expect(call.mock.calls[0][1]).not.toHaveProperty("modified_since");
	});
});

describe("cacheCustomersFromServer delta sync (PERF-03)", () => {
	it("sends modified_since from the last-sync marker and purges disabled rows", async () => {
		memory.customers_last_sync = 1727000000000;

		const deltaPage = [
			...page(0, 3),
			customer(900, { disabled: 1 }),
			customer(901, { disabled: 1 }),
		];
		call.mockResolvedValue({ message: deltaPage });

		const result = await cacheCustomersFromServer("POS-1");

		expect(call.mock.calls[0][1]).toMatchObject({
			modified_since: new Date(1727000000000).toISOString(),
			start: 0,
			limit: BATCH,
		});
		// Disabled rows must never be upserted into the offline cache...
		expect(result.customers.map((c) => c.name)).toEqual([
			"CUST-00000",
			"CUST-00001",
			"CUST-00002",
		]);
		// ...they must be purged from IndexedDB instead.
		expect(dbMock.customers.bulkDelete).toHaveBeenCalledWith([
			"CUST-00900",
			"CUST-00901",
		]);
	});
});
