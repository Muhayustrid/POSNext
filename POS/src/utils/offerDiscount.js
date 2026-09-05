/**
 * Resolve the per-unit discount a percentage offer actually grants, honouring
 * the POS Offer nominal cap (max_discount_amount):
 *   effective per-unit discount = min(baseRate * pct/100, cap)
 * Mirrors the server clamp in pos_next/overrides/pricing_rule._cap_percentage_discount.
 *
 * @param {{discount_percentage?: number|string, max_discount_amount?: number|string}} offer
 * @param {number|string} baseRate item price_list_rate per unit
 * @returns {{type: "percentage"|"amount", value: number, capped: boolean}}
 */
export function resolveOfferUnitDiscount(offer, baseRate) {
	const pct = Number.parseFloat(offer?.discount_percentage) || 0
	const cap = Number.parseFloat(offer?.max_discount_amount) || 0
	const base = Number.parseFloat(baseRate) || 0

	if (pct > 0 && cap > 0 && base > 0 && (base * pct) / 100 > cap) {
		return { type: "amount", value: cap, capped: true }
	}
	return { type: "percentage", value: pct, capped: false }
}
