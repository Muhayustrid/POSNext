import { clearAllDrafts } from "@/utils/draftManager";
import { db } from "@/utils/offline/db";
import { clearAllOfflineReceiptPayloads } from "@/utils/offline/offlineReceiptCache";
import { clearInvoiceHistoryCache } from "@/utils/offline/sync";
import { usePOSCartStore } from "@/stores/posCart";
import { usePOSUIStore } from "@/stores/posUI";
import { useSessionLock } from "@/composables/useSessionLock";
import { shiftState } from "@/composables/useShift";

// All user-specific localStorage keys that must be cleared on logout.
// Device-level settings (pos_qz_printer_name, pos_stock_sync_settings,
// pos_performance_tier, pos_next_language, pos_next_schema_*, pos_next_cache_*)
// are intentionally NOT cleared.
const USER_KEYS = [
	"pos_session_lock",
	"pos_session_pwd_hash",
	"pos_lock_attempts",
	"pos_shift_data",
	"pos_recent_customers",
	"pos_frequent_customers",
	"pos_customers_last_sync",
	"pos_invoice_filters",
];

// Workbox runtime caches that hold cashier-scoped responses (names mirror
// vite.config.js runtimeCaching). Shared caches (fonts, app assets, product
// images) are intentionally kept. "api-cache" is no longer populated (the
// /api/ rule is NetworkOnly now) but must still be wiped on devices where an
// older build filled it; "pos-page-cache" holds /pos HTML with embedded boot
// data of the logged-in user.
const CASHIER_RUNTIME_CACHES = /^(api-cache|pos-page-cache)$/;

/**
 * Best-effort removal of cross-cashier data in Cache Storage and IndexedDB.
 * Cache Storage is shared per-origin between the window and the service
 * worker, so deleting here also clears anything the SW cached — the generated
 * workbox SW has no message listener, so no postMessage is needed.
 */
async function clearCrossCashierCaches() {
	if (typeof caches !== "undefined") {
		try {
			const names = await caches.keys();
			await Promise.all(
				names
					.filter((name) => CASHIER_RUNTIME_CACHES.test(name))
					.map((name) => caches.delete(name)),
			);
		} catch (error) {
			console.error("Failed to clear runtime caches:", error);
		}
	}

	// IndexedDB stores holding data scoped to the previous cashier
	try {
		await Promise.all([
			clearInvoiceHistoryCache(),
			db.unpaid_invoices.clear(),
			db.customers.clear(),
			db.payment_methods.clear(),
		]);
	} catch (error) {
		console.error("Failed to clear offline cashier stores:", error);
	}
}

/**
 * Centralized cleanup of all user-specific session state.
 * Call this from every logout path to ensure consistent cleanup
 * regardless of how the user exits (logout button, shift close,
 * lock screen sign-out, or session expiry).
 */
export async function cleanupUserSession() {
	// 1. Clear all user-specific localStorage keys
	for (const key of USER_KEYS) {
		localStorage.removeItem(key);
	}

	// Clear cashier-specific sessionStorage (offline receipt cache) so the next
	// user on the same tab can't read the previous user's receipts.
	clearAllOfflineReceiptPayloads();

	// 2. Clear Pinia stores
	const cartStore = usePOSCartStore();
	const uiStore = usePOSUIStore();
	cartStore.clearCart();
	// Reset shift/profile refs that persist in the useInvoice singleton
	// (clearCart intentionally does NOT reset these since it's also called between transactions)
	cartStore.posOpeningShift = null;
	cartStore.posProfile = null;
	uiStore.resetAllDialogs();

	// 3. Reset composable singletons
	const { clearLock, stopActivityTracking } = useSessionLock();
	clearLock();
	stopActivityTracking();

	// Reset shift state
	shiftState.value = {
		pos_opening_shift: null,
		pos_profile: null,
		company: null,
		isOpen: false,
		_initialElapsedMs: 0,
		_receivedAt: 0,
	};

	// 4. Clear draft invoices from IndexedDB
	try {
		await clearAllDrafts();
	} catch (error) {
		console.error("Failed to clear draft invoices:", error);
	}

	// 5. Clear cross-cashier Cache Storage + IndexedDB data (SEC-17)
	await clearCrossCashierCaches();
}
