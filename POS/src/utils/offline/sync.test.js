// COR-FE-04: sync_failed invoices must not be retried forever by auto-sync.
// Covers: exclusion from the auto-sync batch (without blocking later entries),
// exponential backoff between transient retries, manual retry reset, and the
// list/count split that keeps failed entries visible in OfflineInvoicesDialog.
import { beforeEach, describe, expect, it, vi } from "vitest";

// In-memory invoice_queue backing the mocked db module. Declared in
// vi.hoisted so the hoisted vi.mock factory below can reference it.
const store = vi.hoisted(() => {
	const rows = [];
	return {
		rows,
		reset: () => {
			rows.length = 0;
		},
		filter(fn) {
			const matched = () => rows.filter(fn);
			return {
				toArray: async () => matched(),
				count: async () => matched().length,
				delete: async () => {
					const doomed = matched();
					for (const row of doomed) {
						const i = rows.indexOf(row);
						if (i >= 0) rows.splice(i, 1);
					}
					return doomed.length;
				},
			};
		},
	};
});

vi.mock("@/utils/offline/db", () => ({
	db: {
		invoice_queue: {
			add: vi.fn(async (record) => {
				const id = store.rows.length + 1;
				store.rows.push({ id, ...record });
				return id;
			}),
			filter: (fn) => store.filter(fn),
			update: vi.fn(async (id, changes) => {
				const row = store.rows.find((r) => r.id === id);
				if (!row) throw new Error(`Record not found: ${id}`);
				Object.assign(row, changes);
				return id;
			}),
			delete: vi.fn(async (id) => {
				const i = store.rows.findIndex((r) => r.id === id);
				if (i >= 0) store.rows.splice(i, 1);
			}),
		},
	},
}));

// serverErrorMessage mirrors the real helper's contract (apiWrapper.js): the
// server's translated messages joined, else the fallback.
vi.mock("@/utils/apiWrapper", () => ({
	call: vi.fn(),
	serverErrorMessage: (error, fallback = "Something went wrong") => {
		const messages = Array.isArray(error?.messages) ? error.messages.filter(Boolean) : [];
		return messages.length ? messages.join(" ") : fallback;
	},
}));

vi.mock("@/utils/logger", () => ({
	logger: {
		create: () => ({
			debug: vi.fn(),
			info: vi.fn(),
			warn: vi.fn(),
			error: vi.fn(),
			success: vi.fn(),
		}),
	},
}));

vi.mock("@/utils/mutex", () => ({
	CoalescingMutex: class {
		async withLock(fn) {
			return fn();
		}
	},
}));

vi.mock("@/utils/offline/offlineState", () => ({
	offlineState: {
		isOffline: false,
		subscribe: vi.fn(),
		setServerOnline: vi.fn(),
		getConnectionQuality: () => "good",
	},
}));

vi.mock("@/utils/offline/offlineReceiptCache", () => ({
	removeOfflineReceiptPayload: vi.fn(),
}));

import { call } from "@/utils/apiWrapper";
import {
	getOfflineInvoiceCount,
	getOfflineInvoices,
	retryOfflineInvoice,
	syncOfflineInvoices,
} from "@/utils/offline/sync";

const SUBMIT = "pos_next.api.invoices.submit_invoice";
const CHECK = "pos_next.api.invoices.check_offline_invoice_synced";

const submittedInvoices = () =>
	call.mock.calls.filter(([method]) => method === SUBMIT).map(([, payload]) => payload);

const seedRow = (overrides = {}) => ({
	id: store.rows.length + 1,
	offline_id: `pos_offline_seed-${store.rows.length + 1}`,
	data: { customer: "Walk-in Customer", items: [{ item_code: "ITEM-1", qty: 1 }], grand_total: 100 },
	timestamp: Date.now(),
	synced: false,
	retry_count: 0,
	...overrides,
});

beforeEach(() => {
	store.reset();
	vi.clearAllMocks();
	call.mockImplementation(async (method) =>
		method === CHECK ? { synced: false } : { name: "INV-001" }
	);
});

