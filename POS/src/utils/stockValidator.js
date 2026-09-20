/**
 * Stock Validation Utility
 * Single source of truth for stock availability checks.
 */

import { call } from "frappe-ui";

/**
 * Determine whether an item requires stock validation.
 * Centralises the skip-logic so every call site uses the same rules.
 *
 * @param {Object} item - Item object (from search API or cart)
 * @returns {boolean} true when stock should be enforced for this item
 */
export function shouldValidateItemStock(item) {
	if (!item) return false;

	// Non-stock items are never validated
	if (item.is_stock_item === 0 || item.is_stock_item === false) return false;

	// Item-level allow_negative_stock bypasses validation
	if (item.allow_negative_stock === 1 || item.allow_negative_stock === true) return false;

	// Batch / serial items have their own dialog-level validation
	if (item.has_serial_no || item.has_batch_no) return false;

	// Must be a stock item or bundle (or have stock data)
	const hasStockData = item.actual_qty !== undefined || item.stock_qty !== undefined;
	return !!(item.is_stock_item || item.is_bundle || hasStockData);
}

/**
 * The qty cart validation must compare against.
 *
 * `actual_qty` on items coming from filteredItems is DISPLAY stock (server
 * minus cart reservations), while the qty being validated already includes
 * the cart — comparing the two subtracts the cart twice and blocks sales
 * well below real stock. The stock store's server map holds the un-reserved
 * figure; fall back to original_stock (the injected server figure), then the
 * item's own fields for items the store has not registered.
 *
 * @param {Object} item - Item (may carry display stock in actual_qty)
 * @param {Map|null} serverMap - stockStore.server: item_code -> { qty }
 * @returns {number}
 */
export function validationStockQty(item, serverMap) {
	const fromStore = serverMap?.get(item.item_code)?.qty;
	if (fromStore !== undefined && fromStore !== null) return fromStore;
	return item.original_stock ?? item.actual_qty ?? item.stock_qty ?? 0;
}

/**
 * Check if the requested quantity exceeds available stock.
 *
 * @param {Object}  item       - Item with actual_qty / stock_qty
 * @param {number}  requestedQty - Total quantity to validate against
 * @param {string}  [warehouse]  - Warehouse name (for error message)
 * @param {number}  [availableQty] - Explicit stock figure; pass
 *   validationStockQty() so display stock on the item is not used as the base
 * @returns {{ available: boolean, actualQty: number, error: string|null }}
 */
export function checkStockAvailability(item, requestedQty, warehouse, availableQty) {
	const actualQty = availableQty ?? item.actual_qty ?? item.stock_qty ?? 0;
	const wh = warehouse || item.warehouse || "";

	if (actualQty >= requestedQty) {
		return { available: true, actualQty, error: null };
	}

	return {
		available: false,
		actualQty,
		error: formatStockError(item.item_name, requestedQty, actualQty, wh),
	};
}

/**
 * Get item stock from Frappe API
 * @param {string} itemCode - Item code
 * @param {string} warehouse - Warehouse
 * @returns {Promise<number>} - Available quantity
 */
export async function getItemStock(itemCode, warehouse) {
	try {
		const result = await call("frappe.client.get_value", {
			doctype: "Bin",
			filters: {
				item_code: itemCode,
				warehouse: warehouse,
			},
			fieldname: "actual_qty",
		});

		return Number.parseFloat(result?.actual_qty || 0);
	} catch (error) {
		console.warn("Failed to fetch stock:", error);
		return 0;
	}
}

/**
 * Format stock error message for user
 * @param {string} itemName - Item name
 * @param {number} requested - Requested quantity
 * @param {number} available - Available quantity
 * @param {string} warehouse - Warehouse name
 * @returns {string} - Formatted error message
 */
export function formatStockError(itemName, requested, available, warehouse) {
	if (available <= 0) {
		return __("\"{0}\" is out of stock in warehouse \"{1}\".", [itemName, warehouse]);
	}

	const unit = requested === 1 ? "unit" : "units";
	const availableUnit = available === 1 ? "unit" : "units";
	return __(
		"Not enough stock for \"{0}\".\n\nYou requested {1} {2}, but only {3} {4} available in \"{5}\".",
		[itemName, requested, unit, available, availableUnit, warehouse]
	);
}
