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
import { flushPromises } from "@vue/test-utils";
import { useToast } from "@/composables/useToast";
import { usePOSOffersStore } from "./posOffers";
import { offlineState } from "@/utils/offline/offlineState";

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

describe("coupon min_amount follows the cart", () => {
	it("removes the coupon when the basis drops below its minimum", async () => {
		const { clearAllToasts, toastQueue, toastNotification } = useToast();
		clearAllToasts();

		const store = usePOSCartStore();
		store.invoiceItems.push({
			item_code: "A",
			item_name: "A",
			quantity: 1,
			uom: "Nos",
			rate: 100000,
			price_list_rate: 100000,
			amount: 100000,
			discount_percentage: 0,
			discount_amount: 0,
		});
		store.rebuildIncrementalCache();

		store.applyDiscountToCart({
			name: "C1",
			code: "C1",
			apply_on: "Grand Total",
			coupon: {
				discount_type: "Percentage",
				discount_percentage: 10,
				min_amount: 50000,
				apply_on: "Grand Total",
			},
		});
		await nextTick();
		expect(store.appliedCoupon).not.toBeNull();

		// A manual 60% discount drops the basis to 40000, below the coupon's 50000.
		const item = store.invoiceItems[0];
		item.discount_percentage = 60;
		store.recalculateItem(item);
		store.rebuildIncrementalCache();
		await nextTick();

		expect(store.appliedCoupon).toBeNull();
		expect(store.additionalDiscount).toBe(0);
		const warned = [toastNotification.value, ...toastQueue.value].some((t) =>
			t?.message?.includes("minimum")
		);
		expect(warned).toBe(true);
	});
});

// A coupon occupies the header slot; whether an offer may sit beside it is the
// stacking matrix. The UI pre-locks: the cashier's deliberate action wins over
// automatic offers, and a forbidden stack is refused with a message.
function couponDiscount(allowOfferCombination) {
	return {
		name: "C1",
		code: "C1",
		apply_on: "Grand Total",
		coupon: {
			discount_type: "Amount",
			discount_amount: 100,
			apply_on: "Grand Total",
			allow_offer_combination: allowOfferCombination,
		},
	};
}

