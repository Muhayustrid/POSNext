const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const utils = require('../../public/js/hq_monitoring_utils.js');

const pageDir = path.resolve(__dirname, '../../pos_next/page/hq_sales_monitoring');
const source = fs.readFileSync(path.join(pageDir, 'hq_sales_monitoring.js'), 'utf8');
const css = fs.readFileSync(path.join(pageDir, 'hq_sales_monitoring.css'), 'utf8');

test('autoHourWindow hugs hours with orders, padded one hour, clamped to the day', () => {
	assert.equal(utils.autoHourWindow([]), null);
	assert.equal(utils.autoHourWindow(null), null);
	// sales but no orders do not open a window
	assert.equal(utils.autoHourWindow([{ hour: 9, orders: 0, net_sales: 5 }]), null);
	// non-integer hours are ignored, real ones still count
	const junk = utils.autoHourWindow([{ hour: 'x', orders: 2 }, { hour: 10, orders: 1 }]);
	assert.deepEqual({ start: junk.start, end: junk.end }, { start: 9, end: 11 });

	const single = utils.autoHourWindow([{ hour: 9, orders: 3, net_sales: 5 }]);
	assert.deepEqual({ start: single.start, end: single.end }, { start: 8, end: 10 });

	const midnight = utils.autoHourWindow([{ hour: 0, orders: 1 }]);
	assert.deepEqual({ start: midnight.start, end: midnight.end }, { start: 0, end: 1 });

	const late = utils.autoHourWindow([{ hour: 23, orders: 1 }]);
	assert.deepEqual({ start: late.start, end: late.end }, { start: 22, end: 24 });

	const span = utils.autoHourWindow([{ hour: 0, orders: 1 }, { hour: 23, orders: 1 }]);
	assert.deepEqual({ start: span.start, end: span.end }, { start: 0, end: 24 });
});

test('isEmptyOutlet hides only zero-sales, zero-TC, no-target outlets', () => {
	assert.equal(utils.isEmptyOutlet(null), false);
	assert.equal(utils.isEmptyOutlet({ orders: 0, net_tax_incl: 0, target: null }), true);
	assert.equal(utils.isEmptyOutlet({ orders: 0, net_tax_incl: 0, target: { missing: true } }), true);
	// a carried target keeps the outlet visible even at zero sales
	assert.equal(utils.isEmptyOutlet({ orders: 0, net_tax_incl: 0, target: { target_sales: 5 } }), false);
	assert.equal(utils.isEmptyOutlet({ orders: 2, net_tax_incl: 0, target: null }), false);
	assert.equal(utils.isEmptyOutlet({ orders: 0, net_tax_incl: 3, target: null }), false);
});

test('donuts skip empty sets and empty states stay one line', () => {
	const render = source.slice(source.indexOf('_render_donuts(s) {'), source.indexOf('_render_hour_chart(s) {'));
	assert.match(render, /if \(!el \|\| !entries\.length\) continue;/);
	assert.match(render, /type: "donut"/);
	assert.doesNotMatch(source, /hq-stat-group/);
	// compact one-line empty states replace the old 190px dashed ring
	assert.match(source, /hq-empty-line/);
	assert.doesNotMatch(source, /_donut_placeholder/);
});

test('page stays a summary: detail lists live in reports', () => {
	for (const gone of ['product_ranking', 'favorite_product', '_recent_card', '_outlet_rank_table', 'data-hq-page']) {
		assert.ok(!source.includes(gone), `${gone} must not come back`);
	}
	assert.match(source, /_report_href\("POS Product Sales Report"\)/);
	assert.match(source, /_report_href\("POS Return Report"\)/);
	assert.match(source, /_report_href\("POS Outlet Performance Report"\)/);
});

test('every drill-down link sits in its card title, same style', () => {
	const links = source.match(/<a class="[^"]*"[^>]*>\$\{__\("[^"]+"\)\} →<\/a>/g) || [];
	assert.equal(links.length, 5);
	for (const a of links) assert.ok(a.startsWith('<a class="btn btn-xs btn-default hq-title-link"'), a);
});

test('hero styling keeps Total Sales visibly dominant over companions', () => {
	const hero = css.match(/\.hq-hero-value\s*\{([^}]+)\}/)[1];
	assert.match(hero, /font-size:\s*28px/);
	const companion = css.match(/\.hq-hero \.hq-mini \.hq-kpi-value\s*\{([^}]+)\}/)[1];
	assert.match(companion, /font-size:\s*16px/);
	// empty cards must not inherit a chart-height box anymore
	assert.doesNotMatch(css, /\.hq-donut-empty/);
	assert.match(css, /\.hq-empty-line\s*\{/);
});
