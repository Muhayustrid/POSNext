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

	/**
	 * Fixed 00..23 axis for the MTD hours chart: hours without sales become
	 * explicit zero bins so the all-day window is honest and every bar maps
	 * to its hour. Peak/top/lowest rankings still come from real-data rows.
	 */
	function hourBins24(rows) {
		const bins = Array.from({ length: 24 }, (_, hour) => ({
			hour,
			net_sales: 0,
			orders: 0,
			apc: null,
		}));
		(rows || []).forEach((r) => {
			const h = Number(r && r.hour);
			if (Number.isInteger(h) && h >= 0 && h <= 23) {
				bins[h] = {
					hour: h,
					net_sales: Number(r.net_sales || 0),
					orders: Number(r.orders || 0),
					apc: r.apc === null || r.apc === undefined ? null : Number(r.apc),
				};
			}
		});
		return bins;
	}

	/** Chart tooltip range: "07:00" -> "07:00 – 08:00" (23:00 wraps to 00:00). */
	function hourRangeLabel(label) {
		const m = /^(\d{1,2}):00$/.exec(String(label));
		if (!m || Number(m[1]) > 23) return String(label);
		return `${m[1].padStart(2, "0")}:00 – ${hourLabel((Number(m[1]) + 1) % 24)}`;
	}

	/**
	 * Validate a peak-hour window: start inclusive (0..23), end exclusive
	 * (1..24, so 24:00 means midnight of the next day), start === end is an
	 * empty window and rejected. end <= start wraps past midnight (22–06).
	 * Returns {start, end, count, overnight} or null when invalid.
	 */
	function parseHourWindow(start, end) {
		const s = Number(start);
		const e = Number(end);
		if (!Number.isInteger(s) || !Number.isInteger(e)) return null;
		if (s < 0 || s > 23 || e < 1 || e > 24 || s === e) return null;
		const overnight = e <= s;
		return { start: s, end: e, count: overnight ? 24 - s + e : e - s, overnight };
	}

	/**
	 * Bins for the selected window only: start inclusive, end exclusive,
	 * chronological (overnight wraps past midnight). Hours without sales stay
	 * explicit zero bins so the axis stays honest.
	 */
	function hourWindowBins(rows, start, end) {
		const all = hourBins24(rows);
		const endH = ((Number(end) % 24) + 24) % 24;
		const bins = [];
		for (let h = Number(start) % 24; ; h = (h + 1) % 24) {
			bins.push(all[h]);
			if ((h + 1) % 24 === endH) break;
		}
		return bins;
	}

	/**
	 * Peak / top / lowest recomputed from the real-data rows visible in the
	 * window (orders > 0 only). Same ordering rules as the server aggregate:
	 * net desc, hour asc — so the label always matches the drawn chart.
	 */
	function windowStats(bins) {
		const withOrders = (bins || []).filter((b) => b.orders > 0);
		const top = withOrders
			.slice()
			.sort((a, b) => b.net_sales - a.net_sales || a.hour - b.hour)
			.slice(0, 3);
		const lowest = withOrders
			.slice()
			.sort((a, b) => a.net_sales - b.net_sales || a.hour - b.hour)
			.slice(0, 3);
		return { peak: top[0] || null, top, lowest };
	}

	/** Proportional chart min-width: 40px per bin keeps every HH:00 tick readable. */
	function hourChartMinWidth(binCount) {
		return Math.max(Number(binCount) || 1, 1) * 40;
	}

	/**
	 * Sanitize stored per-user dashboard preferences (server user settings blob
	 * or raw JSON string). Corrupt/garbage input falls back to defaults
	 * (all-day window, no categories) — never throws. Categories are only
	 * type-checked here; a stale (deleted) group is reported by the API and
	 * then cleared by the page.
	 */
	function sanitizePrefs(raw) {
		const defaults = { hour_start: 0, hour_end: 24, category_a: "", category_b: "" };
		let o = raw;
		if (typeof o === "string") {
			try {
				o = JSON.parse(o);
			} catch (e) {
				return defaults;
			}
		}
		if (!o || typeof o !== "object") return defaults;
		if (o.hq_dashboard && typeof o.hq_dashboard === "object") o = o.hq_dashboard;
		const win = parseHourWindow(o.hour_start, o.hour_end);
		const cat = (v) => (typeof v === "string" ? v.trim().slice(0, 140) : "");
		return {
			hour_start: win ? win.start : defaults.hour_start,
			hour_end: win ? win.end : defaults.hour_end,
			category_a: cat(o.category_a),
			category_b: cat(o.category_b),
		};
	}

	/**
	 * Donut/legend rows for one "Top Selling <category>" card: the top items
	 * returned by the API (positive only) plus one "Other" slice when the rest
	 * of the category has positive sales. Shares are recomputed from exactly
	 * the drawn values, so the chart and the legend always agree over the
	 * whole category. No category / no positive sales -> [] (empty state).
	 */
	function categoryDonutRows(cp, otherLabel) {
		const items = ((cp && cp.items) || []).filter(
			(r) => r && Number(r.net_amount) > 0
		);
		if (!items.length) return [];
		const other = Math.max(Number(cp.other_net) || 0, 0);
		const total = items.reduce((s, r) => s + Number(r.net_amount), 0) + other;
		const rows = items.map((r) => ({
			item_name: r.item_name || r.item_code || "",
			net_amount: Number(r.net_amount),
			qty: Number(r.qty) || 0,
			share_pct: (Number(r.net_amount) / total) * 100,
		}));
		if (other > 0) {
			rows.push({
				item_name: otherLabel || "Other",
				net_amount: other,
				qty: 0,
				share_pct: (other / total) * 100,
			});
		}
		return rows;
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
		hourBins24: hourBins24,
		hourRangeLabel: hourRangeLabel,
		parseHourWindow: parseHourWindow,
		hourWindowBins: hourWindowBins,
		windowStats: windowStats,
		hourChartMinWidth: hourChartMinWidth,
		sanitizePrefs: sanitizePrefs,
		categoryDonutRows: categoryDonutRows,
		toCsv: toCsv,
		csvCell: csvCell,
	};
});
