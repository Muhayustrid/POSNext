// Amount input mask: Indonesian thousand separators while typing.
// "200000" → "200.000", "20000" → "20.000", "2000" → "2.000".
// Dots are grouping only; the comma is the decimal sign (mobile id keyboard).
// parseAmountInput is the single way back to a number for the money payload.

export function formatAmountInput(raw) {
	if (raw == null) return "";
	const text = String(raw);
	// Leading minus survives: closing rows can pre-fill a negative expected
	// (returns outweighing sales in that payment mode).
	const negative = text.trimStart().startsWith("-");
	const cleaned = text.replace(/[^\d,]/g, "");
	if (!cleaned) return "";
	const [intRaw, decRaw = ""] = cleaned.split(",");
	const intPart = intRaw.replace(/^0+(?=\d)/, "") || "0";
	const grouped = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
	const sign = negative ? "-" : "";
	// ponytail: caret jumps to end on reformat — fine for fresh amounts;
	// preserve caret if mid-string editing ever matters here
	return cleaned.includes(",")
		? `${sign}${grouped},${decRaw.slice(0, 2)}`
		: `${sign}${grouped}`;
}

export function parseAmountInput(raw) {
	if (raw == null || raw === "") return 0;
	const text = String(raw);
	const negative = text.trimStart().startsWith("-");
	const cleaned = text.replace(/[^\d,]/g, "");
	if (!cleaned) return 0;
	const [intRaw, decRaw = ""] = cleaned.split(",");
	const value = Number.parseFloat(`${intRaw || "0"}.${decRaw.slice(0, 2) || "0"}`);
	return Number.isNaN(value) ? 0 : negative ? -value : value;
}
