const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const pageDir = path.resolve(__dirname, '../../pos_next/page/hq_sales_monitoring');
const source = fs.readFileSync(path.join(pageDir, 'hq_sales_monitoring.js'), 'utf8');
const css = fs.readFileSync(path.join(pageDir, 'hq_sales_monitoring.css'), 'utf8');
const method = source.slice(source.indexOf('_mini_cards(s) {'), source.indexOf('_sales_card(s) {'));
const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (char) => ({
	'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
})[char]);
const renderer = vm.runInNewContext(`({${method}})`, {
	__: (value) => value,
	frappe: { utils: { escape_html: escapeHtml } },
	HQ_UTILS: { fmtMoney: (value) => String(value), fmtCount: (value) => String(value) },
});
renderer._metric_text = () => 'IDR 100';
renderer._pct_cell = () => 'N/A';
renderer._signed_pct = () => '100%';

test('highlight names remain complete, escaped, and separate from numeric values', () => {
	const name = 'RS PKU MUHAMMADIYAH ' + 'OUTLET PANJANG '.repeat(12) + '<Cabang & Pusat>';
	const outlet = { company: name, orders: 12, share_pct: 100, net_tax_incl: 100, currency: 'IDR' };
	const html = renderer._mini_cards({
		scope: { default_currency: 'IDR' },
		highlights: { biggest_outlet: outlet, most_transactions_outlet: outlet },
		favorite_product: { item_name: name, qty: 6, item_group: 'Roti' },
	});
	assert.equal(html.split('hq-kpi-value hq-kpi-name').length - 1, 3);
	assert.equal(html.split(escapeHtml(name)).length - 1, 3);
	assert.ok(!html.includes('hq-truncate'));
	assert.ok(!html.includes('<Cabang'));
	assert.ok(html.includes('class="hq-kpi-value">IDR 100'));
});

test('name style wraps even unbroken identifiers without clipping or line clamps', () => {
	const rule = css.match(/\.hq-mini \.hq-kpi-name\s*\{([^}]+)\}/)[1];
	assert.match(rule, /white-space:\s*normal/);
	assert.match(rule, /overflow-wrap:\s*anywhere/);
	assert.doesNotMatch(rule, /ellipsis|line-clamp|max-height|overflow:\s*hidden/);
	assert.match(css, /grid-template-columns:\s*repeat\(4, minmax\(0, 1fr\)\)/);
	assert.match(css, /\.hq-money\s*\{\s*white-space:\s*nowrap/);
});
