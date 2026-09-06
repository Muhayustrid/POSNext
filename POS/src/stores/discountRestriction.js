import { call } from "@/utils/apiWrapper";
import { logger } from "@/utils/logger";
import { defineStore } from "pinia";
import { computed, ref } from "vue";

const log = logger.create("DiscountRestriction");

/**
 * Discount code gate store.
 *
 * Every manual discount (item-level or the cart-level additional discount)
 * requires a code issued by head office; codes stay usable until disabled.
 * This store holds the entered code and validates it live for UI feedback —
 * the server re-validates on every draft save and submit
 * (pos_next.overrides.discount_code), so a tampered client cannot bypass it.
 */
export const useDiscountRestrictionStore = defineStore("discountRestriction", () => {
	// State
	const status = ref({ enabled: true });
	const code = ref("");
	const isLoading = ref(false);
	const _company = ref("");

	// Getters
	const applicable = computed(() => status.value.enabled !== false);
	const hasCode = computed(() => Boolean((code.value || "").trim()));

	/**
	 * Offer attribution on a cart item (pricing_rules mirrored from the offers
	 * API — the same signal the invoice payload sends). Mirrors posCart's
	 * hasPricingRules: string or array.
	 */
	function hasOfferAttribution(item) {
		const rules = item.pricing_rules;
		if (!rules) return false;
		if (Array.isArray(rules)) return rules.length > 0;
		return typeof rules === "string" && rules.trim().length > 0;
	}

	/**
	 * Same discount semantics the server applies: explicit discount fields, or
	 * a manual rate edit below price_list_rate. Items carrying offer
	 * attribution are skipped — the server verifies their applied pricing
	 * rules and exempts offer-driven discounts from the code gate.
	 */
	function itemHasDiscount(item) {
		if (!item) return false;
		if (hasOfferAttribution(item)) return false;
		if (Number(item.discount_percentage) > 0 || Number(item.discount_amount) > 0) return true;
		if (
			Number(item.is_rate_manually_edited) &&
			Number(item.price_list_rate) > 0 &&
			Number(item.rate) < Number(item.price_list_rate)
		) {
			return true;
		}
		return false;
	}

	/**
	 * Whether checkout needs a code: an additional discount (hits the whole
	 * cart) or any discounted item does. An offer-sourced additional discount
	 * (transaction-scope POS Offer rule, applied by the server) is exempt;
	 * the server stays the authority — this is prompt-consistency only.
	 */
	function needsCodeForCart(additionalDiscount = 0, items = [], headerDiscountFromOffer = false) {
		if (!applicable.value) return false;
		if (Number(additionalDiscount) > 0 && !headerDiscountFromOffer) return true;
		return items.some((item) => itemHasDiscount(item));
	}

	// Actions
	async function fetchStatus(company) {
		if (!company) return;
		_company.value = company;
		isLoading.value = true;
		try {
			const result = await call("pos_next.api.discount_code.get_status", { company });
			status.value = result || { enabled: true };
		} catch (error) {
			// Status is a UX hint — the server gate still protects checkout.
			// Default to enabled so the POS keeps asking for the code.
			log.warn("Failed to load discount gate status", error);
			status.value = { enabled: true };
		} finally {
			isLoading.value = false;
		}
	}

	/**
	 * Live-validate the entered code against the cart about to be saved.
	 * Returns the server payload { valid, requires_code, message } without
	 * throwing.
	 */
	async function validateCode({ items = [], additionalDiscount = 0 } = {}) {
		const value = (code.value || "").trim();
		if (!value) {
			return { valid: false, requires_code: true, message: "Discount code is required" };
		}
		const discountedItems = items
			.filter((item) => itemHasDiscount(item))
			.map((item) => ({
				item_code: item.item_code,
				discount_percentage: item.discount_percentage || 0,
				discount_amount: item.discount_amount || 0,
				rate: item.rate || 0,
				price_list_rate: item.price_list_rate || 0,
				is_rate_manually_edited: item.is_rate_manually_edited || 0,
			}));
		try {
			return await call("pos_next.api.discount_code.validate_confirmation_code", {
				code: value,
				company: _company.value,
				items: JSON.stringify(discountedItems),
				additional_discount: additionalDiscount || 0,
			});
		} catch (error) {
			log.warn("Discount code validation failed", error);
			return {
				valid: false,
				requires_code: true,
				message: "Could not validate the discount code. Please try again.",
			};
		}
	}

	/**
	 * Validate the code VALUE alone — no cart context. Powers the
	 * locked-fields UX: the discount inputs stay disabled until this passes,
	 * so there is no discounted cart for validateCode to check yet. Returns
	 * the server payload { valid, message? } without throwing.
	 */
	async function checkCode() {
		const value = (code.value || "").trim();
		if (!value) {
			return { valid: false, requires_code: true, message: "Discount code is required" };
		}
		try {
			return await call("pos_next.api.discount_code.check_code", {
				code: value,
				company: _company.value,
			});
		} catch (error) {
			log.warn("Discount code check failed", error);
			return {
				valid: false,
				requires_code: true,
				message: "Could not validate the discount code. Please try again.",
			};
		}
	}

	function setCode(value) {
		code.value = (value || "").trim().toUpperCase();
	}

	function clearCode() {
		code.value = "";
	}

	function reset() {
		status.value = { enabled: true };
		_company.value = "";
		clearCode();
	}

	return {
		// State
		status,
		code,
		isLoading,
		// Getters
		applicable,
		hasCode,
		// Helpers
		itemHasDiscount,
		needsCodeForCart,
		// Actions
		fetchStatus,
		validateCode,
		checkCode,
		setCode,
		clearCode,
		reset,
	};
});
