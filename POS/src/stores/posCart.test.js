/**
 * @vitest-environment jsdom
 *
 * Offer-handling regressions in the POS cart store:
 *  - BUG A: transaction-scope offers were evaluated against rows a cashier had
 *    ALREADY discounted manually (buildOfferEvaluationPayload sent the list rate),
 *    so the server discounted money the customer no longer paid.
 *  - BUG B: when one of several offers was removed, a row that only carried the
 *    removed offer's discount kept that stale discount (applyDiscountsFromServer
 *    "preserved" it as if it were a manual one).
 *  - BUG C: dismissedOfferCodes (offers the cashier switched off) leaked past the
 *    cart that produced them — surviving an incremental empty and a draft load.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { nextTick } from "vue";

const resources = vi.hoisted(() => ({ instances: [] }));

vi.mock("frappe-ui", async () => {
	const vue = await import("vue");
	const createResource = (opts) => {
		const r = vue.reactive({
			loading: false,
			data: null,
			error: null,
			reload: vi.fn(),
			fetch: vi.fn(),
			submit: vi.fn(async () => ({})),
			reset: vi.fn(),
			update: vi.fn(),
		});
		resources.instances.push({ url: opts?.url, opts, resource: r });
		return r;
	};
	return { createResource, call: vi.fn(async () => ({})) };
});

import { usePOSCartStore } from "./posCart";

function applyOffersResource() {
	return resources.instances.find((r) => r.url === "pos_next.api.invoices.apply_offers")
		.resource;
}

beforeEach(() => {
	setActivePinia(createPinia());
	resources.instances.length = 0;
});

describe("buildOfferEvaluationPayload (BUG A)", () => {
	it("sends the effective per-unit rate for a manually discounted row", () => {
		const store = usePOSCartStore();
		// A: cashier discounted manually 100% (rate stays = list, amount 0).
		// B: untouched list-price row.
		store.invoiceItems.push(
			{
				item_code: "A",
				item_name: "A",
				quantity: 1,
				qty: 1,
				uom: "Nos",
				rate: 12000,
				price_list_rate: 12000,
				amount: 0,
				discount_percentage: 100,
				discount_amount: 12000,
				is_free_item: 0,
			},
			{
				item_code: "B",
				item_name: "B",
				quantity: 1,
				qty: 1,
				uom: "Nos",
				rate: 10000,
				price_list_rate: 10000,
				amount: 10000,
				discount_percentage: 0,
				discount_amount: 0,
				is_free_item: 0,
			}
		);

		const payload = store.buildOfferEvaluationPayload({ company: "C" });

		// Effective rate after the manual discount: the server's transaction-offer
		// basis must be B only (10000), not 12000 + 10000.
		expect(payload.items[0].rate).toBe(0);
		expect(payload.items[0].price_list_rate).toBe(12000);
		expect(payload.items[1].rate).toBe(10000);
	});

	it("keeps the undiscounted rate for rows discounted by an offer", () => {
		const store = usePOSCartStore();
		store.invoiceItems.push({
			item_code: "A",
			item_name: "A",
			quantity: 1,
			qty: 1,
			uom: "Nos",
			rate: 12000,
			price_list_rate: 12000,
			amount: 10800,
			discount_percentage: 10,
			discount_amount: 1200,
			pricing_rules: ["PR-1"],
			is_free_item: 0,
		});

		const payload = store.buildOfferEvaluationPayload({ company: "C" });

		// The server recomputes the offer discount from the list price each call.
		expect(payload.items[0].rate).toBe(12000);
	});

	it("leaves free item rows at rate 0", () => {
		const store = usePOSCartStore();
		store.invoiceItems.push({
			item_code: "GIFT",
			item_name: "GIFT",
			quantity: 1,
			qty: 1,
			uom: "Nos",
			rate: 0,
			price_list_rate: 0,
			amount: 0,
			is_free_item: 1,
		});

		const payload = store.buildOfferEvaluationPayload({ company: "C" });

		expect(payload.items[0].rate).toBe(0);
		expect(payload.items[0].price_list_rate).toBe(0);
	});
});

describe("applyDiscountsFromServer via removeOffer (BUG B)", () => {
	it("clears a stale offer discount the server no longer returns", async () => {
		const store = usePOSCartStore();
		const item = {
			item_code: "A",
			item_name: "A",
			quantity: 1,
			qty: 1,
			uom: "Nos",
			rate: 12000,
			price_list_rate: 12000,
			amount: 10800,
			discount_percentage: 10,
			discount_amount: 1200,
			pricing_rules: ["PR-1"],
			is_free_item: 0,
		};
		store.invoiceItems.push(item);
		store.appliedOffers = [
			{ code: "PR-1", name: "Offer 1" },
			{ code: "PR-2", name: "Offer 2" },
		];

		// Remove PR-1; server confirms only PR-2 remains and returns the row
		// WITHOUT any pricing rule or discount.
		applyOffersResource().submit.mockResolvedValue({
			message: {
				items: [
					{
						item_code: "A",
						qty: 1,
						rate: 12000,
						price_list_rate: 12000,
						discount_percentage: 0,
						discount_amount: 0,
					},
				],
				applied_pricing_rules: ["PR-2"],
			},
		});

		await store.removeOffer("PR-1", { company: "C" });

		expect(item.pricing_rules).toEqual([]);
		expect(item.discount_percentage).toBe(0);
		expect(item.discount_amount).toBe(0);
	});

	it("keeps a truly manual discount when the server returns no offer", async () => {
		const store = usePOSCartStore();
		const item = {
			item_code: "A",
			item_name: "A",
			quantity: 1,
			qty: 1,
			uom: "Nos",
			rate: 12000,
			price_list_rate: 12000,
			amount: 10800,
			discount_percentage: 10,
			discount_amount: 1200,
			is_free_item: 0,
		};
		store.invoiceItems.push(item);
		store.appliedOffers = [
			{ code: "PR-1", name: "Offer 1" },
			{ code: "PR-2", name: "Offer 2" },
		];

		applyOffersResource().submit.mockResolvedValue({
			message: {
				items: [
					{
						item_code: "A",
						qty: 1,
						rate: 12000,
						price_list_rate: 12000,
						discount_percentage: 0,
						discount_amount: 0,
					},
				],
				applied_pricing_rules: ["PR-2"],
			},
		});

		await store.removeOffer("PR-1", { company: "C" });

		expect(item.discount_percentage).toBe(10);
		expect(item.discount_amount).toBe(1200);
	});
});

describe("manual discount blocks item-level offer (per-line rule)", () => {
	it("keeps a manual discount and does not take the server's offer for that row", async () => {
		const store = usePOSCartStore();
		// Row A carries a cashier-entered manual discount (no offer attribution).
		const item = {
			item_code: "A",
			item_name: "A",
			quantity: 1,
			qty: 1,
			uom: "Nos",
			rate: 12000,
			price_list_rate: 12000,
			amount: 10800,
			discount_percentage: 10,
			discount_amount: 1200,
			is_free_item: 0,
		};
		store.invoiceItems.push(item);
		store.appliedOffers = [];
		store.posProfile = { company: "C" };

		// The server (still) answers with an offer discount for that row.
		applyOffersResource().submit.mockResolvedValue({
			message: {
				items: [
					{
						item_code: "A",
						qty: 1,
						rate: 12000,
						price_list_rate: 12000,
						discount_percentage: 20,
						discount_amount: 2400,
						pricing_rules: ["PR-1"],
					},
				],
				applied_pricing_rules: ["PR-1"],
			},
		});

		await store.applyOffer({ name: "PR-1", title: "Offer 1" }, { company: "C" });

		// The manual discount wins; the offer discount is never stacked on it.
		expect(item.discount_percentage).toBe(10);
		expect(item.discount_amount).toBe(1200);
		expect(item.pricing_rules).toBeFalsy();
	});
});

describe("coupon cannot stack on a transaction-offer header", () => {
	it("refuses the coupon while the header discount comes from an offer", () => {
		const store = usePOSCartStore();
		store.headerDiscountFromOffer = true;

		store.applyDiscountToCart({ name: "COUPON1", code: "COUPON1", amount: 5000 });

		// The offer-sourced header discount stays untouched and no coupon lands.
		expect(store.additionalDiscount).toBe(0);
		expect(store.appliedCoupon).toBeNull();
		expect(store.headerDiscountFromOffer).toBe(true);
	});
});

describe("dismissedOfferCodes lifetime (BUG C)", () => {
	it("clears the dismissed list when the cart empties by removing items", async () => {
		const store = usePOSCartStore();
		store.invoiceItems.push({
			item_code: "A",
			quantity: 1,
			uom: "Nos",
			rate: 1000,
			price_list_rate: 1000,
			amount: 1000,
		});
		store.dismissedOfferCodes = ["PR-1"];
		await nextTick();

		store.removeItem("A");
		await nextTick();

		expect(store.invoiceItems.length).toBe(0);
		expect(store.dismissedOfferCodes).toEqual([]);
	});

	it("clears the dismissed list on resetDiscounts (draft load / cart replacement)", () => {
		const store = usePOSCartStore();
		store.dismissedOfferCodes = ["PR-1"];

		store.resetDiscounts();

		expect(store.dismissedOfferCodes).toEqual([]);
	});

	it("re-enables a dismissed offer when the cashier switches it back on", async () => {
		const store = usePOSCartStore();
		store.posProfile = { company: "C" };
		store.invoiceItems.push({
			item_code: "A",
			quantity: 1,
			uom: "Nos",
			rate: 1000,
			price_list_rate: 1000,
			amount: 1000,
		});
		store.dismissedOfferCodes = ["PR-1"];
		applyOffersResource().submit.mockResolvedValue({
			message: {
				items: [
					{
						item_code: "A",
						qty: 1,
						rate: 1000,
						price_list_rate: 1000,
						discount_percentage: 10,
						discount_amount: 100,
						pricing_rules: ["PR-1"],
					},
				],
				applied_pricing_rules: ["PR-1"],
			},
		});

		await store.applyOffer({ name: "PR-1", title: "Offer 1" }, { company: "C" });

		expect(store.dismissedOfferCodes).toEqual([]);
	});
});