describe("COR-FE-04 sync_failed handling", () => {
	it("excludes sync_failed entries from the auto-sync batch without blocking later entries", async () => {
		store.rows.push(
			seedRow({ sync_failed: true, retry_count: 3, error: "Validation failed" })
		);
		store.rows.push(seedRow());

		const result = await syncOfflineInvoices();

		expect(result.success).toBe(1);
		expect(result.failed).toBe(0);
		expect(submittedInvoices()).toHaveLength(1);
		expect(store.rows[0].synced).toBe(false);
		expect(store.rows[1].synced).toBe(true);
	});

	it("skips a recently failed entry until the backoff delay elapses", async () => {
		// Failed once 1s ago; backoff after retry_count=1 is 5s * 2^1 = 10s.
		store.rows.push(seedRow({ retry_count: 1, last_attempt: Date.now() - 1000 }));

		await syncOfflineInvoices();
		expect(submittedInvoices()).toHaveLength(0);
		expect(store.rows[0].retry_count).toBe(1);

		// Once the delay has passed, the entry is picked up again.
		store.rows[0].last_attempt = Date.now() - 11000;
		await syncOfflineInvoices();
		expect(submittedInvoices()).toHaveLength(1);
	});

	it("grows the backoff per retry and flags sync_failed at the threshold", async () => {
		store.rows.push(seedRow());

		// Attempt 1 fails: recorded, no flag yet.
		call.mockImplementation(async (method) =>
			method === CHECK ? { synced: false } : Promise.reject(new Error("boom"))
		);
		await syncOfflineInvoices();
		expect(store.rows[0].retry_count).toBe(1);
		expect(store.rows[0].sync_failed).toBeFalsy();

		// Backoff after retry 1 is 10s: 9s later the entry is still skipped.
		store.rows[0].last_attempt = Date.now() - 9000;
		await syncOfflineInvoices();
		expect(store.rows[0].retry_count).toBe(1);
		expect(submittedInvoices()).toHaveLength(1); // only the first attempt

		// After 11s the second attempt goes through and fails too.
		store.rows[0].last_attempt = Date.now() - 11000;
		await syncOfflineInvoices();
		expect(store.rows[0].retry_count).toBe(2);

		// Backoff after retry 2 is 20s: 15s is not enough.
		store.rows[0].last_attempt = Date.now() - 15000;
		await syncOfflineInvoices();
		expect(store.rows[0].retry_count).toBe(2);
		expect(submittedInvoices()).toHaveLength(2);

		// After 21s the third attempt fails and hits the sync_failed threshold.
		store.rows[0].last_attempt = Date.now() - 21000;
		await syncOfflineInvoices();
		expect(store.rows[0].retry_count).toBe(3);
		expect(store.rows[0].sync_failed).toBe(true);
		expect(store.rows[0].error).toBe("boom");
	});

	it("retryOfflineInvoice resets the flag and the entry rejoins auto-sync", async () => {
		store.rows.push(
			seedRow({
				sync_failed: true,
				retry_count: 3,
				last_attempt: Date.now(),
				error: "Validation failed",
			})
		);

		const ok = await retryOfflineInvoice(store.rows[0].id);

		expect(ok).toBe(true);
		expect(store.rows[0].sync_failed).toBe(false);
		expect(store.rows[0].retry_count).toBe(0);
		expect(store.rows[0].last_attempt).toBe(0);
		expect(store.rows[0].error).toBeNull();

		const result = await syncOfflineInvoices();
		expect(result.success).toBe(1);
		expect(store.rows[0].synced).toBe(true);
	});

	it("excludes sync_failed entries from both the pending list and the pending count", async () => {
		store.rows.push(seedRow({ sync_failed: true, retry_count: 3, error: "Validation failed" }));
		store.rows.push(seedRow());

		// sync.js's list feeds auto-sync only (the dialog list comes from the
		// worker's own unfiltered copy, which keeps failed entries visible).
		expect(await getOfflineInvoices()).toHaveLength(1);
		expect(await getOfflineInvoiceCount()).toBe(1);
	});
});

