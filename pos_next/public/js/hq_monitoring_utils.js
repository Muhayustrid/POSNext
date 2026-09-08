// Copyright (c) 2026, BrainWise and contributors
// For license information, please see license.txt

/**
 * Pure helpers for the HQ Sales Monitoring page.
 * No frappe dependency so this file doubles as a node unit-test module.
 */
(function (root, factory) {
	const api = factory();
	if (typeof module === "object" && module.exports) {
		module.exports = api;
	} else {
		root.hqMonitorUtils = api;
	}
})(typeof self !== "undefined" ? self : this, function () {
	function safeDiv(part, whole) {
		whole = Number(whole);
		if (!whole) return null;
		return Number(part || 0) / whole;
	}

	/** Percentage or null when denominator is zero (never fake 0%). */
	function pctOf(part, whole) {
		const r = safeDiv(part, whole);
		return r === null ? null : r * 100;
	}

	/** Growth percentage vs prior, or null when prior is zero. */
	function growthPct(current, prior) {
		prior = Number(prior || 0);
		if (!prior) return null;
		return ((Number(current || 0) - prior) / prior) * 100;
	}

	function fmtPct(value, digits) {
		if (value === null || value === undefined || isNaN(value)) return "N/A";
		return Number(value).toFixed(digits == null ? 1 : digits) + "%";
	}

	function fmtMoney(value, currency) {
		const n = Number(value || 0);
		const ccy = currency || "";
		const formatted = Math.abs(n).toLocaleString("en-US", { maximumFractionDigits: 2 });
		const sign = n < 0 ? "-" : "";
		return ccy ? sign + ccy + " " + formatted : sign + formatted;
	}

	function hourLabel(hour) {
		return String(hour).padStart(2, "0") + ":00";
	}

	function csvCell(value) {
		const s = value === null || value === undefined ? "" : String(value);
		return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
	}

	function toCsv(headers, rows) {
		const lines = [headers.map(csvCell).join(",")];
		rows.forEach((row) => lines.push(row.map(csvCell).join(",")));
		return lines.join("\n");
	}

	return {
		safeDiv: safeDiv,
		pctOf: pctOf,
		growthPct: growthPct,
		fmtPct: fmtPct,
		fmtMoney: fmtMoney,
		hourLabel: hourLabel,
		toCsv: toCsv,
		csvCell: csvCell,
	};
});
