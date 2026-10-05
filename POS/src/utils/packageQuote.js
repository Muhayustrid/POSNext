/**
 * @fileoverview Client-side POS Package quoting.
 *
 * Mirrors `pos_next/api/packages.py:quote()` so packages can be selected and
 * priced while offline. The server re-quotes every package on Sales Invoice
 * validate, so this result is a preview — never the authority.
 *
 * Keep this file and `packages.py` in lockstep: same validation order, same
 * price formula `base_price + Σ(price_adjustment × qty)`, and — when the
 * caller passes `allocate` — the same proportional split of the total across
 * the component lines with the `allocation` snapshot marker.
 *
 * @module packageQuote
 */

import {
	ALLOCATION_MODE,
	allocatePackageRates,
	componentPriceListRate,
} from "@/utils/packageAllocation";
import { getPrecision, roundCurrency } from "@/utils/currency";

export const PACKAGE_ROLE = "Package";
export const PACKAGE_ITEM_ROLE = "Package Item";

/**
 * True when a package snapshot records an allocation-mode quote — the same
 * marker `packages.py:quote()` writes. Accepts both shapes a snapshot travels
 * in: the object on a fresh quote, the JSON string on a cart/draft/server row.
 *
 * @param {Object|string|null} snapshot
 * @returns {boolean}
 */
export function isAllocationSnapshot(snapshot) {
	if (!snapshot) return false;
	if (typeof snapshot === "object") return snapshot.allocation?.mode === ALLOCATION_MODE;
	try {
		return JSON.parse(snapshot)?.allocation?.mode === ALLOCATION_MODE;
	} catch {
		return false;
	}
}

/**
 * Total units picked in a group.
 * @param {Object<string, number>} picks - option_id -> qty
 * @returns {number}
 */
export function pickedQty(picks) {
	return Object.values(picks || {}).reduce((sum, qty) => sum + (Number(qty) || 0), 0);
}

/**
 * Validate a group's picks against its min/max and per-option caps.
 * @param {Object} group - POS Package Group
 * @param {Array<Object>} options - Options belonging to the group
 * @param {Object<string, number>} picks - option_id -> qty
 * @returns {string|null} Error message, or null when valid
 */
export function validateGroup(group, options, picks) {
	const total = pickedQty(picks);
	const minQty = Number(group.min_qty) || 0;
	const maxQty = Number(group.max_qty) || 0;

	if (total < minQty) {
		return __("Choose at least {0} item(s) from {1}.", [minQty, group.label]);
	}
	if (total > maxQty) {
		return __("Choose at most {0} item(s) from {1}.", [maxQty, group.label]);
	}

	for (const [optionId, qty] of Object.entries(picks || {})) {
		if (!qty) continue;
		const option = options.find((o) => o.option_id === optionId);
		if (!option) {
			return __("Option {0} does not belong to {1}.", [optionId, group.label]);
		}
		const optionMax = Number(option.max_qty) || 0;
		if (optionMax && qty > optionMax) {
			return __("You can pick at most {0} x {1}.", [
				optionMax,
				option.item_name || option.item_code,
			]);
		}
	}

	return null;
}

/**
 * Price a package selection locally.
 *
 * With `allocate` on, the total is split across the component lines exactly
 * like the server (`allocate_package_rates`): weights come from each
 * component's price-list rate — supplied by the caller as
 * `priceListRates[item_code] = { [uom]: rate }`, mirroring the server's
 * price-list lookup — falling back to quantity weights when the cache carries
 * no price, and the snapshot gets the same `allocation` marker. Off (the
 * default) the result stays the legacy shape: full price on the parent,
 * components at 0, no marker.
 *
 * @param {Object} pkg - Package definition from `pos_next.api.packages.get_packages`
 * @param {Object<string, Object<string, number>>} selections - group_key -> option_id -> qty
 * @param {Object} [options]
 * @param {boolean} [options.allocate=false] - Apply proportional allocation
 * @param {Object} [options.priceListRates=null] - item_code -> uom -> price-list rate
 * @returns {{valid: boolean, error: string|null, total: number, lines: Array<Object>, snapshot: Object}}
 */