// B2: a server ValidationError is a permanent rejection — the same payload
// can never sync — so the invoice is flagged sync_failed on the FIRST failure
// with a human-readable reason instead of burning MAX_RETRY_COUNT + backoff.
// Network / timeout / 5xx failures stay transient on the existing schedule.
describe("fail-fast on server ValidationError (B2)", () => {
	const validationError = (message, status = 417) => {
		const error = new Error(`${SUBMIT} ValidationError`);
		error.exc_type = "ValidationError";
		error.response = { status };
		error.messages = [message];
		return error;
	};

	it("flags sync_failed on the first server rejection with the human-readable reason", async () => {
		store.rows.push(seedRow());
		const reason = 'Stok tidak cukup untuk "ITEM-1" di gudang Gudang Utama';
		call.mockImplementation(async (method) =>
			method === CHECK ? { synced: false } : Promise.reject(validationError(reason))
		);

		const result = await syncOfflineInvoices();

		expect(result.failed).toBe(1);
		// Exactly one attempt: no retry inside the same cycle.
		expect(submittedInvoices()).toHaveLength(1);
		expect(store.rows[0].sync_failed).toBe(true);
		expect(store.rows[0].retry_count).toBe(1);
		// The stored reason is the server's message, not frappe-ui's
		// "POST /api/method/... ValidationError" wrapper.
		expect(store.rows[0].error).toBe(reason);
	});

	it("does not resubmit a permanently rejected invoice on later sync cycles", async () => {
		store.rows.push(seedRow());
		call.mockImplementation(async (method) =>
			method === CHECK ? { synced: false } : Promise.reject(validationError("rejected"))
		);

		await syncOfflineInvoices();
		expect(submittedInvoices()).toHaveLength(1);

		// The next cycle must not spend an attempt on the rejected payload.
		store.rows[0].last_attempt = 0;
		await syncOfflineInvoices();
		expect(submittedInvoices()).toHaveLength(1);
	});

	it("keeps network errors transient on the retry schedule", async () => {
		store.rows.push(seedRow());
		call.mockImplementation(async (method) =>
			method === CHECK ? { synced: false } : Promise.reject(new TypeError("Failed to fetch"))
		);

		await syncOfflineInvoices();
		expect(store.rows[0].retry_count).toBe(1);
		expect(store.rows[0].sync_failed).toBeFalsy();

		// Backoff still gates the next attempt: 1s later it is skipped.
		store.rows[0].last_attempt = Date.now() - 1000;
		await syncOfflineInvoices();
		expect(submittedInvoices()).toHaveLength(1);
		expect(store.rows[0].retry_count).toBe(1);

		// After the backoff elapses the entry is retried.
		store.rows[0].last_attempt = Date.now() - 11000;
		await syncOfflineInvoices();
		expect(store.rows[0].retry_count).toBe(2);
		expect(submittedInvoices()).toHaveLength(2);
	});

	it("keeps 5xx rejections transient even when an exc_type is present", async () => {
		store.rows.push(seedRow());
		call.mockImplementation(async (method) =>
			method === CHECK
				? { synced: false }
				: Promise.reject(validationError("Internal Server Error", 500))
		);

		await syncOfflineInvoices();
		expect(store.rows[0].retry_count).toBe(1);
		expect(store.rows[0].sync_failed).toBeFalsy();
	});

	it("retryOfflineInvoice re-queues a permanently rejected invoice", async () => {
		store.rows.push(seedRow());
		call.mockImplementation(async (method) =>
			method === CHECK ? { synced: false } : Promise.reject(validationError("rejected"))
		);
		await syncOfflineInvoices();
		expect(store.rows[0].sync_failed).toBe(true);

		const ok = await retryOfflineInvoice(store.rows[0].id);
		expect(ok).toBe(true);
		expect(store.rows[0].sync_failed).toBe(false);
		expect(store.rows[0].retry_count).toBe(0);
		expect(store.rows[0].error).toBeNull();

		// The cashier fixed the cause (or the server rule changed): the
		// re-queued invoice syncs normally.
		call.mockImplementation(async (method) =>
			method === CHECK ? { synced: false } : { name: "INV-001" }
		);
		const result = await syncOfflineInvoices();
		expect(result.success).toBe(1);
		expect(store.rows[0].synced).toBe(true);
	});
});

// COR-FE-11: payment_queue was written by saveOfflinePayment but never read
// by anyone. The offline save path is the worker's invoice_queue only.
describe("single offline save path (COR-FE-11)", () => {
	it("no longer exports saveOfflinePayment", async () => {
		const mod = await import("@/utils/offline/sync");

		expect(mod.saveOfflinePayment).toBeUndefined();
	});
});

// COR-FE-12: a queued item with qty 0 must never be sent with a fabricated
// quantity; the row is marked as failed instead. Absent qty stays at the
// legacy default of 1.
describe("zero quantity is rejected at sync time (COR-FE-12)", () => {
	it("does not submit a zero-qty invoice and marks the attempt failed", async () => {
		store.rows.push(
			seedRow({
				data: {
					customer: "Walk-in Customer",
					items: [{ item_code: "ITEM-1", qty: 0 }],
					grand_total: 100,
				},
			})
		);

		const result = await syncOfflineInvoices();

		expect(submittedInvoices()).toHaveLength(0);
		expect(result.failed).toBe(1);
		expect(store.rows[0].retry_count).toBe(1);
	});

	it("keeps the legacy default of 1 for an absent qty", async () => {
		store.rows.push(
			seedRow({
				data: {
					customer: "Walk-in Customer",
					items: [{ item_code: "ITEM-1" }],
					grand_total: 100,
				},
			})
		);

		const result = await syncOfflineInvoices();

		expect(result.success).toBe(1);
		const payload = JSON.parse(submittedInvoices()[0].data);
		expect(payload.invoice.items[0].qty).toBe(1);
	});
});
