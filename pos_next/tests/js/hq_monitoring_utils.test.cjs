// Copyright (c) 2026, BrainWise and contributors
// Node unit tests for the pure page helpers.
// Run: node pos_next/tests/js/hq_monitoring_utils.test.cjs
const assert = require("assert");
const u = require("../../public/js/hq_monitoring_utils.js");

// percentage / growth guards: zero denominator -> N/A, never fake 0
assert.strictEqual(u.pctOf(50, 200), 25);
assert.strictEqual(u.pctOf(1, 0), null);
assert.strictEqual(u.growthPct(100, 0), null);
assert.strictEqual(u.growthPct(150, 100), 50);
assert.strictEqual(u.growthPct(50, 200), -75);

// formatting
assert.strictEqual(u.fmtPct(null), "N/A");
assert.strictEqual(u.fmtPct(undefined), "N/A");
assert.strictEqual(u.fmtPct(12.34), "12.3%");
assert.strictEqual(u.fmtPct(12.34, 2), "12.34%");
assert.strictEqual(u.fmtMoney(-1500, "IDR"), "-IDR 1,500");
assert.strictEqual(u.fmtMoney(0), "0");
assert.strictEqual(u.hourLabel(7), "07:00");
assert.strictEqual(u.hourLabel(23), "23:00");

// CSV escaping
assert.strictEqual(u.toCsv(["a", "b"], [["x", "y"]]), "a,b\nx,y");
assert.strictEqual(
	u.toCsv(["a", "b"], [["x,y", '1"2']]),
	"a,b\n\"x,y\",\"1\"\"2\""
);

// hourBins24: full 00..23 axis, missing hours become explicit zero bins
const bins = u.hourBins24([
	{ hour: 9, net_sales: 100, orders: 2, apc: 50 },
	{ hour: 23, net_sales: 30.5, orders: 1, apc: 30.5 },
]);
assert.strictEqual(bins.length, 24);
assert.strictEqual(bins[0].hour, 0);
assert.strictEqual(bins[9].net_sales, 100);
assert.strictEqual(bins[9].orders, 2);
assert.strictEqual(bins[10].net_sales, 0); // absent hour -> explicit zero bin
assert.strictEqual(bins[10].orders, 0);
assert.strictEqual(bins[10].apc, null);
assert.strictEqual(bins[23].net_sales, 30.5);
// junk rows ignored; empty input -> all zeros
assert.strictEqual(u.hourBins24([{ hour: 24, net_sales: 5 }])[24], undefined);
assert.strictEqual(u.hourBins24([{ hour: "7", net_sales: 5 }])[7].net_sales, 5);
assert.strictEqual(u.hourBins24([]).reduce((s, b) => s + b.net_sales, 0), 0);
assert.strictEqual(u.hourBins24(null).length, 24);
// real-data rankings are untouched by the binning (same rows in, same out)
assert.deepStrictEqual(
	u.hourBins24([{ hour: 1, net_sales: 2, orders: 1, apc: 2 }])[1],
	{ hour: 1, net_sales: 2, orders: 1, apc: 2 }
);

// hourRangeLabel: full tooltip range, 23:00 wraps to 00:00
assert.strictEqual(u.hourRangeLabel("07:00"), "07:00 – 08:00");
assert.strictEqual(u.hourRangeLabel("23:00"), "23:00 – 00:00");
assert.strictEqual(u.hourRangeLabel("00:00"), "00:00 – 01:00");
assert.strictEqual(u.hourRangeLabel("9:00"), "09:00 – 10:00");
assert.strictEqual(u.hourRangeLabel("25:00"), "25:00"); // invalid passes through
assert.strictEqual(u.hourRangeLabel("Net Sales"), "Net Sales");

