import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

// draftManager is IndexedDB-backed; the store's contract is just "whatever
// draftData it builds reaches saveDraft/updateDraft and comes back through
// loadDraft", so an in-memory stub covers the persistence semantics for 2.10.
const drafts = [];
let nextDraftId = 1;

vi.mock("@/utils/draftManager", () => ({
	saveDraft: vi.fn(async (invoiceData) => {
		const draft = {
			...invoiceData,
			id: nextDraftId,
			draft_id: `DRAFT-${nextDraftId}`,
		};
		nextDraftId++;
		drafts.push(draft);
		return draft;
	}),
	updateDraft: vi.fn(async (draftId, invoiceData) => {
		const existing = drafts.find((d) => d.draft_id === draftId);
		if (!existing) throw new Error("Draft not found");
		Object.assign(existing, invoiceData);
		return existing;
	}),
	getAllDrafts: vi.fn(async () => drafts),
	getDraftsCount: vi.fn(async () => drafts.length),
	deleteDraft: vi.fn(async (draftId) => {
		const idx = drafts.findIndex((d) => d.draft_id === draftId);
		if (idx >= 0) drafts.splice(idx, 1);
		return true;
	}),
}));

const { usePOSDraftsStore } = await import("@/stores/posDrafts");

describe("draft buyer-name round trip (OpenSpec task 2.10)", () => {
	beforeEach(() => {
		setActivePinia(createPinia());
		drafts.length = 0;
		nextDraftId = 1;
	});

	it("persists a trimmed buyer_name in the saved draft", async () => {
		const store = usePOSDraftsStore();
		const saved = await store.saveDraftInvoice(
			[{ item_code: "BREAD", qty: 1, rate: 5000 }],
			"CUSTOMER-A",
			"POS Profile",
			[],
			null,
			"  Budi  "
		);

		expect(saved).toBeTruthy();
		expect(saved.buyer_name).toBe("Budi");
		// The draft must NOT carry a queue number: the server allocates the
		// authoritative number at submit, and the local chip estimate is
		// re-derived from the live shift on resume.
		expect(saved.queue_number).toBeUndefined();
		expect(saved.offline_queue_estimate).toBeUndefined();
	});

	it("resuming a draft returns the stored buyer name", async () => {
		const store = usePOSDraftsStore();
		const saved = await store.saveDraftInvoice(
			[{ item_code: "BREAD", qty: 1, rate: 5000 }],
			"CUSTOMER-A",
			"POS Profile",
			[],
			null,
			"Siti"
		);

		const restored = await store.loadDraft(saved);
		expect(restored.buyer_name).toBe("Siti");
		expect(restored.items).toHaveLength(1);
	});

	it("drafts saved without a buyer name resume as null, not undefined-crash", async () => {
		const store = usePOSDraftsStore();
		const saved = await store.saveDraftInvoice(
			[{ item_code: "BREAD", qty: 1, rate: 5000 }],
			"CUSTOMER-A",
			"POS Profile",
			[],
			null,
			undefined
		);
		expect(saved.buyer_name).toBeNull();
		const restored = await store.loadDraft(saved);
		expect(restored.buyer_name).toBeNull();
	});

	it("legacy drafts (predating buyer_name) load with a null name", async () => {
		const store = usePOSDraftsStore();
		const legacy = {
			id: 99,
			draft_id: "DRAFT-LEGACY",
			pos_profile: "POS Profile",
			customer: "CUSTOMER-A",
			items: [],
			applied_offers: [],
		};
		const restored = await store.loadDraft(legacy);
		expect(restored.buyer_name).toBeNull();
	});

	it("empty carts are rejected before any persistence", async () => {
		const store = usePOSDraftsStore();
		const saved = await store.saveDraftInvoice([], "CUSTOMER-A", "POS Profile", [], null, "Budi");
		expect(saved).toBeNull();
		expect(drafts).toHaveLength(0);
	});
});