describe("coupon vs offer stacking locks", () => {
	it("coupon removes a non-combinable auto offer with a notice", async () => {
		const { clearAllToasts, toastQueue, toastNotification } = useToast();
		clearAllToasts();

		const store = usePOSCartStore();
		store.appliedOffers = [
			{
				code: "PR-1",
				name: "Offer 1",
				source: "auto",
				offer: { name: "PR-1", apply_on: "Item Code", allow_coupon_combination: 0 },
			},
		];

		store.applyDiscountToCart(couponDiscount(1));

		// The coupon wins; the auto offer is gone AND dismissed so the next
		// auto pass cannot put it back.
		expect(store.appliedCoupon).not.toBeNull();
		expect(store.appliedOffers).toEqual([]);
		expect(store.dismissedOfferCodes).toContain("PR-1");
		const warned = [toastNotification.value, ...toastQueue.value].some((t) =>
			t?.message?.includes("can't be combined")
		);
		expect(warned).toBe(true);
	});

	it("removes several non-combinable auto offers in one re-evaluation", async () => {
		const store = usePOSCartStore();
		const autoOffer = (code, allow) => ({
			code,
			name: code,
			source: "auto",
			offer: { name: code, apply_on: "Item Code", allow_coupon_combination: allow },
		});
		store.appliedOffers = [autoOffer("PR-1", 0), autoOffer("PR-2", 0), autoOffer("PR-3", 1)];

		store.applyDiscountToCart(couponDiscount(1));
		await flushPromises();

		// Per-offer removals would each re-send the other conflicting offer as
		// still applied; one call must keep only the combinable offer.
		const submit = applyOffersResource().submit;
		expect(submit).toHaveBeenCalledTimes(1);
		expect(submit.mock.calls[0][0].selected_offers).toEqual(["PR-3"]);
		expect(store.dismissedOfferCodes).toEqual(expect.arrayContaining(["PR-1", "PR-2"]));
	});

	it("refuses the coupon over a non-combinable cashier-applied offer", () => {
		const store = usePOSCartStore();
		store.appliedOffers = [
			{
				code: "PR-1",
				name: "Offer 1",
				source: "manual",
				offer: { name: "PR-1", apply_on: "Item Code", allow_coupon_combination: 0 },
			},
		];

		store.applyDiscountToCart(couponDiscount(1));

		// The cashier's manually applied offer stays; no coupon lands.
		expect(store.appliedCoupon).toBeNull();
		expect(store.appliedOffers).toHaveLength(1);
		expect(store.additionalDiscount).toBe(0);
	});

	it("allows the coupon when both flags are on", () => {
		const store = usePOSCartStore();
		store.appliedOffers = [
			{
				code: "PR-1",
				name: "Offer 1",
				source: "auto",
				offer: { name: "PR-1", apply_on: "Item Code", allow_coupon_combination: 1 },
			},
		];

		store.applyDiscountToCart(couponDiscount(1));

		// Both sides allowed the combination: the coupon lands and the offer stays.
		expect(store.appliedCoupon).not.toBeNull();
		expect(store.appliedOffers).toHaveLength(1);
	});

	it("auto-apply skips a non-combinable offer while a coupon is applied", async () => {
		const store = usePOSCartStore();
		const offersStore = usePOSOffersStore();
		store.posProfile = { company: "C", currency: "IDR" };
		store.invoiceItems.push({
			item_code: "A",
			item_name: "A",
			quantity: 1,
			uom: "Nos",
			rate: 1000,
			price_list_rate: 1000,
			amount: 1000,
			discount_percentage: 0,
			discount_amount: 0,
		});
		offersStore.availableOffers = [
			{
				name: "PR-1",
				title: "Offer 1",
				auto: 1,
				apply_on: "Item Code",
				eligible_items: ["A"],
				discount_type: "Discount Percentage",
				discount_percentage: 10,
				allow_coupon_combination: 0,
			},
		];
		offersStore.hasFetched = true;

		store.applyDiscountToCart(couponDiscount(1));
		store.forceRefreshOffers();
		await flushPromises();

		expect(store.appliedOffers).toEqual([]);
		expect(applyOffersResource().submit).not.toHaveBeenCalled();
	});

	it("auto-apply skips a transaction offer when a row has a manual discount", async () => {
		const store = usePOSCartStore();
		const offersStore = usePOSOffersStore();
		store.posProfile = { company: "C", currency: "IDR" };
		store.invoiceItems.push({
			item_code: "A",
			item_name: "A",
			quantity: 1,
			uom: "Nos",
			rate: 1000,
			price_list_rate: 1000,
			amount: 900,
			discount_percentage: 10,
			discount_amount: 100,
		});
		offersStore.availableOffers = [
			{
				name: "PR-T",
				title: "Txn Offer",
				auto: 1,
				apply_on: "Transaction",
				discount_type: "Discount Percentage",
				discount_percentage: 10,
				allow_coupon_combination: 0,
			},
		];
		offersStore.hasFetched = true;

		store.forceRefreshOffers();
		await flushPromises();

		expect(store.appliedOffers).toEqual([]);
		expect(applyOffersResource().submit).not.toHaveBeenCalled();
	});

	it("refuses a cashier transaction offer over a manual row discount", async () => {
		const store = usePOSCartStore();
		store.posProfile = { company: "C", currency: "IDR" };
		store.invoiceItems.push({
			item_code: "A",
			item_name: "A",
			quantity: 1,
			uom: "Nos",
			rate: 1000,
			price_list_rate: 1000,
			amount: 900,
			discount_percentage: 10,
			discount_amount: 100,
		});

		const result = await store.applyOffer(
			{ name: "PR-T", title: "Txn Offer", apply_on: "Transaction", discount_percentage: 10 },
			{ company: "C", currency: "IDR" }
		);

		// A transaction offer would stack on the row's manual discount: refused.
		expect(result).toBe(false);
		expect(store.appliedOffers).toEqual([]);
		expect(applyOffersResource().submit).not.toHaveBeenCalled();
	});

	it("offline auto-apply skips a non-combinable offer while a coupon is applied", async () => {
		const store = usePOSCartStore();
		const offersStore = usePOSOffersStore();
		offlineState.setManualOffline(true, { silent: true });
		try {
			store.posProfile = { company: "C", currency: "IDR" };
			store.invoiceItems.push({
				item_code: "A",
				item_name: "A",
				quantity: 1,
				uom: "Nos",
				rate: 1000,
				price_list_rate: 1000,
				amount: 1000,
				discount_percentage: 0,
				discount_amount: 0,
			});
			offersStore.availableOffers = [
				{
					name: "PR-1",
					title: "Offer 1",
					auto: 1,
					apply_on: "Item Code",
					eligible_items: ["A"],
					discount_type: "Discount Percentage",
					discount_percentage: 10,
					allow_coupon_combination: 0,
				},
			];
			offersStore.hasFetched = true;

			store.applyDiscountToCart(couponDiscount(1));
			store.forceRefreshOffers();
			await flushPromises();

			expect(store.appliedOffers).toEqual([]);
			expect(store.invoiceItems[0].discount_percentage).toBe(0);
		} finally {
			offlineState.setManualOffline(false, { silent: true });
		}
	});

	it("applies a transaction-offer header when no coupon is applied", async () => {
		const store = usePOSCartStore();
		store.posProfile = { company: "C", currency: "IDR" };
		store.invoiceItems.push({
			item_code: "A",
			item_name: "A",
			quantity: 1,
			uom: "Nos",
			rate: 1000,
			price_list_rate: 1000,
			amount: 1000,
		});
		store.rebuildIncrementalCache();
		store.appliedOffers = [
			{ code: "PR-1", name: "Offer 1", offer: { name: "PR-1" } },
			{ code: "PR-2", name: "Offer 2", offer: { name: "PR-2" } },
		];
		applyOffersResource().submit.mockResolvedValue({
			message: {
				items: [
					{
						item_code: "A",
						qty: 1,
						rate: 1000,
						price_list_rate: 1000,
						discount_percentage: 0,
						discount_amount: 0,
					},
				],
				applied_pricing_rules: ["PR-2"],
				discount_amount: 700,
				apply_discount_on: "Grand Total",
			},
		});

		await store.removeOffer("PR-1", { company: "C", currency: "IDR" });

		// Without a coupon the server's transaction-offer header still lands.
		expect(store.headerDiscountFromOffer).toBe(true);
		expect(store.additionalDiscount).toBe(700);
	});

	it("refuses a cashier offer over a non-combinable coupon", async () => {
		const store = usePOSCartStore();
		store.posProfile = { company: "C", currency: "IDR" };
		store.invoiceItems.push({
			item_code: "A",
			item_name: "A",
			quantity: 1,
			uom: "Nos",
			rate: 1000,
			price_list_rate: 1000,
			amount: 1000,
		});
		store.applyDiscountToCart(couponDiscount(0));
		expect(store.appliedCoupon).not.toBeNull();

		const result = await store.applyOffer(
			{ name: "PR-1", title: "Offer 1", apply_on: "Item Code", allow_coupon_combination: 0 },
			{ company: "C", currency: "IDR" }
		);

		expect(result).toBe(false);
		expect(store.appliedOffers).toEqual([]);
		expect(applyOffersResource().submit).not.toHaveBeenCalled();
	});

	it("does not apply a transaction-offer header while a coupon is applied", async () => {
		const store = usePOSCartStore();
		store.posProfile = { company: "C", currency: "IDR" };
		store.invoiceItems.push({
			item_code: "A",
			item_name: "A",
			quantity: 1,
			uom: "Nos",
			rate: 1000,
			price_list_rate: 1000,
			amount: 1000,
		});
		store.rebuildIncrementalCache();
		store.applyDiscountToCart({
			name: "C1",
			code: "C1",
			apply_on: "Grand Total",
			coupon: {
				discount_type: "Amount",
				discount_amount: 300,
				apply_on: "Grand Total",
				allow_offer_combination: 1,
			},
		});
		expect(store.additionalDiscount).toBe(300);

		store.appliedOffers = [
			{ code: "PR-1", name: "Offer 1", source: "manual", offer: { name: "PR-1" } },
			{ code: "PR-2", name: "Offer 2", source: "manual", offer: { name: "PR-2" } },
		];
		// The server still surfaces a transaction-offer header discount.
		applyOffersResource().submit.mockResolvedValue({
			message: {
				items: [
					{
						item_code: "A",
						qty: 1,
						rate: 1000,
						price_list_rate: 1000,
						discount_percentage: 0,
						discount_amount: 0,
					},
				],
				applied_pricing_rules: ["PR-2"],
				discount_amount: 700,
				apply_discount_on: "Grand Total",
			},
		});

		await store.removeOffer("PR-1", { company: "C", currency: "IDR" });

		// The coupon's header discount is not overwritten by the offer's.
		expect(store.headerDiscountFromOffer).toBe(false);
		expect(store.additionalDiscount).toBe(300);
	});
});
