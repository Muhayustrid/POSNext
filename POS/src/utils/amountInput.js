// Amount input mask: Indonesian thousand separators while typing.
// "200000" → "200.000", "20000" → "20.000", "2000" → "2.000".
// Dots are grouping only; the comma is the decimal sign (mobile id keyboard).
// parseAmountInput is the single way back to a number for the money payload.

export function formatAmountInput(raw) {
	if (raw == null) return "";
	const cleaned = String(raw).replace(/[^\d,]/g, "");
	if (!cleaned) return "";
	const [intRaw, decRaw = ""] = cleaned.split(",");
	const intPart = intRaw.replace(/^0+(?=\d)/, "") || "0";
	const grouped = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
	// ponytail: caret jumps to end on reformat — fine for fresh amounts;
	// preserve caret if mid-string editing ever matters here
	return cleaned.includes(",") ? `${grouped},${decRaw.slice(0, 2)}` : grouped;
}

export function parseAmountInput(raw) {
	if (raw == null || raw === "") return 0;
	const cleaned = String(raw).replace(/[^\d,]/g, "");
	if (!cleaned) return 0;
	const [intRaw, decRaw = ""] = cleaned.split(",");
	const value = Number.parseFloat(`${intRaw || "0"}.${decRaw.slice(0, 2) || "0"}`);
	return Number.isNaN(value) ? 0 : value;
}
