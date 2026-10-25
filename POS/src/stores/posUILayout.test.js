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
		// Items panel takes the space beyond the capped cart (1440 - 576)...
		expect(width).toBe(864);
		// ...and the cart keeps a comfortable remainder, not the old fixed
		// 800px default that starved it on smaller laptops.
		expect(1440 - width).toBe(576);
	});

	it("scales the default down for smaller screens so the cart never starves", () => {
		const store = usePOSUIStore();
		store.updateLayoutBounds(1024);
		// Old fixed default left the cart at the 360px clamp minimum here.
		expect(1024 - store.leftPanelWidth).toBeGreaterThan(400);
	});

	it("restores the cashier's saved divider position instead of the default", () => {
		localStorage.setItem(WIDTH_KEY, "800");
		const store = usePOSUIStore();
		store.updateLayoutBounds(1440);
		// 800 is below the 864 minimum (1440 - capped cart 576), so it clamps up.
		expect(store.leftPanelWidth).toBe(864);
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
		expect(store.leftPanelWidth).toBe(864);
	});

	it("persists the divider position only when asked", () => {
		const store = usePOSUIStore();
		store.updateLayoutBounds(1440);
		expect(localStorage.getItem(WIDTH_KEY)).toBeNull();

		store.setLeftPanelWidth(950, 1440);
		store.saveLeftPanelWidth();
		expect(localStorage.getItem(WIDTH_KEY)).toBe("950");
	});
});
