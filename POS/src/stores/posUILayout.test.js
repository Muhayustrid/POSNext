/**
 * @vitest-environment jsdom
 */
import { beforeEach, describe, expect, it } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { usePOSUIStore } from "./posUI";

const WIDTH_KEY = "pos_left_panel_width";

describe("posUI left panel layout", () => {
	beforeEach(() => {
		setActivePinia(createPinia());
		localStorage.removeItem(WIDTH_KEY);
	});

	it("starts unsized and derives a proportional default on first layout", () => {
		const store = usePOSUIStore();
		expect(store.leftPanelWidth).toBeNull();

		store.updateLayoutBounds(1440);
		const width = store.leftPanelWidth;
		// Items panel stays slightly wider than the cart (56% split)...
		expect(width).toBe(806);
		// ...and the cart keeps a comfortable remainder, not the old fixed
		// 800px default that starved it on smaller laptops.
		expect(1440 - width).toBeGreaterThanOrEqual(360);
	});

	it("scales the default down for smaller screens so the cart never starves", () => {
		const store = usePOSUIStore();
		store.updateLayoutBounds(1024);
		// Old fixed default left the cart at the 360px clamp minimum here.
		expect(1024 - store.leftPanelWidth).toBeGreaterThan(400);
	});

	it("restores the cashier's saved divider position instead of the default", () => {
		localStorage.setItem(WIDTH_KEY, "700");
		const store = usePOSUIStore();
		store.updateLayoutBounds(1440);
		expect(store.leftPanelWidth).toBe(700);
	});

	it("clamps a saved position that no longer fits the current screen", () => {
		localStorage.setItem(WIDTH_KEY, "900");
		const store = usePOSUIStore();
		store.updateLayoutBounds(1000);
		expect(store.leftPanelWidth).toBe(640); // 1000 - RIGHT_PANEL_MIN
	});

	it("falls back to the proportional default on corrupt storage values", () => {
		localStorage.setItem(WIDTH_KEY, "not-a-number");
		const store = usePOSUIStore();
		store.updateLayoutBounds(1440);
		expect(store.leftPanelWidth).toBe(806);
	});

	it("persists the divider position only when asked", () => {
		const store = usePOSUIStore();
		store.updateLayoutBounds(1440);
		expect(localStorage.getItem(WIDTH_KEY)).toBeNull();

		store.setLeftPanelWidth(650, 1440);
		store.saveLeftPanelWidth();
		expect(localStorage.getItem(WIDTH_KEY)).toBe("650");
	});
});
