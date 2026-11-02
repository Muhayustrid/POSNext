const assert = require("node:assert/strict");
const { test, describe, beforeEach } = require("node:test");

let handlers = {};
let calls = [];
let messages = [];

global.__ = (s, args) => {
	if (!args || !args.length) return s;
	return s.replace(/\{(\d+)\}/g, (_, i) => args[i] ?? `{${i}}`);
};
global.flt = (v) => parseFloat(v) || 0;
global.cint = (v) => parseInt(v) || 0;
global.locals = {};
global.format_currency = (v, c) => `${c || "IDR"} ${v}`;

global.frappe = {
	ui: {
		form: {
			on: (doctype, h) => {
				handlers[doctype] = Object.assign(handlers[doctype] || {}, h);
			},
		},
	},
	model: {
		set_value: (cdt, cdn, fieldname, value) => {
			if (!locals[cdt]) locals[cdt] = {};
			if (!locals[cdt][cdn]) locals[cdt][cdn] = {};
			if (typeof fieldname === "object") {
				Object.assign(locals[cdt][cdn], fieldname);
			} else {
				locals[cdt][cdn][fieldname] = value;
			}
		},
	},
	utils: { escape_html: (s) => s },
	datetime: {
		now_date: () => "2026-11-02",
		system_datetime: () => "2026-11-02 10:00:00",
	},
	format_value: (v) => v,
	msgprint: (msg) => {
		messages.push(msg);
	},
	call: (opts) => {
		// like frappe.request: success runs callback, then always
		const cb = opts.callback;
		opts.callback = (r) => {
			if (cb) cb(r);
			if (opts.always) opts.always(r);
		};
		calls.push(opts);
	},
};

const {
	load_shift_context,
	clear_entry_data,
	recompute_closing_payments,
} = require("../../pos_next/doctype/pos_backdate_entry/pos_backdate_entry.js");

function make_frm(is_new = true, initial_doc = {}) {
	const doc = Object.assign(
		{
			docstatus: 0,
			pos_opening_shift: "POS-OPEN-001",
			invoices: [],
			items: [],
			payments: [],
			closing_payments: [],
			cancelled_closing_shift: "",
		},
		initial_doc,
	);
	const frm = {
		is_new: () => is_new,
		doc: doc,
		_touched_closing_modes: new Set(),
		_touched_payment_rows: new Set(),
		_shift_context: null,
		_cash_mode_of_payment: "Cash",
		_dirty: false,
		_df_props: {},
		dirty: () => {
			frm._dirty = true;
		},
		set_df_property: (field, prop, val) => {
			frm._df_props[`${field}.${prop}`] = val;
		},
		fields_dict: {
			shift_invoices_html: {
				$wrapper: {
					empty: () => {},
					html: () => {},
					find: () => ({ on: () => {} }),
				},
			},
			items: { grid: { update_docfield_property: () => {} } },
			payments: { grid: { update_docfield_property: () => {} } },
		},
		set_value: (k, v) => {
			doc[k] = v;
		},
		add_child: (table, row) => {
			if (!doc[table]) doc[table] = [];
			doc[table].push(row);
			return row;
		},
		refresh_field: () => {},
		refresh_fields: () => {},
	};
	return frm;
}