export function quotePackageLocally(
	pkg,
	selections = {},
	{ allocate = false, priceListRates = null } = {}
) {
	const invalid = (error) => ({ valid: false, error, total: 0, lines: [], snapshot: null });

	if (!pkg) return invalid(__("Package not found."));

	let total = Number(pkg.base_price) || 0;
	const componentLines = [];
	const snapshotSelections = [];

	for (const group of pkg.groups || []) {
		const picks = selections[group.group_key] || {};
		const options = (pkg.options || []).filter((o) => o.group_key === group.group_key);

		const error = validateGroup(group, options, picks);
		if (error) return invalid(error);

		for (const [optionId, rawQty] of Object.entries(picks)) {
			const qty = Number(rawQty) || 0;
			if (!qty) continue;

			const option = options.find((o) => o.option_id === optionId);
			total += (Number(option.price_adjustment) || 0) * qty;

			componentLines.push({
				item_code: option.item_code,
				item_name: option.item_name,
				qty: (Number(option.qty_per_unit) || 1) * qty,
				uom: option.uom,
				rate: 0,
				role: PACKAGE_ITEM_ROLE,
				is_stock_item: option.is_stock_item,
			});
			snapshotSelections.push({
				group_key: group.group_key,
				group_label: group.label,
				option_id: optionId,
				item_code: option.item_code,
				item_name: option.item_name,
				qty,
				price_adjustment: Number(option.price_adjustment) || 0,
			});
		}
	}

	for (const row of pkg.items || []) {
		componentLines.push({
			item_code: row.item_code,
			item_name: row.item_name,
			qty: Number(row.qty) || 0,
			uom: row.uom,
			rate: 0,
			role: PACKAGE_ITEM_ROLE,
			is_stock_item: row.is_stock_item,
		});
	}

	if (total < 0) return invalid(__("Package price cannot be negative."));

	total = roundCurrency(total);

	// With no component rows there is nowhere to move the money; the parent
	// keeps it and the snapshot stays legacy-shaped (no allocation marker).
	const allocationApplied = Boolean(allocate && componentLines.length);
	const allocationPrecision = getPrecision().currency;
	let parentRate = total;
	if (allocationApplied) {
		const rates = allocatePackageRates(
			total,
			componentLines.map((line) => ({
				qty_per_package: line.qty,
				price_list_rate: componentPriceListRate(
					priceListRates ? priceListRates[line.item_code] : null,
					line.uom
				),
			})),
			1,
			allocationPrecision
		);
		componentLines.forEach((line, index) => {
			line.rate = rates[index];
		});
		parentRate = 0;
	}

	const parentLine = {
		item_code: pkg.parent_item,
		item_name: pkg.package_name,
		qty: 1,
		rate: parentRate,
		role: PACKAGE_ROLE,
	};

	const snapshot = {
		package: pkg.name,
		package_name: pkg.package_name,
		base_price: Number(pkg.base_price) || 0,
		total,
		selections: snapshotSelections,
		included_items: (pkg.items || []).map((row) => ({
			item_code: row.item_code,
			item_name: row.item_name,
			qty: Number(row.qty) || 0,
		})),
	};
	if (allocationApplied) {
		snapshot.allocation = { mode: ALLOCATION_MODE, precision: allocationPrecision };
	}

	return {
		valid: true,
		error: null,
		total,
		lines: [parentLine, ...componentLines],
		snapshot,
	};
}

/**
 * Convert the dialog's selection map into the API's `choices` payload.
 * @param {Object<string, Object<string, number>>} selections
 * @returns {Array<{group_key: string, options: Array<{option_id: string, qty: number}>}>}
 */
export function selectionsToChoices(selections = {}) {
	return Object.entries(selections).map(([groupKey, picks]) => ({
		group_key: groupKey,
		options: Object.entries(picks || {})
			.filter(([, qty]) => Number(qty) > 0)
			.map(([optionId, qty]) => ({ option_id: optionId, qty: Number(qty) })),
	}));
}
