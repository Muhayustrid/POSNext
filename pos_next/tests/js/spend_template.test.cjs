const assert = require("node:assert/strict");
const { test } = require("node:test");
let handlers;
let html;
let calls = 0;
global.__ = (value) => value;
global.frappe = {
	ui: {
		form: { on: (_doctype, value) => { handlers = value; } },
		Dialog: class {
			constructor() { this.fields_dict = { results: { $wrapper: { html(value) { html = value; } } } }; }
			show() {}
		},
	},
	utils: { escape_html: (value) => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;") },
	msgprint() {},
	async call() {
		calls++;
		return { message: [{ company: "<img src=x>", ready: false, accounts: { debit: "<script>" }, reasons: ["<reason>"], warnings: ["<warning>"] }] };
	},
};
require("../../pos_next/doctype/pos_spend_template/pos_spend_template.js");

test("company scope explains selection and preserves override rows", () => {
	const rows = [{ company: "Outlet", debit_account: "Expense" }];
	const descriptions = [];
	const frm = {
		doc: { companies: rows }, is_new: () => true,
		trigger(event) { handlers[event](this); },
		set_df_property(field, property, value) { descriptions.push({ field, property, value }); },
	};
	handlers.refresh(frm);
	assert.ok(descriptions.at(-1)?.value.includes("Select companies"));
	frm.doc.company_scope = "All Companies";
	handlers.company_scope(frm);
	assert.ok(descriptions.at(-1).value.includes("including new ones"));
	assert.equal(frm.doc.companies, rows);
	assert.ok(descriptions.every(({ field, property }) => field === "companies" && property === "description"));
	frm.doc.company_scope = "Selected Companies";
	handlers.company_scope(frm);
	assert.ok(descriptions.at(-1).value.includes("Select companies"));
	assert.equal(frm.doc.companies, rows);
});

test("reference query follows selected company", () => {
	let query;
	const frm = { doc: { reference_company: "Ref" }, set_query(_field, callback) { query = callback; } };
	handlers.setup(frm);
	assert.deepEqual(query(), { filters: { company: "Ref", is_group: 0 } });
});

test("readiness escapes data and does not check unsaved changes", async () => {
	let action;
	const frm = { trigger(event) { handlers[event](this); }, set_df_property() {}, doc: { name: "Spend" }, is_new: () => false, is_dirty: () => false, add_custom_button(_label, callback) { action = callback; } };
	handlers.refresh(frm);
	await action();
	assert.equal(calls, 1);
	assert.ok(html.includes("&lt;img src=x&gt;"));
	assert.ok(html.includes("&lt;script&gt;"));
	assert.ok(html.includes("&lt;reason&gt;"));
	assert.ok(html.includes("&lt;warning&gt;"));
	assert.ok(!html.includes("<img"));
	frm.is_dirty = () => true;
	await action();
	assert.equal(calls, 1);
});
