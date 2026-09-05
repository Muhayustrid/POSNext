/**
 * Resolve the per-unit discount a percentage offer actually grants, honouring
 * the POS Offer nominal cap (max_discount_amount):
 *   effective per-unit discount = min(baseRate * pct/100, cap)
 *
 * When the cap binds, the result is an EFFECTIVE PERCENTAGE of the unit price
 * ((cap / base) * 100) rather than a flat amount. Per unit the two are
 * identical, but a percentage rescales with quantity — the cart store's
 * discount_amount is a line-total field and would under-charge for qty > 1
 * if the per-unit cap were written there. Mirrors the server clamp in
 * pos_next/overrides/pricing_rule._cap_percentage_discount.
 *
 * @param {{discount_percentage?: number|string, max_discount_amount?: number|string}} offer
 * @param {number|string} baseRate item price_list_rate per unit
 * @returns {{type: "percentage", value: number, capped: boolean}}
 */
export function resolveOfferUnitDiscount(offer, baseRate) {
	const pct = Number.parseFloat(offer?.discount_percentage) || 0
	const cap = Number.parseFloat(offer?.max_discount_amount) || 0
	const base = Number.parseFloat(baseRate) || 0

	if (pct > 0 && cap > 0 && base > 0 && (base * pct) / 100 > cap) {
		return { type: "percentage", value: (cap / base) * 100, capped: true }
	}
	return { type: "percentage", value: pct, capped: false }
}
