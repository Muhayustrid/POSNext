// Draft Invoice Management with IndexedDB
import { __ } from "@/utils/translation";

const DB_NAME = "pos_next_drafts";
const DB_VERSION = 1;
const STORE_NAME = "invoices";

let db = null;

function sanitizeDraftData(data) {
	if (data === null || data === undefined) {
		return data;
	}

	try {
		// Vue reactive refs/proxies can't be stored directly in IndexedDB.
		// Serializing through JSON removes reactivity while keeping data shallow.
		return JSON.parse(JSON.stringify(data));
	} catch (error) {
		console.warn("Failed to sanitize draft data for IndexedDB storage", error);
		throw error;
	}
}

// Initialize IndexedDB
async function initDB() {
	if (db) return db;

	return new Promise((resolve, reject) => {
		const request = indexedDB.open(DB_NAME, DB_VERSION);

		request.onerror = () => reject(request.error);
		request.onsuccess = () => {
			db = request.result;
			resolve(db);
		};

		request.onupgradeneeded = (event) => {
			const database = event.target.result;

			// Create object store if it doesn't exist
			if (!database.objectStoreNames.contains(STORE_NAME)) {
				const objectStore = database.createObjectStore(STORE_NAME, {
					keyPath: "id",
					autoIncrement: true,
				});

				// Create indexes
				objectStore.createIndex("draft_id", "draft_id", { unique: true });
				objectStore.createIndex("created_at", "created_at", { unique: false });
				objectStore.createIndex("customer", "customer", { unique: false });
			}
		};
	});
}

// Save draft invoice
export async function saveDraft(invoiceData) {
	const database = await initDB();
	const sanitizedInvoiceData = sanitizeDraftData(invoiceData) || {};

	const draft = {
		draft_id: `DRAFT-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
		...sanitizedInvoiceData,
		created_at: new Date().toISOString(),
		updated_at: new Date().toISOString(),
	};

	return new Promise((resolve, reject) => {
		const transaction = database.transaction([STORE_NAME], "readwrite");
		const store = transaction.objectStore(STORE_NAME);
		const request = store.add(draft);

		request.onsuccess = () => resolve(draft);
		request.onerror = () => reject(request.error);
	});
}

// Update existing draft
export async function updateDraft(draftId, invoiceData) {
	const database = await initDB();

	return new Promise(async (resolve, reject) => {
		try {
			// Get existing draft
			const existingDraft = await getDraftById(draftId);
			if (!existingDraft) {
				return reject(new Error(__("Draft not found")));
			}

			const sanitizedInvoiceData = sanitizeDraftData(invoiceData) || {};
			const updatedDraft = {
				...existingDraft,
				...sanitizedInvoiceData,
				updated_at: new Date().toISOString(),
			};

			const transaction = database.transaction([STORE_NAME], "readwrite");
			const store = transaction.objectStore(STORE_NAME);
			const request = store.put(updatedDraft);

			request.onsuccess = () => resolve(updatedDraft);
			request.onerror = () => reject(request.error);
		} catch (error) {
			reject(error);
		}
	});
}

// Drafts live on a shared device, but belong to the account that saved them.
// Reads are scoped to the active user; ownerless (pre-owner-scope) drafts stay
// visible to every account so old data is never silently hidden.
export function isDraftVisible(draft, owner) {
	return draft.owner == null || draft.owner === owner;
}

// Get all draft invoices visible to the given owner
export async function getAllDrafts(owner) {
	const database = await initDB();

	return new Promise((resolve, reject) => {
		const transaction = database.transaction([STORE_NAME], "readonly");
		const store = transaction.objectStore(STORE_NAME);
		const request = store.getAll();

		request.onsuccess = () => {
			// ponytail: in-memory filter instead of an owner index — a few dozen
			// drafts per device at most; bump DB_VERSION only if that changes.
			const drafts = request.result
				.filter((draft) => isDraftVisible(draft, owner))
				.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
			resolve(drafts);
		};
		request.onerror = () => reject(request.error);
	});
}

// Get draft by ID
export async function getDraftById(draftId) {
	const database = await initDB();

	return new Promise((resolve, reject) => {
		const transaction = database.transaction([STORE_NAME], "readonly");
		const store = transaction.objectStore(STORE_NAME);
		const index = store.index("draft_id");
		const request = index.get(draftId);

		request.onsuccess = () => resolve(request.result);
		request.onerror = () => reject(request.error);
	});
}

// Delete draft
export async function deleteDraft(draftId) {
	const database = await initDB();

	return new Promise(async (resolve, reject) => {
		try {
			const draft = await getDraftById(draftId);
			if (!draft) {
				return reject(new Error(__("Draft not found")));
			}

			const transaction = database.transaction([STORE_NAME], "readwrite");
			const store = transaction.objectStore(STORE_NAME);
			const request = store.delete(draft.id);

			request.onsuccess = () => resolve(true);
			request.onerror = () => reject(request.error);
		} catch (error) {
			reject(error);
		}
	});
}

// Clear all drafts. Unscoped (owner == null) wipes the whole store — used by
// logout cleanup. With an owner, only deletes drafts that owner can see so
// other accounts' drafts survive a "Clear All" in the dialog.
export async function clearAllDrafts(owner) {
	const database = await initDB();

	if (owner == null) {
		return new Promise((resolve, reject) => {
			const transaction = database.transaction([STORE_NAME], "readwrite");
			const store = transaction.objectStore(STORE_NAME);
			const request = store.clear();

			request.onsuccess = () => resolve(true);
			request.onerror = () => reject(request.error);
		});
	}

	const drafts = await getAllDrafts(owner);
	await Promise.all(drafts.map((draft) => deleteDraft(draft.draft_id)));
	return true;
}

// Get drafts count visible to the given owner
export async function getDraftsCount(owner) {
	const drafts = await getAllDrafts(owner);
	return drafts.length;
}
