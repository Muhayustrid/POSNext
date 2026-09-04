import { call } from "@/utils/apiWrapper";
import { logger } from "@/utils/logger";
import { defineStore } from "pinia";
import { computed, ref } from "vue";

const log = logger.create("DiscountRestriction");

/**
 * Discount Restriction store
 *
 * Mirrors the active POS Discount Restriction rule for the shift's company so
 * the POS UI can prompt for an HQ confirmation code and warn about quota
 * before checkout. This is UX only — the server re-validates every rule on
 * draft save and submit (pos_next.overrides.discount_restriction).
 */
export const useDiscountRestrictionStore = defineStore("discountRestriction", () => {
	// State
	const status = ref({ applicable: false });
	const code = ref("");
	const isLoading = ref(false);
	const _company = ref("");

	// Getters
	const applicable = computed(() => Boolean(status.value.applicable));
	const activeRule = computed(() => (applicable.value ? status.value.rule : null));
	const requiresCode = computed(() => applicable.value && Boolean(status.value.requires_code));
	const enforceQuota = computed(() => applicable.value && Boolean(status.value.enforce_quota));
	const quotaExhausted = computed(() => applicable.value && Boolean(status.value.quota_exhausted));
	const quota = computed(() => (applicable.value ? status.value.quota : null));
	const codeItems = computed(() => (applicable.value ? status.value.code_items || [] : []));
	const hasCode = computed(() => Boolean((code.value || "").trim()));

	/**
	 * Whether discounting this item requires an HQ confirmation code.
	 * An empty code_items list on the rule means every item does.
	 */
	function needsCodeForItem(itemCode) {
		if (!requiresCode.value) return false;
		if (codeItems.value.length === 0) return true;
		return codeItems.value.includes(itemCode);
	}

	/**
	 * Same discount semantics the server applies: explicit discount fields, or
	 * a manual rate edit below price_list_rate.
	 */
	function itemHasDiscount(item) {
		if (!item) return false;
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
	 * cart) or any restricted item discount.
	 */
	function needsCodeForCart(additionalDiscount = 0, items = []) {
		if (!requiresCode.value) return false;
		if (Number(additionalDiscount) > 0) return true;
		return items.some((item) => itemHasDiscount(item) && needsCodeForItem(item.item_code));
	}

	// Actions
	async function fetchStatus(company) {
		if (!company) return;
		_company.value = company;
		isLoading.value = true;
		try {
			const result = await call("pos_next.api.discount_restriction.get_status", { company });
			status.value = result || { applicable: false };
		} catch (error) {
			// Status is a UX hint — the server gate still protects checkout.
			log.warn("Failed to load discount restriction status", error);
			status.value = { applicable: false };
		} finally {
			isLoading.value = false;
		}
	}

	/**
	 * Live-validate the entered code against the active rule. Returns the
	 * server payload { valid, message, requires_code } without throwing.
	 */
	async function validateCode({ items = [], additionalDiscount = 0 } = {}) {
		const value = (code.value || "").trim();
		if (!value) {
			return { valid: false, requires_code: true, message: "Confirmation code is required" };
		}
		const restrictedItems = items
			.filter((item) => itemHasDiscount(item) && needsCodeForItem(item.item_code))
			.map((item) => ({
				item_code: item.item_code,
				discount_percentage: item.discount_percentage || 0,
				discount_amount: item.discount_amount || 0,
				rate: item.rate || 0,
				price_list_rate: item.price_list_rate || 0,
				is_rate_manually_edited: item.is_rate_manually_edited || 0,
			}));
		try {
			return await call("pos_next.api.discount_restriction.validate_confirmation_code", {
				code: value,
				company: _company.value,
				items: JSON.stringify(restrictedItems),
				additional_discount: additionalDiscount || 0,
			});
		} catch (error) {
			log.warn("Confirmation code validation failed", error);
			return {
				valid: false,
				requires_code: true,
				message: "Could not validate the confirmation code. Please try again.",
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
		status.value = { applicable: false };
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
		activeRule,
		requiresCode,
		enforceQuota,
		quotaExhausted,
		quota,
		codeItems,
		hasCode,
		needsCodeForItem,
		itemHasDiscount,
		needsCodeForCart,
		// Actions
		fetchStatus,
		validateCode,
		setCode,
		clearCode,
		reset,
	};
});
