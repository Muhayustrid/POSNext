/**
 * @fileoverview Client-side mirror of `pos_next/api/packages.py:allocate_package_rates()`.
 *
 * Splits a package price across its component lines proportionally to each
 * component's POS price-list value, with the rounding remainder carried by
 * the qty-1 line with the largest weight above 0 (the last such line on ties)
 * so the sum matches the package price exactly; a qty-1 line with no weight
 * must never carry it (its share is 0, so the leftover would book negative).
 * Without an eligible line the last line carries it and the server validates
 * the sum (see `allocatePackageRates`). Pure: no
 * store, no IndexedDB — the caller supplies the component price-list rates.
 *
 * Rounding mirrors `frappe.utils.flt(value, precision)` through
 * `currency.roundToPrecision()`, the repo's established JS mirror of
 * `frappe.utils.data.rounded()`. A site set to "Banker's Rounding (legacy)"
 * rounds an exact .5 tie half-up for precision > 0, while this mirror rounds
 * half-to-even ("Banker's Rounding"); at precision 0 — the IDR rate precision
 * this mirror serves — the two agree. The server re-quotes every package on
 * Sales Invoice validate, so a preview differing on an exact tie can never
 * change the charged amount.
 *
 * @module packageAllocation
 */

import { roundToPrecision } from "@/utils/currency";

export const ALLOCATION_MODE = "proportional";

/** frappe.utils.flt: numeric coercion, falling back to 0. */
function flt(value) {
	const num = Number(value);
	return Number.isFinite(num) ? num : 0;
}

/**
 * Split a package price across its component lines, proportionally.
 *
 * `children` is a list of `{qty_per_package, price_list_rate}`: each
 * component's quantity in one package and its current POS price-list rate.
 * A component's weight is `price_list_rate * qty_per_package`, so the package
 * price is distributed by value; when *no* component carries a price the split
 * falls back to quantity weights, then to an even split, logging a warning
 * instead of failing. Returns one per-unit rate per child, in input order. The
 * rounding remainder is carried by the last child whose per-unit line quantity
 * (`qty_per_package * package_qty`) is exactly 1 AND whose weight is above 0,
 * so that
 *
 *     sum(rate_i * qty_per_package_i * package_qty) == package_price * package_qty
 *
 * holds whenever such a child exists (one unit divides any remainder exactly,
 * and a positive weight means its own share dominates the leftover). Without
 * one the remainder lands on the last child instead and the sum can
 * differ from the package price when it does not divide that child's quantity —
 * the server validates the sum and fails closed, and POS Package rejects
 * definitions that can never offer such a line.
 *
 * @param {number} packagePrice - Package total for one package
 * @param {Array<{qty_per_package: number, price_list_rate: number}>} children
 * @param {number} [packageQty=1] - Number of packages quoted
 * @param {number} [precision=0] - Currency precision to round each rate at
 * @returns {Array<number>} One per-unit rate per child, in input order
 */
export function allocatePackageRates(packagePrice, children, packageQty = 1, precision = 0) {
	const list = Array.isArray(children) ? children : [];
	if (!list.length) return [];

	const price = flt(packagePrice);
	const quantity = flt(packageQty) || 1;
	const quantities = list.map((child) => flt(child?.qty_per_package));

	let weights = list.map(
		(child, index) => flt(child?.price_list_rate) * quantities[index]
	);
	if (!weights.some((weight) => weight)) {
		weights = [...quantities];
		if (quantities.some((qty) => qty)) {
			console.warn(
				"POS package allocation: no component has a price-list rate; using quantity weights."
			);
		}
	}
	if (!weights.some((weight) => weight)) {
		weights = list.map(() => 1);
		console.warn(
			"POS package allocation: no component has a price-list rate or quantity; splitting evenly."
		);
	}

	const totalWeight = weights.reduce((sum, weight) => sum + weight, 0);
	const totalMoney = price * quantity;

	// Remainder carrier: the last qty-1 line whose weight is above 0. Without
	// an eligible line, keep the previous behaviour and let the last child try.
	let carrier = list.length - 1;
	let carrierWeight = 0;
	for (let index = list.length - 1; index >= 0; index--) {
		if (quantities[index] * quantity === 1 && weights[index] > carrierWeight) {
			carrier = index;
			carrierWeight = weights[index];
		}
	}

	const rates = [];
	let allocated = 0;

	for (let index = 0; index < list.length; index++) {
		const qty = quantities[index];
		const lineQty = qty * quantity;
		let rate;
		if (index === carrier) {
			rate = 0; // filled in below, once every other line is rounded
		} else if (!lineQty) {
			rate = 0;
		} else {
			rate = totalWeight ? ((weights[index] / totalWeight) * totalMoney) / lineQty : 0;
		}
		rate = roundToPrecision(rate, precision);
		rates.push(rate);
		if (index !== carrier) allocated += rate * lineQty;
	}

	const carrierLineQty = quantities[carrier] * quantity;
	if (carrierLineQty) {
		// Never book a negative component rate; the server sum check fails
		// closed instead (mirrors packages.py).
		rates[carrier] = Math.max(
			roundToPrecision((totalMoney - allocated) / carrierLineQty, precision),
			0
		);
	}

	return rates;
}

/**
 * Per-line price-list rate from an item's `uom_prices` map, mirroring the
 * fallback order of `packages.py:_component_price_list_rates`: the row's UOM,
 * then the no-UOM price, then — when the item carries exactly one price — it.
 * Unknown prices resolve to 0, routing the quote to the weight fallback.
 *
 * @param {Object<string, number>} uomPrices - item.uom_prices from the cache
 * @param {string|null} uom - Component row UOM
 * @returns {number}
 */
export function componentPriceListRate(uomPrices, uom) {
	const prices = uomPrices || {};
	let rate = uom ? prices[uom] : prices[""];
	if (rate == null) rate = prices[""];
	if (rate == null) {
		const keys = Object.keys(prices);
		if (keys.length === 1) rate = prices[keys[0]];
	}
	return rate == null ? 0 : flt(rate);
}
