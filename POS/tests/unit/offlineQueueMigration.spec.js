import "fake-indexeddb/auto";
import Dexie from "dexie";
import { describe, expect, it } from "vitest";

/**
 * OpenSpec task 2.12: prove the invoice_queue schema bump is additive — a
 * database that already holds queued, unsynced invoices keeps them intact
 * through the v1 -> v2 upgrade that the buyer-identity fields ride on.
 *
 * SCHEMA_V1 below is a deliberate literal copy of the pre-migration schema
 * (invoice_queue WITHOUT the `server_queue_number` index, every other store
 * unchanged) because a migration fixture must not be derived from the new
 * schema or it can never catch drift. The hash assertion ties it to the
 * fromHash the production module registers, so the fixture and the migration
 * definition cannot diverge silently.
 */
const SCHEMA_V1 = {
	settings: "&key",
	invoice_queue: "++id, &offline_id, timestamp, synced",
	items: "&item_code, item_name, item_group, variant_of, has_variants, brand, *barcodes",
	customers: "&name, customer_name, mobile_no, email_id",
	item_prices: "&[price_list+item_code], price_list, item_code",
	stock: "&[item_code+warehouse], item_code, warehouse",
	payment_methods: "&mode_of_payment, pos_profile",
	sales_persons: "&name, pos_profile",
	payment_queue: "++id, timestamp, synced",
	drafts: "++id, draft_id, timestamp",
	translations: "&locale, timestamp",
	offers: "&name, pos_profile, apply_on, valid_upto",
	invoice_history: "&name, pos_profile, posting_date, customer",
	unpaid_invoices: "&name, pos_profile, outstanding_amount, customer",
	one_time_redemptions: "&customer",
};

// Mirror of db.js's private djb2 hash, used only to verify that the seeded
// fixture's hash matches the migration the production module registered.
function hashSchema(schema) {
	const schemaString = JSON.stringify(schema);
	let hash = 0;
	for (let i = 0; i < schemaString.length; i++) {
		const char = schemaString.charCodeAt(i);
		hash = (hash << 5) - hash + char;
		hash = hash & hash;
	}
	return Math.abs(hash);
}

const QUEUED_ROW = {
	offline_id: "pos_offline_seed-before-migration",
	data: {
		pos_profile: "POS Profile",
		customer: "Walk-in Customer",
		items: [{ item_code: "BREAD", qty: 1, rate: 5000 }],
		grand_total: 5000,
		// A row queued before the buyer-identity flow simply lacks these
		// fields; after the upgrade it must be untouched, not rewritten.
	},
	timestamp: 1700000000000,
	synced: false,
	retry_count: 0,
};

const QUEUED_ROW_WITH_IDENTITY = {
	offline_id: "pos_offline_seed-with-buyer",
	data: {
		pos_profile: "POS Profile",
		customer: "Walk-in Customer",
		items: [{ item_code: "BREAD", qty: 2, rate: 5000 }],
		grand_total: 10000,
		buyer_name: "Budi",
		offline_queue_estimate: 7,
	},
	timestamp: 1700000000500,
	synced: false,
	retry_count: 0,
};

describe("invoice_queue additive schema migration v1 -> v2 (OpenSpec task 2.12)", () => {
	it("upgrading a database with queued unsynced invoices preserves them intact", async () => {
		// 1. Seed a v1 database holding two unsynced queued invoices, exactly
		// as the terminal would have left them on an app update.
		const seedDb = new Dexie("pos_next_offline");
		seedDb.version(1).stores(SCHEMA_V1);
		await seedDb.open();
		await seedDb.invoice_queue.add({ ...QUEUED_ROW });
		await seedDb.invoice_queue.add({ ...QUEUED_ROW_WITH_IDENTITY });
		seedDb.close();

		// 2. Pretend the terminal last ran the v1 schema, then load the
		// production module. Its auto-versioner sees the v1 hash, bumps to
		// v2, attaches the registered migration's upgrade, and the eager
		// initDB() open fires IndexedDB's versionchange, running it.
		localStorage.setItem("pos_next_schema_hash", String(hashSchema(SCHEMA_V1)));
		localStorage.setItem("pos_next_schema_version", "1");
		const { MIGRATIONS, db, checkDBHealth } = await import("@/utils/offline/db");

		// The fixture must hash to the migration's fromHash, otherwise the
		// production upgrade path was never exercised.
		expect(MIGRATIONS.length).toBeGreaterThanOrEqual(1);
		expect(String(MIGRATIONS[0].fromHash)).toBe(String(hashSchema(SCHEMA_V1)));

		await db.open();
		expect(await checkDBHealth()).toBe(true);

		// 3. The version tracker advanced exactly one step.
		expect(localStorage.getItem("pos_next_schema_version")).toBe("2");

		// 4. Both queued invoices survived intact: same ids, same data, still
		// unsynced; the buyer-identity payload fields are preserved.
		const rows = await db.invoice_queue.toArray();
		expect(rows).toHaveLength(2);

		const legacy = rows.find((r) => r.offline_id === QUEUED_ROW.offline_id);
		expect(legacy).toBeTruthy();
		expect(legacy.synced).toBe(false);
		expect(legacy.timestamp).toBe(QUEUED_ROW.timestamp);
		expect(legacy.retry_count).toBe(0);
		expect(legacy.data.grand_total).toBe(5000);

		const identity = rows.find((r) => r.offline_id === QUEUED_ROW_WITH_IDENTITY.offline_id);
		expect(identity.data.buyer_name).toBe("Budi");
		expect(identity.data.offline_queue_estimate).toBe(7);

		// 5. The new index is live on upgraded rows: absent field reads as no
		// match, a written value indexes normally.
		await db.invoice_queue.update(identity.id, { server_queue_number: 9 });
		const numbered = await db.invoice_queue.where("server_queue_number").equals(9).primaryKeys();
		expect(numbered).toEqual([identity.id]);

		db.close();
	});
});
