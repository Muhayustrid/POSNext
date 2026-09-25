// Tests for the IndexedDB persistence layer (offline/db.js).
// Dexie is replaced with a controllable double so the schema declarations and
// the recovery path can be asserted without a real IndexedDB.
import { beforeEach, describe, expect, it, vi } from "vitest"

const DexieMock = vi.hoisted(() => {
	class DexieMock {
		constructor(name) {
			this.name = name
			this._versions = []
			this.isOpen = vi.fn(() => false)
			this.close = vi.fn()
			this.open = vi.fn(() => Promise.resolve())
		}
		version(number) {
			const entry = { number, stores: null }
			this._versions.push(entry)
			const self = this
			return {
				stores(schema) {
					entry.stores = schema
					return self
				},
			}
		}
	}
	DexieMock.delete = vi.fn(() => Promise.resolve())
	return DexieMock
})

vi.mock("dexie", () => ({ default: DexieMock }))
vi.mock("../logger", () => ({
	logger: {
		create: () => ({
			debug: vi.fn(),
			info: vi.fn(),
			warn: vi.fn(),
			error: vi.fn(),
			success: vi.fn(),
		}),
	},
}))

import { checkDBHealth, clearBrowserCache, db } from "./db"

beforeEach(() => {
	localStorage.clear()
	sessionStorage.clear()
	vi.clearAllMocks()
	db.open.mockResolvedValue(undefined)
	DexieMock.delete.mockResolvedValue(undefined)
})

describe("clearBrowserCache protects frappe session keys (COR-FE-16)", () => {
	it("clears POS-owned keys and keeps frappe session/CSRF keys", () => {
		localStorage.setItem("frappe_user", "cashier1")
		localStorage.setItem("frappe_csrf_token", "csrf-token")
		localStorage.setItem("pos_next_cache_ready", "1")
		localStorage.setItem("pos_next_schema_hash", "abc")
		sessionStorage.setItem("pos_next_ui_state", "grid")
		sessionStorage.setItem("frappe_user", "cashier1")

		const result = clearBrowserCache()

		expect(result.success).toBe(true)
		// Session keys survive: wiping them logged the cashier out.
		expect(localStorage.getItem("frappe_user")).toBe("cashier1")
		expect(localStorage.getItem("frappe_csrf_token")).toBe("csrf-token")
		expect(sessionStorage.getItem("frappe_user")).toBe("cashier1")
		// POS-owned keys are still cleared.
		expect(localStorage.getItem("pos_next_cache_ready")).toBeNull()
		expect(localStorage.getItem("pos_next_schema_hash")).toBeNull()
		expect(sessionStorage.getItem("pos_next_ui_state")).toBeNull()
	})
})

describe("checkDBHealth backs up the queues before recreating (COR-FE-08)", () => {
	const versionError = () =>
		Object.assign(new Error("version mismatch"), { name: "VersionError" })

	function seedQueueTables({ invoices = true, drafts = true, readable = true } = {}) {
		db.settings = {
			get: vi.fn().mockRejectedValue(new Error("health check read failed")),
		}
		db.invoice_queue = {
			toArray: vi.fn(() =>
				readable
					? Promise.resolve(
							invoices
								? [
										{
											id: 1,
											offline_id: "pos_offline_a",
											data: { customer: "Bob", grand_total: 100 },
											synced: false,
										},
									]
								: [],
						)
					: Promise.reject(new Error("unreadable")),
			),
			bulkPut: vi.fn(() => Promise.resolve()),
		}
		db.drafts = {
			toArray: vi.fn(() =>
				readable ? Promise.resolve(drafts ? [{ id: 7, draft_id: "D-1" }] : []) : Promise.reject(new Error("unreadable")),
			),
			bulkPut: vi.fn(() => Promise.resolve()),
		}
	}

	it("exports invoice_queue + drafts before Dexie.delete and restores them after", async () => {
		seedQueueTables()
		db.isOpen.mockReturnValue(false)
		db.open
			.mockRejectedValueOnce(versionError()) // reopen attempt
			.mockResolvedValueOnce(undefined) // recreate attempt

		let backupAtDeleteTime = null
		DexieMock.delete.mockImplementation(async () => {
			// The export MUST already be on disk when the delete runs.
			backupAtDeleteTime = localStorage.getItem("pos_next_invoice_queue_backup")
		})

		const result = await checkDBHealth()

		expect(result).toBe(true)
		expect(JSON.parse(backupAtDeleteTime)).toEqual([
			{ id: 1, offline_id: "pos_offline_a", data: { customer: "Bob", grand_total: 100 }, synced: false },
		])
		// Rows are back in the fresh database and the backup copy is gone.
		expect(db.invoice_queue.bulkPut).toHaveBeenCalledWith([
			{ id: 1, offline_id: "pos_offline_a", data: { customer: "Bob", grand_total: 100 }, synced: false },
		])
		expect(db.drafts.bulkPut).toHaveBeenCalledWith([{ id: 7, draft_id: "D-1" }])
		expect(localStorage.getItem("pos_next_invoice_queue_backup")).toBeNull()
		expect(localStorage.getItem("pos_next_drafts_backup")).toBeNull()
	})

	it("recreates the database even when the queues cannot be read (review MAJOR)", async () => {
		seedQueueTables({ readable: false })
		db.isOpen.mockReturnValue(false)
		db.open
			.mockRejectedValueOnce(versionError()) // reopen attempt
			.mockResolvedValueOnce(undefined) // recreate attempt

		const result = await checkDBHealth()

		// A database that cannot even be read holds nothing an export could
		// save: refusing the reset would just brick the till forever.
		expect(result).toBe(true)
		expect(DexieMock.delete).toHaveBeenCalled()
		// No backup was possible, so no stale backup keys are left behind.
		expect(localStorage.getItem("pos_next_invoice_queue_backup")).toBeNull()
	})

	it("never deletes the database when the export hits the localStorage quota", async () => {
		seedQueueTables()
		db.isOpen.mockReturnValue(false)
		// clearAllMocks does not drain once-queues: a leftover resolve from an
		// earlier test would make the reopen succeed and hide this path
		db.open.mockReset()
		db.open.mockRejectedValueOnce(versionError())

		// The module reaches Storage through the prototype, so the quota
		// failure has to be injected there.
		const setItemSpy = vi
			.spyOn(Storage.prototype, "setItem")
			.mockImplementation(() => {
				throw Object.assign(new Error("quota exceeded"), { name: "QuotaExceededError" })
			})
		try {
			const result = await checkDBHealth()

			// The data is still readable in place: the reset must abort so a
			// manual recovery stays possible.
			expect(result).toBe(false)
			expect(DexieMock.delete).not.toHaveBeenCalled()
		} finally {
			setItemSpy.mockRestore()
		}
	})
})

describe("payment_queue is dropped from the Dexie schema (COR-FE-11)", () => {
	it("declares no payment_queue table in any version", () => {
		for (const version of db._versions) {
			if (!version.stores) continue
			// Absent or explicitly null (drop marker) are both fine; a schema
			// string like "++id, timestamp, synced" is not.
			expect(version.stores.payment_queue ?? null).toBeNull()
		}
	})

	it("keeps a drop version so running databases lose the dead table", () => {
		const dropVersions = db._versions.filter(
			(v) => v.stores && v.stores.payment_queue === null
		)
		expect(dropVersions.length).toBeGreaterThanOrEqual(1)
	})
})
