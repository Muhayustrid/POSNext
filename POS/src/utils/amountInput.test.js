import { describe, expect, it } from "vitest";
import { formatAmountInput, parseAmountInput } from "./amountInput";

describe("formatAmountInput", () => {
	it("groups thousands with dots", () => {
		expect(formatAmountInput("200000")).toBe("200.000");
		expect(formatAmountInput("20000")).toBe("20.000");
		expect(formatAmountInput("2000")).toBe("2.000");
		expect(formatAmountInput("200")).toBe("200");
	});

	it("keeps the typed decimal comma and caps at 2 decimals", () => {
		expect(formatAmountInput("1000,5")).toBe("1.000,5");
		expect(formatAmountInput("1000,555")).toBe("1.000,55");
		expect(formatAmountInput(",5")).toBe("0,5");
		expect(formatAmountInput("2000,")).toBe("2.000,");
	});

	it("ignores non-numeric characters and stray dots", () => {
		expect(formatAmountInput("12a34")).toBe("1.234");
		expect(formatAmountInput("12.34")).toBe("1.234");
		expect(formatAmountInput("")).toBe("");
		expect(formatAmountInput("abc")).toBe("");
		expect(formatAmountInput(null)).toBe("");
	});

	it("keeps a lone zero but collapses leading zeros", () => {
		expect(formatAmountInput("0")).toBe("0");
		expect(formatAmountInput("007")).toBe("7");
	});

	it("keeps a leading minus (closing rows can pre-fill negative expected)", () => {
		expect(formatAmountInput("-19750")).toBe("-19.750");
		expect(formatAmountInput(-19750)).toBe("-19.750");
		expect(formatAmountInput("-1.000,5")).toBe("-1.000,5");
		expect(formatAmountInput("-")).toBe("");
	});
});

describe("parseAmountInput", () => {
	it("parses formatted amounts back to numbers", () => {
		expect(parseAmountInput("200.000")).toBe(200000);
		expect(parseAmountInput("2.000")).toBe(2000);
		expect(parseAmountInput("200")).toBe(200);
		expect(parseAmountInput("1.000,5")).toBe(1000.5);
		expect(parseAmountInput("0,55")).toBe(0.55);
	});

	it("returns 0 for empty or invalid input", () => {
		expect(parseAmountInput("")).toBe(0);
		expect(parseAmountInput("abc")).toBe(0);
		expect(parseAmountInput(null)).toBe(0);
	});

	it("honors the leading minus", () => {
		expect(parseAmountInput("-19.750")).toBe(-19750);
		expect(parseAmountInput("-0,5")).toBe(-0.5);
		expect(parseAmountInput("-")).toBe(0);
	});

	it("round-trips through format", () => {
		expect(parseAmountInput(formatAmountInput("1234567"))).toBe(1234567);
		expect(parseAmountInput(formatAmountInput(-19750))).toBe(-19750);
	});
});
