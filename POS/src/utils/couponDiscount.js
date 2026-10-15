/**
 * Client-side mirror of the server coupon formula
 * (pos_next/.../doctype/pos_coupon/pos_coupon.py: expected_coupon_discount and
 * coupon_min_amount_unmet). Keeping one formula on both sides lets the header
 * discount follow the cart — a later item discount shrinks the coupon amount
 * instead of leaving the apply-time amount frozen.
 *
 * `coupon` is the POS Coupon definition: {discount_type, discount_percentage,
 * discount_amount, max_amount, min_amount, apply_on}. `bases` are the totals
 * BEFORE the header discount: {grand, net}. Rounding stays with the caller
 * (roundCurrency), matching the server's flt() at the edge.
 */

/** Pre-header base the coupon is defined against (its own apply_on basis). */
function couponBasis(coupon, bases) {
	return coupon.apply_on === "Net Total" ? bases.net : bases.grand
}

/**
 * The header discount `coupon` promises against the current bases.
 * An unknown discount_type grants nothing.
 *
 * @param {Object} coupon POS Coupon definition
 * @param {{grand: number, net: number}} bases pre-header-discount totals
 * @returns {number}
 */
export function expectedCouponDiscount(coupon, bases) {
	if (!coupon) return 0

	const basis = couponBasis(coupon, bases)

	let expected
	if (coupon.discount_type === "Percentage") {
		expected =
			(basis * (Number.parseFloat(coupon.discount_percentage) || 0)) / 100
	} else if (coupon.discount_type === "Amount") {
		expected = Number.parseFloat(coupon.discount_amount) || 0
	} else {
		return 0
	}

	// Apply the maximum discount limit, then never exceed the basis itself.
	const maxAmount = Number.parseFloat(coupon.max_amount) || 0
	if (maxAmount > 0) expected = Math.min(expected, maxAmount)
	if (expected > basis) expected = basis
	return Math.max(expected, 0)
}

/**
 * True when the coupon's minimum spend is not met on its own basis.
 *
 * @param {Object} coupon POS Coupon definition
 * @param {{grand: number, net: number}} bases pre-header-discount totals
 * @returns {boolean}
 */
export function couponMinAmountUnmet(coupon, bases) {
	if (!coupon) return false
	const minAmount = Number.parseFloat(coupon.min_amount) || 0
	if (minAmount <= 0) return false
	return couponBasis(coupon, bases) < minAmount
}
