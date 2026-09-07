import { describe, expect, it } from "vitest";

import { formatCurrency, formatCurrencyNumber } from "./currency";

describe("money display format (Indonesian, no decimals)", () => {
	it("groups thousands with dots", () => {
		expect(formatCurrencyNumber(2000)).toBe("2.000");
		expect(formatCurrencyNumber(1500000)).toBe("1.500.000");
	});

	it("drops decimals", () => {
		expect(formatCurrencyNumber(1234.56)).toBe("1.235");
		expect(formatCurrencyNumber(0.99)).toBe("1");
	});

	it("handles null-ish input", () => {
		expect(formatCurrencyNumber(Number.NaN)).toBe("0");
	});

	it("formats with symbol and keeps the minus in front", () => {
		expect(formatCurrency(2000, "IDR")).toBe("Rp 2.000");
		expect(formatCurrency(-2000, "IDR")).toBe("-Rp 2.000");
	});
});