describe("pos_backdate_entry Desk form shift context", () => {
	beforeEach(() => {
		calls = [];
	});

	test("two get_shift_context responses do not overwrite pinned closing with stale amounts", () => {
		const frm = make_frm(true, { pos_opening_shift: "POS-OPEN-001" });

		// Trigger load_shift_context
		load_shift_context(frm);
		assert.equal(calls.length, 1);
		const call1 = calls[0];

		// Response 1 arrives with CLOSE-1 and Cash 100
		const ctx1 = {
			shift: { pos_closing_shift: "POS-CLS-001", company: "Test Co", pos_profile: "Prof" },
			closing_counts: [
				{ mode_of_payment: "Cash", opening_amount: 0, expected_amount: 100, closing_amount: 100 },
			],
			payment_modes: ["Cash"],
		};
		call1.callback({ message: ctx1 });

		// Verify form is pinned to CLOSE-1 with Cash 100
		assert.equal(frm.doc.cancelled_closing_shift, "POS-CLS-001");
		assert.equal(frm.doc.closing_payments.length, 1);
		assert.equal(frm.doc.closing_payments[0].closing_amount, 100);

		// Now another backdate submitted in background: shift current closing is now CLOSE-2
		// Response 2 arrives with CLOSE-2 and Cash 200
		const ctx2 = {
			shift: { pos_closing_shift: "POS-CLS-002", company: "Test Co", pos_profile: "Prof" },
			closing_counts: [
				{ mode_of_payment: "Cash", opening_amount: 0, expected_amount: 200, closing_amount: 200 },
			],
			payment_modes: ["Cash"],
		};

		// Call 2 callback is invoked (simulating late arriving second response)
		call1.callback({ message: ctx2 });

		// Invariant: cancelled_closing_shift MUST NOT be updated to POS-CLS-002
		// while closing_payments contains nominals from POS-CLS-001!
		// The pin remains POS-CLS-001 (matching the data used), so the server's
		// stale guard will reject submit with STALE_MSG.
		assert.equal(frm.doc.cancelled_closing_shift, "POS-CLS-001");
		assert.equal(frm.doc.closing_payments[0].closing_amount, 100);
		assert.notEqual(frm.doc.cancelled_closing_shift, "POS-CLS-002");
	});

	test("changing POS Opening Shift on saved draft clears pin and updates to new shift closing", () => {
		// Existing saved draft prepared against shift 1 / CLOSE-1
		const frm = make_frm(false, {
			pos_opening_shift: "POS-OPEN-001",
			cancelled_closing_shift: "POS-CLS-001",
			closing_payments: [
				{ mode_of_payment: "Cash", opening_amount: 0, expected_amount: 100, closing_amount: 100 },
			],
		});

		// User changes opening shift to POS-OPEN-002: clear_entry_data runs
		frm.doc.pos_opening_shift = "POS-OPEN-002";
		clear_entry_data(frm);

		assert.equal(frm.doc.cancelled_closing_shift, "");
		assert.equal(frm.doc.closing_payments.length, 0);

		// load_shift_context runs for POS-OPEN-002
		load_shift_context(frm);
		assert.equal(calls.length, 1);

		const ctx_new = {
			shift: { pos_closing_shift: "POS-CLS-002", company: "Test Co", pos_profile: "Prof" },
			closing_counts: [
				{ mode_of_payment: "Cash", opening_amount: 0, expected_amount: 300, closing_amount: 300 },
			],
			payment_modes: ["Cash"],
		};
		calls[0].callback({ message: ctx_new });

		// Pin and nominals are now both updated to POS-CLS-002
		assert.equal(frm.doc.cancelled_closing_shift, "POS-CLS-002");
		assert.equal(frm.doc.closing_payments.length, 1);
		assert.equal(frm.doc.closing_payments[0].closing_amount, 300);
	});

	test("A -> B -> A: the first A response cannot overwrite the latest request", () => {
		const frm = make_frm(false, { pos_opening_shift: "POS-OPEN-A" });
		load_shift_context(frm);
		frm.doc.pos_opening_shift = "POS-OPEN-B";
		clear_entry_data(frm);
		load_shift_context(frm);
		frm.doc.pos_opening_shift = "POS-OPEN-A";
		clear_entry_data(frm);
		load_shift_context(frm);
		assert.equal(calls.length, 3);
		const ctx = (closing) => ({
			shift: { pos_closing_shift: closing, company: "Test Co", pos_profile: "Prof" },
			current_closing: closing,
			closing_counts: [{ mode_of_payment: "Cash", opening_amount: 0, expected_amount: 10, closing_amount: 10 }],
			payment_modes: ["Cash"],
		});
		calls[0].callback({ message: ctx("POS-CLS-A-OLD") });
		assert.equal(frm._shift_context, null);
		assert.equal(frm._loading_shift_context, true);
		calls[2].callback({ message: ctx("POS-CLS-A-NEW") });
		assert.equal(frm.doc.cancelled_closing_shift, "POS-CLS-A-NEW");
		assert.equal(frm._loading_shift_context, false);
	});

	test("a failed request without error callback still allows reloading", () => {
		const frm = make_frm(false, { pos_opening_shift: "POS-OPEN-001" });
		load_shift_context(frm);
		calls[0].always(null); // frappe handles timeouts/deadlocks itself, only always fires
		load_shift_context(frm);
		assert.equal(calls.length, 2);
	});

	test("late response for a previously selected shift is ignored", () => {
		const frm = make_frm(false, { pos_opening_shift: "POS-OPEN-A" });
		load_shift_context(frm);
		frm.doc.pos_opening_shift = "POS-OPEN-B";
		clear_entry_data(frm);
		load_shift_context(frm);
		assert.equal(calls.length, 2);
		const ctx = (closing) => ({
			shift: { pos_closing_shift: closing, company: "Test Co", pos_profile: "Prof" },
			current_closing: closing,
			closing_counts: [{ mode_of_payment: "Cash", opening_amount: 0, expected_amount: 10, closing_amount: 10 }],
			payment_modes: ["Cash"],
		});
		calls[0].callback({ message: ctx("POS-CLS-A") });
		assert.equal(frm._shift_context, null);
		calls[1].callback({ message: ctx("POS-CLS-B") });
		assert.equal(frm.doc.cancelled_closing_shift, "POS-CLS-B");
	});

	test("load_shift_context does not issue duplicate call while request is in flight", () => {
		const frm = make_frm(true, { pos_opening_shift: "POS-OPEN-001" });

		load_shift_context(frm);
		assert.equal(calls.length, 1);
		assert.equal(frm._loading_shift_context, true);

		// Calling load_shift_context again while in flight for same shift should be a no-op
		load_shift_context(frm);
		assert.equal(calls.length, 1);

		// When callback finishes, flag is cleared
		calls[0].callback({
			message: {
				shift: { pos_closing_shift: "POS-CLS-001" },
				closing_counts: [{ mode_of_payment: "Cash", opening_amount: 0, expected_amount: 50, closing_amount: 50 }],
				payment_modes: ["Cash"],
			},
		});
		assert.equal(frm._loading_shift_context, false);
	});

	test("refresh does not call load_shift_context when onload already has request in flight", () => {
		const frm = make_frm(true, { pos_opening_shift: "POS-OPEN-001" });
		frm.add_custom_button = () => {};

		// Simulate onload triggering load_shift_context
		handlers["POS Backdate Entry"].onload(frm);
		assert.equal(calls.length, 1);
		assert.equal(frm._loading_shift_context, true);

		// Immediate refresh while request in flight does not trigger second call
		handlers["POS Backdate Entry"].refresh(frm);
		assert.equal(calls.length, 1);
	});

	test("default closing date/time on new form from closing_period_end", () => {
		const frm = make_frm(true, { pos_opening_shift: "POS-OPEN-001" });
		load_shift_context(frm);
		calls[0].callback({
			message: {
				shift: { pos_closing_shift: "POS-CLS-001", company: "Test Co", pos_profile: "Prof" },
				current_closing: "POS-CLS-001",
				closing_period_end: "2026-11-01 18:30:00",
				closing_counts: [{ mode_of_payment: "Cash", opening_amount: 0, expected_amount: 100, closing_amount: 100 }],
				payment_modes: ["Cash"],
			},
		});
		assert.equal(frm.doc.closing_date, "2026-11-01");
		assert.equal(frm.doc.closing_time, "18:30:00");
	});

	test("default closing date/time uses moment when available", () => {
		global.moment = (str) => ({
			isValid: () => true,
			format: (fmt) => (fmt === "YYYY-MM-DD" ? "2026-11-01" : "18:30:00"),
		});
		try {
			const frm = make_frm(true, { pos_opening_shift: "POS-OPEN-001" });
			load_shift_context(frm);
			calls[0].callback({
				message: {
					shift: { pos_closing_shift: "POS-CLS-001" },
					current_closing: "POS-CLS-001",
					closing_period_end: "2026-11-01 18:30:00",
					closing_counts: [],
				},
			});
			assert.equal(frm.doc.closing_date, "2026-11-01");
			assert.equal(frm.doc.closing_time, "18:30:00");
		} finally {
			delete global.moment;
		}
	});

	test("stale saved draft re-bases cancelled_closing_shift, recalcs closing_payments, marks dirty, and warns", () => {
		messages = [];
		const frm = make_frm(false, {
			pos_opening_shift: "POS-OPEN-001",
			cancelled_closing_shift: "POS-CLS-001",
			closing_payments: [{ mode_of_payment: "Cash", opening_amount: 0, expected_amount: 100, closing_amount: 80, difference: -20 }],
		});
		load_shift_context(frm);
		calls[0].callback({
			message: {
				shift: { pos_closing_shift: "POS-CLS-002", company: "Test Co", pos_profile: "Prof" },
				current_closing: "POS-CLS-002",
				closing_counts: [{ mode_of_payment: "Cash", opening_amount: 0, expected_amount: 250, closing_amount: 200, difference: -50 }],
				payment_modes: ["Cash"],
			},
		});
		assert.equal(frm.doc.cancelled_closing_shift, "POS-CLS-002");
		assert.equal(frm.doc.closing_payments.length, 1);
		assert.equal(frm.doc.closing_payments[0].expected_amount, 250);
		assert.equal(frm.doc.closing_payments[0].closing_amount, 250);
		assert.equal(frm._dirty, true);
		assert.equal(messages.length, 1);
		assert.match(messages[0], /POS-CLS-002/);
	});

	test("recompute_closing_payments defaults closing_amount to expected for all modes and sets cash hint", () => {
		const frm = make_frm(true, { pos_opening_shift: "POS-OPEN-001" });
		frm._shift_context = {
			shift: { pos_closing_shift: "POS-CLS-001" },
			currency: "IDR",
			closing_counts: [
				{ mode_of_payment: "Cash", opening_amount: 0, expected_amount: 100, closing_amount: 80 },
				{ mode_of_payment: "Bank", opening_amount: 0, expected_amount: 200, closing_amount: 200 },
			],
			payment_modes: ["Cash", "Bank"],
		};
		recompute_closing_payments(frm);
		const cash = frm.doc.closing_payments.find((r) => r.mode_of_payment === "Cash");
		const bank = frm.doc.closing_payments.find((r) => r.mode_of_payment === "Bank");
		// Both default to expected_amount (cash is 100, not old closing 80)
		assert.equal(cash.closing_amount, 100);
		assert.equal(bank.closing_amount, 200);
		// Description hint shows old count
		assert.equal(frm._df_props["closing_payments.description"], "Previously counted Cash: IDR 80");
	});

	test("fetch_packages receives historical closing date from closing_period_end", () => {
		const frm = make_frm(true, { pos_opening_shift: "POS-OPEN-001" });
		load_shift_context(frm);
		calls[0].callback({
			message: {
				shift: { pos_closing_shift: "POS-CLS-001", company: "Test Co", pos_profile: "Prof" },
				current_closing: "POS-CLS-001",
				closing_period_end: "2026-10-15 18:30:00",
				closing_counts: [],
				payment_modes: [],
			},
		});
		const pkg_call = calls.find(
			(c) => c.method === "pos_next.api.backdate_invoices.get_backdate_packages",
		);
		assert.ok(pkg_call, "fetch_packages should have been called");
		assert.equal(pkg_call.args.posting_date, "2026-10-15");
	});

	test("saved draft with edited closing amount preserves closing amount on reopen and recompute", () => {
		const frm = make_frm(false, {
			pos_opening_shift: "POS-OPEN-001",
			cancelled_closing_shift: "POS-CLS-001",
			invoices: [{ invoice_no: 1, entry_type: "Sale", grand_total: 50 }],
			payments: [{ invoice_no: 1, mode_of_payment: "Cash", amount: 50 }],
			closing_payments: [
				{
					mode_of_payment: "Cash",
					opening_amount: 0,
					expected_amount: 100,
					closing_amount: 80,
					difference: -20,
				},
				{
					mode_of_payment: "Bank",
					opening_amount: 0,
					expected_amount: 200,
					closing_amount: 200,
					difference: 0,
				},
			],
		});
		frm._touched_closing_modes = new Set();

		handlers["POS Backdate Entry"].onload(frm);

		calls[0].callback({
			message: {
				shift: { pos_closing_shift: "POS-CLS-001", company: "Test Co", pos_profile: "Prof" },
				current_closing: "POS-CLS-001",
				currency: "IDR",
				closing_counts: [
					{ mode_of_payment: "Cash", opening_amount: 0, expected_amount: 100, closing_amount: 100 },
					{ mode_of_payment: "Bank", opening_amount: 0, expected_amount: 200, closing_amount: 200 },
				],
				payment_modes: ["Cash", "Bank"],
			},
		});

		assert.ok(
			frm._touched_closing_modes.has("Cash"),
			"Cash should be recognized as touched from saved draft",
		);
		assert.ok(
			!frm._touched_closing_modes.has("Bank"),
			"a saved amount equal to expected keeps following expected",
		);

		recompute_closing_payments(frm);

		const cash = frm.doc.closing_payments.find((r) => r.mode_of_payment === "Cash");
		const bank = frm.doc.closing_payments.find((r) => r.mode_of_payment === "Bank");

		assert.equal(cash.closing_amount, 80);
		assert.equal(cash.expected_amount, 150);
		assert.equal(cash.difference, -70);

		assert.equal(bank.closing_amount, 200);
		assert.equal(bank.expected_amount, 200);
	});
});
