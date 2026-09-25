import { describe, expect, it } from "vitest";

import { formatCurrency, formatCurrencyNumber, getCurrencySymbol } from "./currency";

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

describe("per-currency display decimals (COR-FE-06)", () => {
	it("shows two decimals for non-IDR currencies", () => {
		// display locale is id-ID: comma decimal separator
		expect(formatCurrency(12.5, "USD")).toBe("$ 12,50");
		expect(formatCurrency(99.999, "EUR")).toBe("€ 100,00");
	});

	it("keeps rupiah as whole numbers", () => {
		expect(formatCurrency(12500, "IDR")).toBe("Rp 12.500");
		expect(formatCurrency(12500.75, "IDR")).toBe("Rp 12.501");
	});

	it("uses the correct riyal symbol", () => {
		expect(getCurrencySymbol("SAR")).toBe("SR");
		expect(formatCurrency(12.5, "SAR")).toBe("SR 12,50");
	});
});
