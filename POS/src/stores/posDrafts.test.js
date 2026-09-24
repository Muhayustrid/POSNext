/**
 * COR-FE-02 regression: a saved draft only kept a slice of the cart (items,
 * customer, offers). The header additional discount and the applied coupon
 * were dropped, so reloading a draft lost its discount — while the live cart's
 * discount/coupon survived the switch and leaked into the next draft's
 * submission. The draft payload must round-trip both values, and legacy
 * drafts without the fields must load as "no discount", not as "keep
 * whatever the cart had".
 */
import { beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

const draftManager = vi.hoisted(() => ({
	saveDraft: vi.fn(async (data) => ({ draft_id: "DRAFT-1", ...data })),
	updateDraft: vi.fn(async (draftId, data) => ({ draft_id: draftId, ...data })),
	getAllDrafts: vi.fn(async () => []),
	getDraftsCount: vi.fn(async () => 0),
	deleteDraft: vi.fn(async () => {}),
}))

vi.mock("@/utils/draftManager", () => draftManager)

import { usePOSDraftsStore } from "./posDrafts"

const ITEMS = [
	{
		item_code: "ITEM-1",
		item_name: "Kopi Susu",
		quantity: 1,
		rate: 25000,
		uom: "Nos",
	},
]
const COUPON = { name: "DISKON10RB", code: "DISKON10RB", amount: 10000 }

beforeEach(() => {
	setActivePinia(createPinia())
	vi.clearAllMocks()
})

describe("posDrafts discount round-trip (COR-FE-02)", () => {
	it("stores the header discount and the coupon in the draft payload", async () => {
		const store = usePOSDraftsStore()

		await store.saveDraftInvoice(ITEMS, "CUST-1", "Budi", "Profile 1", [], null, COUPON, 10000)

		expect(draftManager.saveDraft).toHaveBeenCalledTimes(1)
		const payload = draftManager.saveDraft.mock.calls[0][0]
		expect(payload.applied_coupon).toEqual(COUPON)
		expect(payload.additional_discount).toBe(10000)
	})

	it("restores the header discount and the coupon when a draft is loaded", async () => {
		const store = usePOSDraftsStore()

		const data = await store.loadDraft({
			draft_id: "DRAFT-1",
			items: ITEMS,
			customer: "CUST-1",
			buyer_name: "Budi",
			applied_coupon: COUPON,
			additional_discount: 10000,
		})

		expect(data.applied_coupon).toEqual(COUPON)
		expect(data.additional_discount).toBe(10000)
	})

	it("loads legacy drafts without discount fields as an empty discount state", async () => {
		const store = usePOSDraftsStore()

		const data = await store.loadDraft({
			draft_id: "DRAFT-OLD",
			items: ITEMS,
			customer: "CUST-1",
			buyer_name: "",
		})

		expect(data.applied_coupon).toBeNull()
		expect(data.additional_discount).toBe(0)
	})
})
