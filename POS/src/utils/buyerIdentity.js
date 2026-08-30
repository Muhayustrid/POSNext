/**
 * Buyer identity gating rules (OpenSpec queue-buyer-identity, D1/D2 UI side).
 *
 * Pure functions so the submit-blocking logic is unit-testable without mounting
 * PaymentDialog/InvoiceCart. The server re-validates everything in
 * pos_next/api/invoices.py; these rules only drive the UI (chip display,
 * canComplete gating, focus).
 */

/**
 * Whether the buyer-name input (and queue chip) should be shown at all.
 * @param {boolean} enableBuyerIdentity - POS Settings switch
 * @returns {boolean}
 */
export function isBuyerIdentityEnabled(enableBuyerIdentity) {
	return Boolean(enableBuyerIdentity);
}

/**
 * Whether submitting must be blocked because a mandatory buyer name is missing.
 * A whitespace-only name counts as absent (mirrors server-side sanitization).
 * @param {Object} state
 * @param {boolean} state.enableBuyerIdentity
 * @param {boolean} state.requireBuyerName
 * @param {string|null|undefined} state.buyerName
 * @returns {boolean}
 */
export function isBuyerNameRequiredButMissing({
	enableBuyerIdentity,
	requireBuyerName,
	buyerName,
}) {
	if (!isBuyerIdentityEnabled(enableBuyerIdentity) || !requireBuyerName) {
		return false;
	}
	return !String(buyerName ?? "").trim();
}

/**
 * Next queue number for the open POS Opening Shift, as shown on the chip.
 * The shift doc carries `current_queue_number` (highest server-allocated
 * number); +1 is the next sale's number. While offline this is the terminal's
 * local estimate, reconciled to the server value at sync time (D2).
 * @param {Object|null|undefined} currentShift - POS Opening Shift doc
 * @returns {number|null} null when no shift is open or the counter is missing
 */
export function getNextQueueNumber(currentShift) {
	const allocated = Number.parseInt(currentShift?.current_queue_number, 10);
	return Number.isFinite(allocated) ? allocated + 1 : null;
}

/** localStorage key (see useShift) holding the last known POS Opening Shift doc. */
const SHIFT_CACHE_KEY = "pos_shift_data";

/**
 * Resolve the shift document to base a queue number on.
 *
 * While online `shiftStore.currentShift` is live. After connectivity drops the
 * checkOpeningShift resource can fail and leave it null, so we fall back to the
 * shift the app persisted to localStorage on the last successful check/create.
 * A stale counter is acceptable here: this only feeds the *local estimate*
 * printed on an offline receipt (D2), which is reconciled to the server value
 * at sync time via reconcileQueueAfterSync.
 * @param {Object|null|undefined} currentShift - Live shift doc from the store, if any
 * @returns {Object|null} The shift doc to use, or null when none is known
 */
export function resolveShiftForQueueEstimate(currentShift) {
	if (currentShift?.name) return currentShift;
	if (typeof localStorage === "undefined") return null;
	try {
		const cached = JSON.parse(localStorage.getItem(SHIFT_CACHE_KEY) || "null");
		return cached?.pos_opening_shift?.name ? cached.pos_opening_shift : null;
	} catch {
		return null;
	}
}

/**
 * Local queue-number estimate for an offline sale's printed receipt.
 * @param {Object|null|undefined} currentShift - Live shift doc (may be null offline)
 * @returns {number|null} null when no shift is known at all
 */
export function getOfflineQueueEstimate(currentShift) {
	return getNextQueueNumber(resolveShiftForQueueEstimate(currentShift));
}

/**
 * Reconcile a synced offline invoice queue record with the server result (D2).
 *
 * The server allocates the authoritative `queue_number` at submit and returns
 * the invoice dict; the terminal printed `offline_queue_estimate` from the last
 * known counter. Audit requires BOTH values on the record: the estimate tells
 * what the customer actually held, `server_queue_number` what the counter
 * allocated. The record's `data.offline_queue_estimate` is the source of truth
 * and is never overwritten; the top-level copy exists for querying.
 *
 * Mutates and returns `record` so the caller can pass a live Dexie row.
 * @param {Object} record - invoice_queue record (has `data`, may have `id`)
 * @param {Object|null|undefined} serverResult - submit_invoice result dict
 * @param {Object} [options]
 * @param {boolean} [options.persist=true] - Write through to invoice_queue
 *   (needs `record.id`). Tests and read-only callers can disable it.
 * @returns {Promise<{server_queue_number: number|null, changed: boolean}>}
 */
export async function reconcileQueueAfterSync(record, serverResult, { persist = true } = {}) {
	const allocated = Number.parseInt(serverResult?.queue_number, 10);
	const serverQueueNumber = Number.isFinite(allocated) ? allocated : null;
	const changed = record.server_queue_number !== serverQueueNumber;

	record.server_queue_number = serverQueueNumber;

	const estimate = record.data?.offline_queue_estimate;
	if (estimate != null) {
		record.offline_queue_estimate = estimate;
	}

	if (persist && record.id != null && changed) {
		const { db } = await import("@/utils/offline/db");
		await db.invoice_queue.update(record.id, { server_queue_number: serverQueueNumber });
	}

	return { server_queue_number: serverQueueNumber, changed };
}