// parseHourWindow: start inclusive / end exclusive, start == end rejected
assert.deepStrictEqual(u.parseHourWindow(5, 18), {
	start: 5,
	end: 18,
	count: 13,
	overnight: false,
});
// overnight wraps past midnight: 22:00 -> 06:00 next day = 8 bins
assert.deepStrictEqual(u.parseHourWindow(22, 6), {
	start: 22,
	end: 6,
	count: 8,
	overnight: true,
});
// whole day spans the 24 boundary: 00:00 inclusive .. 24:00 exclusive
assert.deepStrictEqual(u.parseHourWindow(0, 24), {
	start: 0,
	end: 24,
	count: 24,
	overnight: false,
});
// single hour
assert.deepStrictEqual(u.parseHourWindow(9, 10), { start: 9, end: 10, count: 1, overnight: false });
// start == end -> empty window, rejected (both orders)
assert.strictEqual(u.parseHourWindow(5, 5), null);
assert.strictEqual(u.parseHourWindow(0, 0), null);
// out-of-range / junk rejected
assert.strictEqual(u.parseHourWindow(24, 6), null); // start must be 0..23
assert.strictEqual(u.parseHourWindow(5, 25), null); // end must be 1..24
assert.strictEqual(u.parseHourWindow(5, 0), null); // end 0 unreachable (24 is the midnight end)
assert.strictEqual(u.parseHourWindow(-1, 6), null);
assert.strictEqual(u.parseHourWindow("a", 6), null);
assert.strictEqual(u.parseHourWindow(null, null), null);
// numeric strings coerced (stored prefs may be strings)
assert.strictEqual(u.parseHourWindow("5", "18").count, 13);

// hourWindowBins: only the visible window, chronological, zero bins kept
const rows = [
	{ hour: 6, net_sales: 60, orders: 1, apc: 60 },
	{ hour: 9, net_sales: 100, orders: 2, apc: 50 },
	{ hour: 22, net_sales: 40, orders: 1, apc: 40 },
];
assert.strictEqual(u.hourWindowBins(rows, 5, 18).length, 13);
assert.strictEqual(u.hourWindowBins(rows, 5, 18)[0].hour, 5);
assert.strictEqual(u.hourWindowBins(rows, 5, 18)[12].hour, 17); // end exclusive
assert.strictEqual(u.hourWindowBins(rows, 5, 18)[1].net_sales, 60);
// overnight: 22 -> 06 wraps past midnight, 8 bins in clock order
const night = u.hourWindowBins(rows, 22, 6);
assert.deepStrictEqual(
	night.map((b) => b.hour),
	[22, 23, 0, 1, 2, 3, 4, 5]
);
// full day = all 24 bins in order
const full = u.hourWindowBins(rows, 0, 24);
assert.strictEqual(full.length, 24);
assert.strictEqual(full[0].hour, 0);
assert.strictEqual(full[23].hour, 23);

// windowStats: peak/top/lowest recomputed from measured rows in-window only
const stats = u.windowStats(u.hourWindowBins(rows, 5, 18));
assert.strictEqual(stats.peak.hour, 9); // 100 > 60; 22:00 not visible
assert.strictEqual(stats.peak.net_sales, 100);
assert.strictEqual(stats.top.length, 2); // only hours with orders in window
const nightStats = u.windowStats(u.hourWindowBins(rows, 22, 6));
assert.strictEqual(nightStats.peak.hour, 22); // only hour 22 has orders (06:00 excluded)
assert.strictEqual(nightStats.top.length, 1);
assert.strictEqual(nightStats.lowest[0].hour, 22);
// tie on net -> earlier hour wins; empty window -> no peak
const tie = u.windowStats([
	{ hour: 8, net_sales: 50, orders: 1, apc: 50 },
	{ hour: 7, net_sales: 50, orders: 1, apc: 50 },
]);
assert.strictEqual(tie.peak.hour, 7);
assert.strictEqual(u.windowStats([{ hour: 3, net_sales: 0, orders: 0, apc: null }]).peak, null);
assert.strictEqual(u.windowStats([]).peak, null);

// hourChartMinWidth: proportional to bins (24 bins -> ~calibrated 940px axis)
assert.strictEqual(u.hourChartMinWidth(24), 960);
assert.strictEqual(u.hourChartMinWidth(13), 520);
assert.strictEqual(u.hourChartMinWidth(1), 40);

