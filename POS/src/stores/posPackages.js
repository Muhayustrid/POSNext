/**
 * @fileoverview POS Package ("Paket") store.
 *
 * Loads package definitions once per shift, caches them in IndexedDB, and
 * resolves a tapped catalog item to the package it represents.
 *
 * Quoting is server-authoritative when online. Offline it falls back to
 * `packageQuote.js`, which mirrors the Python implementation; either way the
 * server re-quotes on Sales Invoice validate.
 *
 * @module stores/posPackages
 */

import { call } from "@/utils/apiWrapper";
import { logger } from "@/utils/logger";
import { getCachedItem, isOffline } from "@/utils/offline";
import { offlineWorker } from "@/utils/offline/workerClient";
import { quotePackageLocally, selectionsToChoices } from "@/utils/packageQuote";
import { defineStore } from "pinia";
import { computed, ref } from "vue";
import { usePOSSettingsStore } from "./posSettings";

const log = logger.create("POSPackages");

export const usePOSPackagesStore = defineStore("posPackages", () => {
	const packages = ref([]);
	const fetchedProfile = ref(null);
	const isLoading = ref(false);

	let fetchPromise = null;

	/** Package definitions keyed by their parent item_code. */
	const packagesByParentItem = computed(() => {
		const map = new Map();
		for (const pkg of packages.value) {
			map.set(pkg.parent_item, pkg);
		}
		return map;
	});

	/** Item codes that open the package dialog instead of being added directly. */
	const packageItemCodes = computed(() => new Set(packagesByParentItem.value.keys()));

	function isPackageItem(itemCode) {
		return packageItemCodes.value.has(itemCode);
	}

	function getPackageForItem(itemCode) {
		return packagesByParentItem.value.get(itemCode) || null;
	}

	function setPackages(list = []) {
		packages.value = Array.isArray(list) ? list : [];
	}

	function clearPackages() {
		packages.value = [];
		fetchedProfile.value = null;
		fetchPromise = null;
	}

	/**
	 * Load packages for a profile, from cache when offline.
	 * Concurrent calls share one in-flight request.
	 *
	 * @param {string} posProfile - POS Profile name
	 * @param {boolean} force - Refetch even if already loaded
	 * @returns {Promise<boolean>} True when packages are available
	 */
	async function ensurePackagesFetched(posProfile, force = false) {
		if (!posProfile) return false;

		// Keyed by profile: a shift switch must not keep showing the previous
		// outlet's packages, which the server would then reject at checkout.
		if (fetchedProfile.value === posProfile && !force) return packages.value.length > 0;
		if (fetchPromise) return fetchPromise;

		isLoading.value = true;
		fetchPromise = (async () => {
			try {
				if (isOffline()) {
					const cached = await offlineWorker.getCachedPackages(posProfile);
					setPackages(cached || []);
					fetchedProfile.value = posProfile;
					return packages.value.length > 0;
				}

				const response = await call("pos_next.api.packages.get_packages", {
					pos_profile: posProfile,
				});
				const list = response?.message || response || [];
				setPackages(list);
				fetchedProfile.value = posProfile;

				offlineWorker.cachePackages(list, posProfile).catch((error) => {
					log.warn("Failed to cache packages for offline use", error);
				});

				return list.length > 0;
			} catch (error) {
				log.error("Failed to load packages", error);
				// Do not cache the failure against this profile: a retry must be
				// able to load the real list instead of showing an empty catalog.
				setPackages([]);
				return false;
			} finally {
				isLoading.value = false;
				fetchPromise = null;
			}
		})();

		return fetchPromise;
	}

	/**
	 * Price-list rates for every component the package can place, keyed
	 * item_code -> { uom: rate } — the offline stand-in for the server's
	 * `_component_price_list_rates` lookup. Read from IndexedDB, where items
	 * are cached with their `uom_prices` map. A component missing from the
	 * cache contributes no price and routes the allocation to its weight
	 * fallback, exactly like a price list that does not carry it.
	 *
	 * @param {Object} pkg - Package definition
	 * @returns {Promise<Object<string, Object<string, number>>>}
	 */
	async function componentPriceListRates(pkg) {
		const codes = new Set();
		for (const row of pkg?.items || []) codes.add(row.item_code);
		for (const row of pkg?.options || []) codes.add(row.item_code);

		const rates = {};
		await Promise.all(
			[...codes].map(async (itemCode) => {
				const item = await getCachedItem(itemCode);
				if (item?.uom_prices) rates[itemCode] = item.uom_prices;
			})
		);
		return rates;
	}

	/**
	 * Price a selection locally, applying the global allocation toggle the
	 * same way the server does.
	 *
	 * @param {Object} pkg - Package definition
	 * @param {Object<string, Object<string, number>>} selections - group_key -> option_id -> qty
	 * @returns {Promise<Object>} Local quote
	 */
	async function quoteLocally(pkg, selections) {
		const settingsStore = usePOSSettingsStore();
		const allocate = settingsStore.packageAllocationEnabled;
		if (!allocate) return quotePackageLocally(pkg, selections);

		return quotePackageLocally(pkg, selections, {
			allocate: true,
			priceListRates: await componentPriceListRates(pkg),
		});
	}

	/**
	 * Price a selection. Uses the server when online so the preview matches what
	 * the invoice will charge; falls back to the local mirror when offline.
	 *
	 * @param {Object} pkg - Package definition
	 * @param {Object<string, Object<string, number>>} selections - group_key -> option_id -> qty
	 * @param {string} posProfile - POS Profile name
	 * @returns {Promise<{valid: boolean, error: string|null, total: number, lines: Array, snapshot: Object}>}
	 */
	async function quote(pkg, selections, posProfile) {
		const local = await quoteLocally(pkg, selections);

		// Local validation failed — no point asking the server the same question.
		if (!local.valid || isOffline()) return local;

		try {
			const response = await call("pos_next.api.packages.quote_package", {
				package: pkg.name,
				choices: JSON.stringify(selectionsToChoices(selections)),
				pos_profile: posProfile,
			});
			const result = response?.message || response;
			if (!result) return local;

			// Server-authoritative: an allocation-mode quote comes back with the
			// component rates already split, exactly as the invoice will charge.
			return {
				valid: true,
				error: null,
				total: result.total,
				lines: result.lines,
				snapshot: result.snapshot,
			};
		} catch (error) {
			// The server rejected this package (expired, wrong outlet, edited
			// definition). Falling back to the local price would quote the
			// customer an amount that the invoice will refuse at checkout.
			log.error("Server rejected the package quote", error);

			return {
				valid: false,
				error:
					error?.message ||
					__("This package is no longer available. Please reload the POS."),
				total: 0,
				lines: [],
				snapshot: null,
			};
		}
	}

	return {
		packages,
		fetchedProfile,
		isLoading,
		packagesByParentItem,
		packageItemCodes,
		isPackageItem,
		getPackageForItem,
		setPackages,
		clearPackages,
		ensurePackagesFetched,
		quote,
	};
});
