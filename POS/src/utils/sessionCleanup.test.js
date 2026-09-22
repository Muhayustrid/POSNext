import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/utils/draftManager", () => ({
	clearAllDrafts: vi.fn().mockResolvedValue(undefined),
}));
vi.mock("@/utils/offline/offlineReceiptCache", () => ({
	clearAllOfflineReceiptPayloads: vi.fn(),
}));
vi.mock("@/utils/offline/sync", () => ({
	clearInvoiceHistoryCache: vi.fn().mockResolvedValue(true),
}));
vi.mock("@/utils/offline/db", () => ({
	db: {
		unpaid_invoices: { clear: vi.fn().mockResolvedValue(0) },
		customers: { clear: vi.fn().mockResolvedValue(0) },
		payment_methods: { clear: vi.fn().mockResolvedValue(0) },
	},
}));
vi.mock("@/stores/posCart", () => ({
	usePOSCartStore: () => ({ clearCart: vi.fn(), posOpeningShift: null, posProfile: null }),
}));
vi.mock("@/stores/posUI", () => ({
	usePOSUIStore: () => ({ resetAllDialogs: vi.fn() }),
}));
vi.mock("@/composables/useSessionLock", () => ({
	useSessionLock: () => ({ clearLock: vi.fn(), stopActivityTracking: vi.fn() }),
}));
vi.mock("@/composables/useShift", () => ({ shiftState: { value: {} } }));

import { clearAllDrafts } from "@/utils/draftManager";
import { db } from "@/utils/offline/db";
import { clearInvoiceHistoryCache } from "@/utils/offline/sync";
import { cleanupUserSession } from "@/utils/sessionCleanup";

// jsdom has no Cache Storage; stub the minimal surface sessionCleanup uses.
function stubCaches() {
	const deleted = [];
	const caches = {
		keys: vi
			.fn()
			.mockResolvedValue(["api-cache", "pos-page-cache", "product-images-cache", "workbox-precache-v2-app"]),
		delete: vi.fn((name) => {
			deleted.push(name);
			return Promise.resolve(true);
		}),
	};
	vi.stubGlobal("caches", caches);
	return { deleted, caches };
}

describe("cleanupUserSession SEC-17 cross-cashier cleanup", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		localStorage.clear();
	});

	afterEach(() => {
		vi.unstubAllGlobals();
	});

	it("deletes cashier runtime caches but keeps shared caches", async () => {
		const { deleted, caches } = stubCaches();

		await cleanupUserSession();

		expect(caches.keys).toHaveBeenCalledTimes(1);
		expect(deleted).toEqual(["api-cache", "pos-page-cache"]);
	});

	it("clears cross-cashier IndexedDB stores", async () => {
		stubCaches();

		await cleanupUserSession();

		expect(clearInvoiceHistoryCache).toHaveBeenCalledWith();
		expect(db.unpaid_invoices.clear).toHaveBeenCalledTimes(1);
		expect(db.customers.clear).toHaveBeenCalledTimes(1);
		expect(db.payment_methods.clear).toHaveBeenCalledTimes(1);
	});

	it("still clears localStorage keys and drafts (existing behavior)", async () => {
		localStorage.setItem("pos_shift_data", "x");
		stubCaches();

		await cleanupUserSession();

		expect(localStorage.getItem("pos_shift_data")).toBeNull();
		expect(clearAllDrafts).toHaveBeenCalledTimes(1);
	});

	it("survives a missing caches API (jsdom default, unstubbed)", async () => {
		await expect(cleanupUserSession()).resolves.toBeUndefined();
		expect(db.customers.clear).toHaveBeenCalledTimes(1);
	});
});