// sanitizePrefs: corrupt / garbage storage falls back to all-day defaults
const defaults = { hour_start: 0, hour_end: 24, category_a: "", category_b: "" };
assert.deepStrictEqual(u.sanitizePrefs(null), defaults);
assert.deepStrictEqual(u.sanitizePrefs(undefined), defaults);
assert.deepStrictEqual(u.sanitizePrefs(""), defaults);
assert.deepStrictEqual(u.sanitizePrefs("{corrupt json"), defaults); // corrupt storage
assert.deepStrictEqual(u.sanitizePrefs(42), defaults);
assert.deepStrictEqual(u.sanitizePrefs('{"hq_dashboard": "not an object"}'), defaults);
// valid prefs round-trip (frappe user_settings returns the parsed blob)
assert.deepStrictEqual(
	u.sanitizePrefs({ hq_dashboard: { hour_start: 5, hour_end: 18, category_a: "Food", category_b: "Beverage" } }),
	{ hour_start: 5, hour_end: 18, category_a: "Food", category_b: "Beverage" }
);
// plain shape (no wrapper key) also accepted
assert.deepStrictEqual(u.sanitizePrefs({ hour_start: 22, hour_end: 6 }), {
	hour_start: 22,
	hour_end: 6,
	category_a: "",
	category_b: "",
});
// stored hour window start == end -> rejected back to all-day, junk hours too
assert.deepStrictEqual(u.sanitizePrefs({ hour_start: 5, hour_end: 5 }).hour_start, 0);
assert.deepStrictEqual(u.sanitizePrefs({ hour_start: 5, hour_end: 5 }).hour_end, 24);
assert.deepStrictEqual(u.sanitizePrefs({ hour_start: "x", hour_end: 6 }).hour_end, 24);
// categories must be strings; oversize/whitespace tamed
assert.deepStrictEqual(u.sanitizePrefs({ category_a: 7, category_b: "  Food " }).category_a, "");
assert.strictEqual(u.sanitizePrefs({ category_b: "  Food " }).category_b, "Food");

// categoryDonutRows: chart data for one "Top Selling <category>" card —
// top items + one Other slice, shares recomputed from the drawn values so
// donut and legend always agree over the whole category.
const cpData = {
	category: "Beverages",
	items: [
		{ item_code: "A", item_name: "Alpha", qty: 3, net_amount: 100, share_pct: 33.3 },
		{ item_code: "B", item_name: "Beta", qty: 1, net_amount: 100, share_pct: 33.3 },
	],
	other_net: 100,
	other_share_pct: 33.3,
	category_total: 300,
	items_with_sales: 7,
};
const donutRows = u.categoryDonutRows(cpData, "Other");
assert.strictEqual(donutRows.length, 3);
assert.deepStrictEqual(
	donutRows.map((r) => r.item_name),
	["Alpha", "Beta", "Other"]
);
// chart values sum to the whole category (top 5 + Other = category_total)
assert.strictEqual(donutRows.reduce((s, r) => s + r.net_amount, 0), 300);
assert.strictEqual(
	donutRows.reduce((s, r) => s + r.share_pct, 0).toFixed(9),
	"100.000000000"
);
assert.strictEqual(donutRows[2].qty, 0); // Other carries no qty
assert.strictEqual(donutRows[0].qty, 3);
// no Other slice when the top items are the whole category / other not positive
assert.strictEqual(u.categoryDonutRows({ ...cpData, other_net: 0 }, "Other").length, 2);
assert.strictEqual(u.categoryDonutRows({ ...cpData, other_net: -5 }, "Other").length, 2);
// empty states: nothing picked, junk payload, or no positive sales -> []
assert.deepStrictEqual(u.categoryDonutRows({}, "Other"), []);
assert.deepStrictEqual(u.categoryDonutRows(null, "Other"), []);
assert.deepStrictEqual(u.categoryDonutRows(undefined, "Other"), []);
assert.deepStrictEqual(
	u.categoryDonutRows({ items: [{ item_code: "Z", net_amount: 0, qty: 2 }] }, "Other"),
	[]
);
assert.deepStrictEqual(
	u.categoryDonutRows({ items: [{ item_code: "Z", net_amount: -3 }] }, "Other"),
	[]
);
// nonpositive items filtered out; name falls back to item_code; lone item = 100%
const fallback = u.categoryDonutRows(
	{ items: [{ item_code: "C", item_name: "", net_amount: 10 }, { item_code: "D", net_amount: -3 }] },
	"O"
);
assert.strictEqual(fallback.length, 1);
assert.strictEqual(fallback[0].item_name, "C");
assert.strictEqual(fallback[0].share_pct, 100);

console.log("hq_monitoring_utils: 13 groups OK");
