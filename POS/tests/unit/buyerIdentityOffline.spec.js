import { beforeEach, describe, expect, it, vi } from "vitest";

// Minimal in-memory stand-in for the IndexedDB layer (the real db module opens
// Dexie eagerly on import; the migration path itself is covered by
// offlineQueueMigration.spec.js). Only the surface sync.js touches is stubbed.
function makeTable() {
	const rows = [];
	let nextId = 1;
	return {
		rows,
		async add(row) {
			const id = nextId++;
			rows.push({ ...row, id });
			return id;
		},
		async update(id, changes) {
			const row = rows.find((r) => r.id === id);
			if (row) Object.assign(row, changes);
			return row ? 1 : 0;
		},
		async get(id) {
			return rows.find((r) => r.id === id);
		},
		filter(predicate) {
			return {
				async toArray() {
					return rows.filter(predicate);
				},
				async count() {
					return rows.filter(predicate).length;
				},
				async delete() {
					const doomed = rows.filter(predicate);
					for (const d of doomed) rows.splice(rows.indexOf(d), 1);
				},
			};
		},
	};
}

const mockDb = {
	invoice_queue: makeTable(),
	stock: {
		_rows: [],
		async get(key) {
			return this._rows.find((r) => r.item_code === key.item_code && r.warehouse === key.warehouse);
		},
		async put(row) {
			const existing = this._rows.find(
				(r) => r.item_code === row.item_code && r.warehouse === row.warehouse
			);
			if (existing) Object.assign(existing, row);
			else this._rows.push(row);
		},
	},
};

vi.mock("@/utils/offline/db", () => ({ db: mockDb }));
vi.mock("@/utils/offline/offlineState", () => ({
	offlineState: { isOffline: false, setServerOnline: () => {} },
}));

const mockCall = vi.fn();
vi.mock("@/utils/apiWrapper", () => ({
	call: (...args) => mockCall(...args),
}));

const { saveOfflineInvoice, getOfflineInvoices, syncOfflineInvoices } = await import(
	"@/utils/offline/sync"
);
const { reconcileQueueAfterSync } = await import("@/utils/buyerIdentity");

function buildInvoiceData(overrides = {}) {
	return {
		pos_profile: "POS Profile",
		posa_pos_opening_shift: "SHIFT-1",
		customer: "Walk-in Customer",
		items: [{ item_code: "BREAD", qty: 2, rate: 5000, warehouse: "W1" }],
		grand_total: 10000,
		buyer_name: "Budi",
		offline_queue_estimate: 7,
		...overrides,
	};
}

describe("offline queue buyer identity + sync reconciliation (OpenSpec task 2.11)", () => {
	beforeEach(() => {
		mockDb.invoice_queue.rows.length = 0;
		mockDb.stock._rows.length = 0;
		mockCall.mockReset();
	});

	it("saveOfflineInvoice carries buyer_name and the local estimate through the clone", async () => {
		const { offline_id } = await saveOfflineInvoice(buildInvoiceData());

		const pending = await getOfflineInvoices();
		expect(pending).toHaveLength(1);
		expect(pending[0].data.buyer_name).toBe("Budi");
		expect(pending[0].data.offline_queue_estimate).toBe(7);
		expect(pending[0].offline_id).toBe(offline_id);
		// The client never sends an authoritative queue_number; the server
		// allocates it (and strips any client value).
		expect(pending[0].data.queue_number).toBeUndefined();
	});

	it("after sync the record holds BOTH the printed estimate and the server number", async () => {
		await saveOfflineInvoice(buildInvoiceData());
		const record = (await getOfflineInvoices())[0];

		// Server allocates 9 (different from the estimate 7: drift is the point).
		mockCall.mockImplementation(async (method) => {
			if (method === "pos_next.api.invoices.check_offline_invoice_synced") {
				return { synced: false };
			}
			return { name: "ACC-SAL-INV-0009", queue_number: 9 };
		});

		const result = await syncOfflineInvoices();
		expect(result.success).toBe(1);

		// The in-memory row the sync operated on is reconciled in place, and
		// the persisted update went through invoice_queue.update (same object).
		expect(record.server_queue_number).toBe(9);
		expect(record.data.offline_queue_estimate).toBe(7); // printed value retained
		expect(record.data.buyer_name).toBe("Budi");
		expect(record.synced).toBe(true);
		expect(record.server_invoice).toBe("ACC-SAL-INV-0009");
	});

	it("sync without a server queue_number records null but keeps the estimate", async () => {
		await saveOfflineInvoice(buildInvoiceData());
		const record = (await getOfflineInvoices())[0];

		mockCall.mockImplementation(async (method) => {
			if (method === "pos_next.api.invoices.check_offline_invoice_synced") {
				return { synced: false };
			}
			return { name: "ACC-SAL-INV-0010" }; // buyer identity off server-side
		});

		await syncOfflineInvoices();
		expect(record.server_queue_number).toBeNull();
		expect(record.data.offline_queue_estimate).toBe(7);
	});

	it("reconcileQueueAfterSync coerces junk and never mutates the estimate", async () => {
		const record = { id: 1, data: { offline_queue_estimate: 4 } };
		const out = await reconcileQueueAfterSync(
			{ ...record, id: undefined },
			{ queue_number: "not-a-number" },
			{ persist: false }
		);
		expect(out.server_queue_number).toBeNull();

		const out2 = await reconcileQueueAfterSync(record, { queue_number: "12" }, { persist: false });
		expect(out2.server_queue_number).toBe(12);
		expect(record.data.offline_queue_estimate).toBe(4);
		expect(record.offline_queue_estimate).toBe(4);
	});
});
