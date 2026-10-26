import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

vi.mock("frappe-ui", () => ({
	createResource: () => ({
		submit: vi.fn(),
		reload: vi.fn(),
	}),
}));

import { usePOSSettingsStore } from "./posSettings";

describe("posSettings store stock blocking", () => {
	beforeEach(() => {
		setActivePinia(createPinia());
	});

	it("defaults block_out_of_stock_items to 1", () => {
		const store = usePOSSettingsStore();
		expect(store.settings.block_out_of_stock_items).toBe(1);
		expect(store.blockOutOfStockItems).toBe(true);
	});

	it("enforces stock validation when enabled and block_out_of_stock_items is 1", () => {
		const store = usePOSSettingsStore();
		store.settings.enabled = 1;
		store.settings.block_out_of_stock_items = 1;
		store.settings.allow_negative_stock = 0;

		expect(store.shouldEnforceStockValidation()).toBe(true);
	});

	it("wins over allow_negative_stock: enforces even when negative stock is allowed", () => {
		const store = usePOSSettingsStore();
		store.settings.enabled = 1;
		store.settings.block_out_of_stock_items = 1;
		store.settings.allow_negative_stock = 1;

		expect(store.shouldEnforceStockValidation()).toBe(true);
	});

	it("skips stock validation when block_out_of_stock_items is 0", () => {
		const store = usePOSSettingsStore();
		store.settings.enabled = 1;
		store.settings.block_out_of_stock_items = 0;
		store.settings.allow_negative_stock = 0;

		expect(store.shouldEnforceStockValidation()).toBe(false);
	});

	it("skips stock validation when store is not enabled", () => {
		const store = usePOSSettingsStore();
		store.settings.enabled = 0;
		store.settings.block_out_of_stock_items = 1;

		expect(store.shouldEnforceStockValidation()).toBe(false);
	});

	it("resets block_out_of_stock_items to 1 on resetSettings", () => {
		const store = usePOSSettingsStore();
		store.settings.block_out_of_stock_items = 0;
		store.resetSettings();

		expect(store.settings.block_out_of_stock_items).toBe(1);
	});
});
