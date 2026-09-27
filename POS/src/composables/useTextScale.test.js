// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";
import {
	initTextScale,
	TEXT_SCALE_DEFAULT,
	TEXT_SCALE_MAX,
	TEXT_SCALE_MIN,
	useTextScale,
} from "./useTextScale";

const STORAGE_KEY = "pos_text_scale";

describe("useTextScale", () => {
	beforeEach(() => {
		localStorage.removeItem(STORAGE_KEY);
		document.documentElement.style.fontSize = "";
	});

	it("applies the saved scale at init and reports it", () => {
		localStorage.setItem(STORAGE_KEY, "110");
		initTextScale();
		const { scale, isDefault } = useTextScale();
		expect(scale.value).toBe(110);
		expect(isDefault.value).toBe(false);
		expect(document.documentElement.style.fontSize).toBe("110%");
	});

	it("falls back to the default scale when nothing is stored", () => {
		initTextScale();
		const { scale, isDefault } = useTextScale();
		expect(scale.value).toBe(TEXT_SCALE_DEFAULT);
		expect(isDefault.value).toBe(true);
		expect(document.documentElement.style.fontSize).toBe("100%");
	});

	it("increases and decreases in bounded steps, persisting each change", () => {
		initTextScale();
		const { scale, increase, decrease } = useTextScale();

		increase();
		expect(scale.value).toBe(105);
		expect(document.documentElement.style.fontSize).toBe("105%");
		expect(localStorage.getItem(STORAGE_KEY)).toBe("105");

		for (let i = 0; i < 20; i++) increase();
		expect(scale.value).toBe(TEXT_SCALE_MAX);
		expect(document.documentElement.style.fontSize).toBe("125%");

		for (let i = 0; i < 20; i++) decrease();
		expect(scale.value).toBe(TEXT_SCALE_MIN);
		expect(document.documentElement.style.fontSize).toBe("85%");
		expect(localStorage.getItem(STORAGE_KEY)).toBe("85");
	});

	it("clamps an out-of-range stored value instead of applying it", () => {
		localStorage.setItem(STORAGE_KEY, "300");
		initTextScale();
		const { scale } = useTextScale();
		expect(scale.value).toBe(TEXT_SCALE_MAX);
		expect(document.documentElement.style.fontSize).toBe("125%");
	});

	it("treats a corrupt stored value as the default", () => {
		localStorage.setItem(STORAGE_KEY, "besar");
		initTextScale();
		const { scale, isDefault } = useTextScale();
		expect(scale.value).toBe(TEXT_SCALE_DEFAULT);
		expect(isDefault.value).toBe(true);
	});

	it("reset returns to the default and persists it", () => {
		initTextScale();
		const { scale, reset } = useTextScale();
		useTextScale().increase();
		reset();
		expect(scale.value).toBe(TEXT_SCALE_DEFAULT);
		expect(document.documentElement.style.fontSize).toBe("100%");
		expect(localStorage.getItem(STORAGE_KEY)).toBe("100");
	});
});
