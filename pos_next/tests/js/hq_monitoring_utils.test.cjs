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

console.log("hq_monitoring_utils: 5 groups OK");
