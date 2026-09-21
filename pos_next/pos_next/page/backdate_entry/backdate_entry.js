// Copyright (c) 2026, POS Next and contributors
// For license information, please see license.txt

// Desk page for HO backdate entry — the SPA kasir dialog
// (BackdateEntryDialog.vue) ported to vanilla Frappe. Every payload shape and
// support endpoint mirrors the dialog exactly; the server owns every gate
// (role, POS Settings flag, posting-date window) and its errors (including
// NegativeStockError) surface as-is through frappe.call's default msgprint.

const API = "pos_next.api.backdate_invoices";

frappe.pages["backdate-entry"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Backdate Entry"),
		single_column: true,
	});
	page.add_menu_item(__("Refresh"), () => wrapper.backdate_entry && wrapper.backdate_entry.refresh());
	wrapper.backdate_entry = new BackdateEntry(page);
};

class BackdateEntry {
	constructor(page) {
		this.page = page;
		this.shifts = [];
		this.today = frappe.datetime.get_today();
		this.outlet = "";
		// shifts reopened in this session — a fresh context only lists Closed
		// shifts, so the in-memory row keeps them reachable ("Continue Entry").
		this.reopened = {};
		this.view = "outlets"; // outlets | entry | done
		this.entry_mode = "sale"; // sale | return
		this.shift = null;
		this.payment_modes = [];
		this.support_loaded = false;
		this.amount_touched = false;
		this.refund_touched = false;
		this.$root = $('<div class="bd-root">').appendTo(page.main);
		this._restore_state();
		this._check_access();
	}

	// ------------------------------------------------------------------
	// Progress persistence: Desk destroys the page on navigation, so the
	// reopened shifts and an in-flight entry survive in localStorage (per
	// user). The confirmation code is deliberately NOT persisted — it is a
	// discount/refund credential and gets re-verified after a restore.
	// ------------------------------------------------------------------

	_storage_key() {
		return "pos_next:backdate_entry:" + (frappe.session.user || "");
	}

	_save_state() {
		if (!window.localStorage) return;
		const in_entry = this.shift && (this.view === "entry" || this.view === "done");
		const entry = in_entry
			? {
					shift: this.shift,
					entry_mode: this.entry_mode,
					posting_date: this.posting_date,
					customer: this.customer,
					sale_rows: this.sale_rows,
					payment_mode: this.payment_mode,
					payment_amount: this.payment_amount,
					amount_touched: this.amount_touched,
					original_invoice: this.original_invoice,
					prepared_doc: this.prepared_doc,
					return_rows: this.return_rows,
					refund_mode: this.refund_mode,
					refund_amount: this.refund_amount,
					refund_touched: this.refund_touched,
				}
			: null;
		try {
			localStorage.setItem(
				this._storage_key(),
				JSON.stringify({ outlet: this.outlet, reopened: this.reopened, entry })
			);
		} catch (e) {
			/* private mode / quota — persistence is best-effort */
		}
	}

	_clear_entry_state() {
		if (!window.localStorage) return;
		try {
			const saved = JSON.parse(localStorage.getItem(this._storage_key()) || "{}");
			saved.entry = null;
			localStorage.setItem(this._storage_key(), JSON.stringify(saved));
		} catch (e) {
			/* best-effort */
		}
	}

	_restore_state() {
		let saved = null;
		try {
			saved = JSON.parse(localStorage.getItem(this._storage_key()) || "null");
		} catch (e) {
			return;
		}
		if (!saved) return;
		this.outlet = saved.outlet || this.outlet;
		this.reopened = saved.reopened || {};
		if (!saved.entry || !saved.entry.shift) return;
		const entry = saved.entry;
		this.shift = entry.shift;
		this.view = "entry";
		this.entry_mode = entry.entry_mode === "return" ? "return" : "sale";
		this.posting_date =
			entry.posting_date ||
			this.shift.period_start_date ||
			this.shift.posting_date ||
			this.today;
		this.customer = entry.customer || "";
		this.sale_rows = entry.sale_rows || [];
		this.payment_amount = entry.payment_amount || 0;
		this.amount_touched = Boolean(entry.amount_touched);
		this.original_invoice = entry.original_invoice || null;
		this.prepared_doc = entry.prepared_doc || null;
		this.return_rows = entry.return_rows || [];
		this.refund_amount = entry.refund_amount || 0;
		this.refund_touched = Boolean(entry.refund_touched);
		this.payment_mode = entry.payment_mode || "";
		this.refund_mode = entry.refund_mode || this.payment_mode;
	}

	refresh() {
		if (this.view === "outlets") this._load_context();
	}

	// ------------------------------------------------------------------
	// Gate: role check first; the per-outlet POS Settings flag shows as a
	// badge per shift and is enforced server-side on every action.
	// ------------------------------------------------------------------

	_check_access() {
		frappe.call({
			method: `${API}.get_access`,
			callback: (r) => {
				if (r.message && r.message.roles_ok) this._setup();
				else this._denied();
			},
		});
	}

	_denied() {
		this.$root.html(
			`<div class="bd-card"><p class="bd-muted">${__("Not permitted to enter backdated invoices")}</p></div>`
		);
	}

	_setup() {
		this.$root.html(`
			<div class="bd-hint">${__(
				"Select an outlet with a closed shift to reopen for backdate entry"
			)}</div>
			<div class="bd-body"></div>`);
		this.$body = this.$root.find(".bd-body");
		if (this.view === "entry" && this.shift) {
			// restored mid-entry (Desk navigation): resume the form, refresh
			// the closed-shift list in the background for the Back view
			this._render_entry();
			this._load_support();
			this._load_refund_gate();
			this._load_context();
			return;
		}
		this._load_context();
	}

	_load_context() {
		if (this.view !== "entry") {
			this.$body.html(`<p class="bd-center bd-muted">${__("Loading...")}</p>`);
		}
		frappe.call({
			method: `${API}.get_backdate_context`,
			callback: (r) => {
				this.shifts = (r.message && r.message.shifts) || [];
				this.today = (r.message && r.message.today) || this.today;
				// a shift that has been closed again elsewhere returns to the
				// Closed list — drop our stale reopened copy of it
				for (const name of Object.keys(this.reopened)) {
					if (this.shifts.some((s) => s.name === name)) delete this.reopened[name];
				}
				if (this.view !== "entry") this._render_outlets();
				else this._save_state();
			},
		});
	}

	// ------------------------------------------------------------------
	// Outlet + closed-shift list (shifts grouped per profile; one POS
	// Settings row per profile so every shift of an outlet agrees)
	// ------------------------------------------------------------------

	_outlets() {
		const map = new Map();
		for (const shift of this.shifts.concat(Object.values(this.reopened))) {
			if (!map.has(shift.pos_profile)) {
				map.set(shift.pos_profile, {
					pos_profile: shift.pos_profile,
					setting_on: false,
					shifts: [],
				});
			}
			const outlet = map.get(shift.pos_profile);
			outlet.shifts.push(shift);
			outlet.setting_on = outlet.setting_on || Boolean(shift.setting_on);
		}
		return [...map.values()];
	}

	_render_outlets() {
		this.view = "outlets";
		this.shift = null;
		const outlets = this._outlets();
		if (!outlets.length) {
			this.$body.html(
				`<div class="bd-card"><p class="bd-center bd-muted">${__(
					"No closed shifts available for backdate entry"
				)}</p></div>`
			);
			return;
		}
		if (!outlets.find((o) => o.pos_profile === this.outlet)) {
			this.outlet = outlets[0].pos_profile;
		}
		const options = outlets
			.map(
				(o) =>
					`<option value="${frappe.utils.escape_html(o.pos_profile)}"${
						o.pos_profile === this.outlet ? " selected" : ""
					}>${frappe.utils.escape_html(o.pos_profile)} (${o.shifts.length})</option>`
			)
			.join("");
		const selected = outlets.find((o) => o.pos_profile === this.outlet);
		const rows = selected.shifts.map((shift) => this._shift_row(shift)).join("");
		const badge = selected.setting_on
			? `<span class="bd-badge bd-badge--on">${__("Backdate entry enabled")}</span>`
			: `<span class="bd-badge">${__("Backdate entry is disabled for this outlet")}</span>`;
		this.$body.html(`
			<div class="bd-toolbar">
				<label class="bd-label">${__("Outlet")}
					<select class="bd-select" data-bd-outlet>${options}</select></label>
				${badge}
			</div>
			<div class="bd-list">${rows}</div>`);
		this._bind_outlets(selected);
	}

	_shift_row(shift) {
		const period = `${frappe.datetime.str_to_user(shift.period_start_date)}${
			shift.period_end_date ? ` — ${frappe.datetime.str_to_user(shift.period_end_date)}` : ""
		}`;
		const closing = shift.pos_closing_shift
			? ` · <a href="/app/pos-closing-shift/${encodeURIComponent(shift.pos_closing_shift)}" target="_blank">${frappe.utils.escape_html(
					shift.pos_closing_shift
				)}</a>`
			: "";
		const badge = shift._reopened
			? ` <span class="bd-badge bd-badge--on">${__("Reopened")}</span>`
			: "";
		const button = shift._reopened
			? `<button class="btn btn-primary btn-sm" data-bd-continue="${frappe.utils.escape_html(shift.name)}">${__(
					"Continue Entry"
				)}</button>`
			: shift.setting_on
				? `<button class="btn btn-primary btn-sm" data-bd-reopen="${frappe.utils.escape_html(shift.name)}">${__(
						"Reopen Shift"
					)}</button>`
				: `<button class="btn btn-default btn-sm" disabled title="${__(
						"Backdate entry is disabled for this outlet"
					)}">${__("Reopen Shift")}</button>`;
		return `<div class="bd-card bd-row" data-bd-shift="${frappe.utils.escape_html(shift.name)}">
			<div class="bd-row-main">
				<div class="bd-row-title"><b>${frappe.utils.escape_html(shift.name)}</b>${badge}
					<span class="bd-muted">${__("Cashier")}: ${frappe.utils.escape_html(shift.user || "")}</span></div>
				<div class="bd-row-sub">${period}${closing}</div>
			</div>
			<div class="bd-row-side">${button}</div>
		</div>`;
	}

	_bind_outlets(selected) {
		this.$body
			.off("change", "[data-bd-outlet]")
			.on("change", "[data-bd-outlet]", (e) => {
				this.outlet = e.currentTarget.value;
				this._render_outlets();
			})
			.off("click", "[data-bd-reopen]")
			.on("click", "[data-bd-reopen]", (e) => {
				const shift = selected.shifts.find((s) => s.name === e.currentTarget.dataset.bdReopen);
				this._confirm_reopen(shift);
			})
			.off("click", "[data-bd-continue]")
			.on("click", "[data-bd-continue]", (e) => {
				const shift = selected.shifts.find((s) => s.name === e.currentTarget.dataset.bdContinue);
				if (shift) this._open_entry(shift);
			});
	}

	_confirm_reopen(shift) {
		frappe.confirm(
			__(
				"This cancels the submitted closing shift {0}. The shift stays open until it is closed again — finish the backdate entries, then re-close it.",
				[shift.pos_closing_shift]
			),
			() => {
				frappe.call({
					method: `${API}.reopen_shift`,
					args: { pos_opening_shift: shift.name },
					callback: () => {
						// keep the in-memory row so the list still offers it
						this.reopened[shift.name] = Object.assign({}, shift, { _reopened: true });
						this._delete_row(this.shifts, shift.name);
						frappe.show_alert({ message: __("Shift {0} reopened", [shift.name]), indicator: "green" });
						this._open_entry(this.reopened[shift.name]);
					},
				});
			},
			null,
			__("Yes, Reopen Shift")
		);
	}

	_delete_row(list, name) {
		const i = list.findIndex((s) => s.name === name);
		if (i > -1) list.splice(i, 1);
	}

	// ------------------------------------------------------------------
	// Entry form: sale or return on the reopened shift. The payload shapes
	// below are copied 1:1 from BackdateEntryDialog.vue — do not wing them.
	// ------------------------------------------------------------------

	_open_entry(shift) {
		this.shift = shift;
		this.view = "entry";
		this.entry_mode = "sale";
		this.posting_date = shift.period_start_date || shift.posting_date || this.today;
		this.customer = "";
		this.sale_rows = [];
		this.item_results = [];
		this.payment_amount = 0;
		this.amount_touched = false;
		this.invoice_results = [];
		this.prepared_doc = null;
		this.original_invoice = null;
		this.return_rows = [];
		this.refund_amount = 0;
		this.refund_touched = false;
		this.payment_mode = "";
		this.refund_mode = "";
		this.refund_code = "";
		this.refund_code_verified = false;
		this.refund_code_error = "";
		// fail closed — the field shows until get_status answers otherwise
		this.refund_code_required = true;
		this._render_entry();
		this._load_support();
		this._load_refund_gate();
	}

	_shift_start() {
		return this.shift.period_start_date || this.shift.posting_date || "";
	}

	_money(v) {
		const n = Number(v) || 0;
		// display only — desk global, falls back to a plain number
		return typeof format_currency === "function" ? format_currency(n) : String(n);
	}

	_render_entry() {
		this.$body.html(`
			<div class="bd-toolbar">
				<button class="btn btn-default btn-sm" data-bd-back>${__("Back")}</button>
				<span class="bd-muted">${frappe.utils.escape_html(this.shift.pos_profile)} · ${frappe.utils.escape_html(
					this.shift.name
				)} · ${frappe.datetime.str_to_user(this._shift_start())}</span>
			</div>
			<div class="bd-card">
				<div class="bd-grid2">
					<div>
						<label class="bd-label" for="bd-posting-date">${__("Posting Date")}</label>
						<input class="bd-input" id="bd-posting-date" type="date" data-bd-posting-date
							value="${frappe.utils.escape_html(this.posting_date)}"
							min="${frappe.utils.escape_html(this._shift_start())}"
							max="${frappe.utils.escape_html(this.today)}" />
						<p class="bd-hint">${__("Defaults to the shift date ({0})", [
							frappe.datetime.str_to_user(this._shift_start()),
						])}</p>
					</div>
					<div>
						<label class="bd-label">${__("Entry Type")}</label>
						<div class="bd-seg">
							<button type="button" class="btn btn-sm ${this.entry_mode === "sale" ? "btn-primary" : "btn-default"}"
								data-bd-mode="sale">${__("Sale")}</button>
							<button type="button" class="btn btn-sm ${this.entry_mode === "return" ? "btn-primary" : "btn-default"}"
								data-bd-mode="return">${__("Return")}</button>
						</div>
					</div>
				</div>
				<div data-bd-panel></div>
			</div>`);
		this.$panel = this.$body.find("[data-bd-panel]");
		this.entry_mode === "sale" ? this._render_sale() : this._render_return();

		this.$body
			.off("click", "[data-bd-back]")
			.on("click", "[data-bd-back]", () => {
				// explicit exit — drop the saved entry, keep reopened shifts
				this._clear_entry_state();
				this._render_outlets();
			})
			.off("input", "[data-bd-posting-date]")
			.on("input", "[data-bd-posting-date]", (e) => {
				this.posting_date = e.currentTarget.value;
				this._save_state();
			})
			.off("click", "[data-bd-mode]")
			.on("click", "[data-bd-mode]", (e) => {
				this.entry_mode = e.currentTarget.dataset.bdMode;
				this._render_entry();
			});
		this._save_state();
	}

	// --- SALE ---

	_render_sale() {
		this.$panel.html(`
			<div class="bd-grid2">
				<div class="bd-search-wrap">
					<label class="bd-label">${__("Customer")}</label>
					<input class="bd-input" type="search" autocomplete="off"
						placeholder="${__("Search customer...")}" data-bd-customer />
					<div class="bd-menu" data-bd-customer-menu hidden></div>
				</div>
				<div class="bd-search-wrap">
					<label class="bd-label">${__("Items")}</label>
					<input class="bd-input" type="search" autocomplete="off"
						placeholder="${__("Search item...")}" data-bd-item />
					<div class="bd-menu" data-bd-item-menu hidden></div>
				</div>
			</div>
			<div data-bd-sale-rows></div>
			<div class="bd-grid2">
				<div>
					<label class="bd-label" for="bd-payment-mode">${__("Payment Mode")}</label>
					<select class="bd-select" id="bd-payment-mode" data-bd-payment-mode></select>
				</div>
				<div>
					<label class="bd-label" for="bd-payment-amount">${__("Amount")}</label>
					<input class="bd-input" id="bd-payment-amount" type="number" min="0" step="any"
						data-bd-payment-amount value="${this.payment_amount || 0}" />
				</div>
			</div>
			<div class="bd-search-wrap" data-bd-sale-code-wrap ${this._sale_has_discount() ? "" : "hidden"}>
				<label class="bd-label" for="bd-sale-code">${__("Confirmation Code (HQ)")} *</label>
				<div class="bd-code-row">
					<input class="bd-input" id="bd-sale-code" type="text" autocomplete="off" maxlength="8"
						placeholder="${__("Enter the code from head office")}" data-bd-refund-code
						value="${frappe.utils.escape_html(this.refund_code || "")}"
						${this.refund_code_verified ? "disabled" : ""} />
					<button type="button" class="btn btn-sm btn-default" data-bd-verify-code
						${this.refund_code_verified ? "disabled" : ""}>${this.refund_code_verified ? "✓" : __("Verify")}</button>
				</div>
				<p class="bd-hint" data-bd-refund-code-msg></p>
			</div>
			<div class="bd-actions">
				<button class="btn btn-primary btn-sm" data-bd-submit>${__("Submit Sale")}</button>
			</div>`);

		this._render_sale_rows();
		this._bind_sale();
	}

	_render_sale_rows() {
		const rows = this.sale_rows
			.map(
				(row, idx) => `<tr>
				<td>${frappe.utils.escape_html(row.item_name)}</td>
				<td><input type="number" min="0" step="any" class="bd-input bd-input--qty" data-bd-qty="${idx}"
					value="${row.qty}" aria-label="${frappe.utils.escape_html(row.item_name)} — ${__("Qty")}" /></td>
				<td class="bd-num">${this._money(row.rate)}</td>
				<td><input type="number" min="0" max="100" step="any" class="bd-input bd-input--qty" data-bd-disc="${idx}"
					value="${row.discount_percentage || 0}" aria-label="${frappe.utils.escape_html(row.item_name)} — ${__("Discount")} %" /></td>
				<td><button type="button" class="btn btn-link btn-sm" data-bd-remove="${idx}">×</button></td>
			</tr>`
			)
			.join("");
		const total = this.sale_rows.reduce(
			(sum, row) =>
				sum +
				(Number(row.qty) || 0) *
					(Number(row.rate) || 0) *
					(1 - (Number(row.discount_percentage) || 0) / 100),
			0
		);
		this.$body.find("[data-bd-sale-rows]").html(
			this.sale_rows.length
				? `<table class="bd-table">
					<thead><tr><th>${__("Item")}</th><th class="bd-num">${__("Qty")}</th>
					<th class="bd-num">${__("Rate")}</th><th class="bd-num">${__("Discount")} %</th><th></th></tr></thead>
					<tbody>${rows}</tbody></table>
					<p class="bd-total">${__("Total")}: ${this._money(total)}</p>`
				: ""
		);
		if (!this.amount_touched) {
			this.payment_amount = total;
			this.$body.find("[data-bd-payment-amount]").val(total);
		}
		this._save_state();
	}

	_sale_has_discount() {
		return this.sale_rows.some((row) => Number(row.discount_percentage) > 0);
	}

	_toggle_sale_code() {
		if (this.entry_mode !== "sale") return;
		const $wrap = this.$body.find("[data-bd-sale-code-wrap]");
		if ($wrap.length) $wrap.toggle(this._sale_has_discount());
	}

	_bind_sale() {
		this._attach_search("[data-bd-customer]", "[data-bd-customer-menu]", (term, done) => {
			frappe.call({
				method: "pos_next.api.customers.get_customers",
				args: { search_term: term || "", pos_profile: this.shift.pos_profile, limit: 20 },
				callback: (r) =>
					done(
						(r.message || []).map((c) => ({
							value: c.name,
							label: c.customer_name || c.name,
						}))
					),
			});
		}, (pick) => {
			this.customer = pick;
			this._save_state();
		});
		this._attach_search("[data-bd-item]", "[data-bd-item-menu]", (term, done) => {
			frappe.call({
				method: "pos_next.api.items.get_items",
				args: {
					pos_profile: this.shift.pos_profile,
					search_term: term || null,
					start: 0,
					limit: 20,
				},
				callback: (r) => {
					this.item_results = r.message || [];
					done(
						this.item_results.map((i) => ({
							value: i.item_code,
							label: i.item_name,
							subtitle: this._money(i.price_list_rate),
						}))
					);
				},
			});
		}, (pick) => this._on_item_pick(pick));

		const modes = this.payment_modes
			.map(
				(m) =>
					`<option value="${frappe.utils.escape_html(m.mode_of_payment)}"${
						m.mode_of_payment === this.payment_mode ? " selected" : ""
					}>${frappe.utils.escape_html(m.mode_of_payment)}</option>`
			)
			.join("");
		this.$body.find("[data-bd-payment-mode]").html(modes);

		this.$panel
			.off("input", "[data-bd-qty]")
			.on("input", "[data-bd-qty]", (e) => {
				this.sale_rows[Number(e.currentTarget.dataset.bdQty)].qty = e.currentTarget.value;
				this._render_sale_rows();
			})
			.off("input", "[data-bd-disc]")
			.on("input", "[data-bd-disc]", (e) => {
				this.sale_rows[Number(e.currentTarget.dataset.bdDisc)].discount_percentage =
					e.currentTarget.value;
				this._render_sale_rows();
				this._toggle_sale_code();
			})
			.off("click", "[data-bd-remove]")
			.on("click", "[data-bd-remove]", (e) => {
				this.sale_rows.splice(Number(e.currentTarget.dataset.bdRemove), 1);
				this._render_sale_rows();
				this._toggle_sale_code();
			});
		this.$body
			.off("input", "[data-bd-payment-amount]")
			.on("input", "[data-bd-payment-amount]", (e) => {
				this.amount_touched = true;
				this.payment_amount = Number(e.currentTarget.value) || 0;
				this._save_state();
			})
			.off("click", "[data-bd-submit]")
			.on("click", "[data-bd-submit]", () => this._submit());
		this._bind_confirmation_code();
	}

	_on_item_pick(code) {
		const item = this.item_results.find((i) => i.item_code === code);
		if (!item) return;
		this.sale_rows.push({
			item_code: item.item_code,
			item_name: item.item_name || item.item_code,
			uom: item.stock_uom || "",
			qty: 1,
			rate: Number(item.price_list_rate) || 0,
			discount_percentage: 0,
		});
		this._render_sale_rows();
		this._toggle_sale_code();
	}

	// --- RETURN ---

	_render_return() {
		this.$panel.html(`
			<div class="bd-search-wrap">
				<label class="bd-label">${__("Original Invoice (from this shift)")}</label>
				<input class="bd-input" type="search" autocomplete="off"
					placeholder="${__("Search invoice...")}" data-bd-invoice />
				<div class="bd-menu" data-bd-invoice-menu hidden></div>
			</div>
			<p class="bd-hint" data-bd-invoice-info></p>
			<div data-bd-return-rows></div>
			<div class="bd-grid2">
				<div>
					<label class="bd-label" for="bd-refund-mode">${__("Refund Mode")}</label>
					<select class="bd-select" id="bd-refund-mode" data-bd-refund-mode></select>
				</div>
				<div>
					<label class="bd-label" for="bd-refund-amount">${__("Amount")}</label>
					<input class="bd-input" id="bd-refund-amount" type="number" min="0" step="any"
						data-bd-refund-amount value="${this.refund_amount || 0}" />
				</div>
			</div>
			<div class="bd-search-wrap" data-bd-refund-code-wrap ${this.refund_code_required ? "" : "hidden"}>
				<label class="bd-label" for="bd-refund-code">${__("Confirmation Code (HQ)")} *</label>
				<div class="bd-code-row">
					<input class="bd-input" id="bd-refund-code" type="text" autocomplete="off" maxlength="8"
						placeholder="${__("Enter the code from head office")}" data-bd-refund-code
						value="${frappe.utils.escape_html(this.refund_code || "")}"
						${this.refund_code_verified ? "disabled" : ""} />
					<button type="button" class="btn btn-sm btn-default" data-bd-verify-code
						${this.refund_code_verified ? "disabled" : ""}>${this.refund_code_verified ? "✓" : __("Verify")}</button>
				</div>
				<p class="bd-hint" data-bd-refund-code-msg></p>
			</div>
			<div class="bd-actions">
				<button class="btn btn-primary btn-sm" data-bd-submit>${__("Submit Return")}</button>
			</div>`);

		this._render_return_rows();
		this._bind_return();
	}

	_render_return_rows() {
		const rows = this.return_rows
			.map(
				(row, idx) => `<tr>
				<td>${frappe.utils.escape_html(row.item_name)}</td>
				<td class="bd-num">${this._money(row.rate)}</td>
				<td class="bd-num">${row.remaining_qty}</td>
				<td><input type="number" min="0" max="${row.remaining_qty}" step="any" class="bd-input bd-input--qty"
					data-bd-return-qty="${idx}" value="${row.return_qty}"
					aria-label="${frappe.utils.escape_html(row.item_name)} — ${__("Qty")}" /></td>
			</tr>`
			)
			.join("");
		const total = this.return_rows.reduce(
			(sum, row) =>
				sum +
				(Number(row.return_qty) > 0 ? Number(row.return_qty) : 0) *
					(Number(row.rate) || 0),
			0
		);
		this.$body.find("[data-bd-return-rows]").html(
			this.return_rows.length
				? `<table class="bd-table">
					<thead><tr><th>${__("Item")}</th><th class="bd-num">${__("Rate")}</th>
					<th class="bd-num">${__("Remaining")}</th><th class="bd-num">${__("Return Qty")}</th></tr></thead>
					<tbody>${rows}</tbody></table>
					<p class="bd-total">${__("Refund")}: ${this._money(total)}</p>`
				: ""
		);
		if (!this.refund_touched) {
			this.refund_amount = total;
			this.$body.find("[data-bd-refund-amount]").val(total);
		}
		this._save_state();
	}

	_bind_return() {
		this._attach_search("[data-bd-invoice]", "[data-bd-invoice-menu]", (term, done) => {
			frappe.call({
				method: "pos_next.api.invoices.get_invoices",
				args: {
					pos_profile: this.shift.pos_profile,
					search: term || null,
					from_date: this._shift_start() || null,
					to_date: this.today,
					docstatus: 1,
					start: 0,
					limit: 20,
				},
				callback: (r) => {
					// only the outlet's own sales inside the shift window are offered
					this.invoice_results = (r.message || []).filter((inv) => !inv.is_return);
					done(
						this.invoice_results.map((inv) => ({
							value: inv.name,
							label: inv.name,
							subtitle: `${inv.customer_name || ""} · ${this._money(inv.grand_total)}`,
						}))
					);
				},
			});
		}, (pick) => this._on_invoice_pick(pick));

		const modes = this.payment_modes
			.map(
				(m) =>
					`<option value="${frappe.utils.escape_html(m.mode_of_payment)}"${
						m.mode_of_payment === this.refund_mode ? " selected" : ""
					}>${frappe.utils.escape_html(m.mode_of_payment)}</option>`
			)
			.join("");
		this.$body.find("[data-bd-refund-mode]").html(modes);

		this.$panel
			.off("input", "[data-bd-return-qty]")
			.on("input", "[data-bd-return-qty]", (e) => {
				this.return_rows[Number(e.currentTarget.dataset.bdReturnQty)].return_qty =
					e.currentTarget.value;
				this._render_return_rows();
			});
		this.$body
			.off("input", "[data-bd-refund-amount]")
			.on("input", "[data-bd-refund-amount]", (e) => {
				this.refund_touched = true;
				this.refund_amount = Number(e.currentTarget.value) || 0;
				this._save_state();
			})
			.off("click", "[data-bd-submit]")
			.on("click", "[data-bd-submit]", () => this._submit());
		this._bind_confirmation_code();
	}

	// Shared by the sale and return panels — only one is rendered at a time,
	// so the same data-bd attributes serve both code inputs.
	_bind_confirmation_code() {
		this.$body
			.off("input", "[data-bd-refund-code]")
			.on("input", "[data-bd-refund-code]", () => {
				this.refund_code_verified = false;
				this.refund_code_error = "";
				this._render_refund_code_state();
			})
			.off("click", "[data-bd-verify-code]")
			.on("click", "[data-bd-verify-code]", () => this._verify_refund_code());
	}

	_load_refund_gate() {
		// POS Settings require_refund_code for this outlet (UI hint only —
		// the doc_events gate re-checks on save/submit and fails closed).
		frappe.call({
			method: "pos_next.api.discount_code.get_status",
			args: { pos_profile: this.shift.pos_profile },
			callback: (r) => {
				this.refund_code_required = (r.message || {}).refund_code_required !== false;
				const $wrap = this.$body.find("[data-bd-refund-code-wrap]");
				if ($wrap.length) $wrap.toggle(this.refund_code_required);
			},
		});
	}

	_verify_refund_code() {
		const value = (this.$body.find("[data-bd-refund-code]").val() || "").trim().toUpperCase();
		this.refund_code = value;
		if (!value) {
			this.refund_code_verified = false;
			this.refund_code_error = __("Enter the confirmation code from head office");
			this._render_refund_code_state();
			return;
		}
		frappe.call({
			method: "pos_next.api.discount_code.check_code",
			args: {
				code: value,
				company: (this.prepared_doc || {}).company || this.shift.company,
			},
			callback: (r) => {
				const res = r.message || {};
				this.refund_code_verified = Boolean(res.valid);
				this.refund_code_error = res.valid
					? ""
					: res.message || __("Confirmation code is not valid");
				this._render_refund_code_state();
			},
		});
	}

	_render_refund_code_state() {
		const $input = this.$body.find("[data-bd-refund-code]");
		if (!$input.length) return;
		$input.val(this.refund_code || "");
		$input.prop("disabled", this.refund_code_verified);
		const $btn = this.$body.find("[data-bd-verify-code]");
		$btn.prop("disabled", this.refund_code_verified);
		$btn.text(this.refund_code_verified ? "✓" : __("Verify"));
		this.$body.find("[data-bd-refund-code-msg]").text(this.refund_code_error || "");
	}
	_on_invoice_pick(name) {
		frappe.call({
			method: "pos_next.api.invoices.prepare_return_invoice",
			args: { invoice_name: name, pos_opening_shift: this.shift.name },
			callback: (r) => {
				const doc = r.message;
				const rows = (doc && doc.items ? doc.items : []).filter(
					(item) => item.remaining_qty > 0
				);
				if (!rows.length) {
					frappe.msgprint(__("All items from this invoice have already been returned"));
					return;
				}
				this.original_invoice =
					this.invoice_results.find((inv) => inv.name === name) || { name };
				this.prepared_doc = doc;
				// start at zero: rows are only returned when HO fills a quantity
				this.return_rows = rows.map((item) =>
					Object.assign({}, item, { return_qty: 0 })
				);
				this.$body.find("[data-bd-invoice-info]").text(
					`${doc.name || name} · ${doc.customer || ""}`
				);
				this._render_return_rows();
			},
		});
	}

	// ------------------------------------------------------------------
	// Profile support data (payment modes + default customer of the OUTLET
	// profile, not the session's) — same endpoints as the SPA dialog.
	// ------------------------------------------------------------------

	_load_support() {
		if (this.support_loaded) return;
		frappe.call({
			method: "pos_next.api.pos_profile.get_payment_methods",
			args: { pos_profile: this.shift.pos_profile },
			callback: (r) => {
				this.payment_modes = r.message || [];
				this.payment_mode =
					((this.payment_modes.find((m) => m.default) || {}).mode_of_payment) ||
					(this.payment_modes[0] || {}).mode_of_payment ||
					"";
				this.refund_mode = this.payment_mode;
				// re-seed whatever selects are on screen now
				if (this.entry_mode === "sale") this._bind_sale();
				else this._bind_return();
				this._save_state();
			},
		});
		frappe.call({
			method: "pos_next.api.pos_profile.get_default_customer",
			args: { pos_profile: this.shift.pos_profile },
			callback: (r) => {
				const cust = r.message;
				if (cust && cust.customer && !this.customer) {
					this.customer = cust.customer;
					const $input = this.$body.find("[data-bd-customer]");
					if ($input.length) $input.val(cust.customer_name || cust.customer);
				}
			},
		});
	}

	// ------------------------------------------------------------------
	// Submit — payload shapes copied 1:1 from BackdateEntryDialog.vue.
	// The server overrides pos_profile / posting_date / set_posting_time
	// from the shift; they are still sent exactly like the dialog does.
	// ------------------------------------------------------------------

	_submit() {
		this.posting_date = this.$body.find("[data-bd-posting-date]").val();
		if (!this.posting_date) {
			frappe.msgprint(__("A posting date is required for backdate entry"));
			return;
		}
		let invoice;
		if (this.entry_mode === "sale") {
			const rows = this.sale_rows.filter((row) => Number(row.qty) > 0);
			if (!this.customer) {
				frappe.msgprint(__("Customer is required"));
				return;
			}
			if (!rows.length) {
				frappe.msgprint(__("At least one item is required"));
				return;
			}
			this.payment_mode = this.$body.find("[data-bd-payment-mode]").val();
			if (!this.payment_mode) {
				frappe.msgprint(__("Select a payment mode"));
				return;
			}
			this.payment_amount = Number(this.$body.find("[data-bd-payment-amount]").val()) || 0;
			if (this._sale_has_discount() && !this.refund_code_verified) {
				frappe.msgprint(__("Verify the confirmation code before submitting the sale"));
				return;
			}
			invoice = {
				doctype: "Sales Invoice",
				pos_profile: this.shift.pos_profile,
				posa_pos_opening_shift: this.shift.name,
				company: this.shift.company,
				customer: this.customer,
				is_pos: 1,
				update_stock: 1,
				discount_confirmation_code: (this.refund_code || "").trim().toUpperCase(),
				posting_date: this.posting_date,
				set_posting_time: 1,
				items: rows.map((row) => ({
					item_code: row.item_code,
					qty: Number(row.qty),
					rate: Number(row.rate),
					discount_percentage: Number(row.discount_percentage) || 0,
					uom: row.uom,
					conversion_factor: 1,
				})),
				payments: [
					{ mode_of_payment: this.payment_mode, amount: this.payment_amount },
				],
			};
		} else {
			const rows = this.return_rows.filter((row) => Number(row.return_qty) > 0);
			if (!this.prepared_doc) {
				frappe.msgprint(__("Select an original invoice"));
				return;
			}
			if (!rows.length) {
				frappe.msgprint(__("At least one item is required"));
				return;
			}
			this.refund_mode = this.$body.find("[data-bd-refund-mode]").val();
			if (!this.refund_mode) {
				frappe.msgprint(__("Select a payment mode"));
				return;
			}
			this.refund_amount = Number(this.$body.find("[data-bd-refund-amount]").val()) || 0;
			if (this.refund_code_required && !this.refund_code_verified) {
				frappe.msgprint(__("Verify the confirmation code before submitting the return"));
				return;
			}
			const return_against =
				this.prepared_doc.return_against || (this.original_invoice || {}).name;
			invoice = {
				doctype: "Sales Invoice",
				pos_profile: this.shift.pos_profile,
				posa_pos_opening_shift: this.shift.name,
				company: this.prepared_doc.company || this.shift.company,
				customer: this.prepared_doc.customer,
				is_return: 1,
				return_against: return_against,
				discount_confirmation_code: (this.refund_code || "").trim().toUpperCase(),
				update_outstanding_for_self: 0,
				is_pos: 1,
				update_stock: 1,
				posting_date: this.posting_date,
				set_posting_time: 1,
				// sales_team rides along so commissions reverse on the returned
				// items (same as the cashier return flow)
				sales_team: (this.prepared_doc.sales_team || []).map((member) => ({
					sales_person: member.sales_person,
					allocated_percentage: member.allocated_percentage || 0,
				})),
				items: rows.map((row) => ({
					item_code: row.item_code,
					qty: -Math.abs(Number(row.return_qty)),
					rate: Number(row.rate),
					warehouse: row.warehouse,
					uom: row.uom,
					conversion_factor: row.conversion_factor || 1,
					// row link of the original line (doctype-specific field name)
					sales_invoice_item: row.sales_invoice_item || row.pos_invoice_item,
				})),
				payments: [
					{
						mode_of_payment: this.refund_mode,
						amount: -Math.abs(this.refund_amount),
					},
				],
				remarks: __("Backdate return against {0}", [return_against]),
			};
		}

		frappe.call({
			method: `${API}.submit_backdate_invoice`,
			args: { invoice: JSON.stringify(invoice) },
			freeze: true,
			callback: (r) => {
				this.submitted_name = (r.message || {}).name || "";
				frappe.show_alert({
					message: __("Invoice {0} submitted", [this.submitted_name]),
					indicator: "green",
				});
				this._render_done();
			},
		});
	}

	// ------------------------------------------------------------------
	// Done: enter another entry, or re-close the shift right here. The SPA
	// routed "Close Shift Now" to the cashier ShiftClosingDialog (built on
	// shifts.get_closing_shift_data + shifts.submit_closing_shift) — here
	// the same two APIs are used with a counted-amount prompt.
	// ------------------------------------------------------------------

	_render_done() {
		this.view = "done";
		this.$body.html(`
			<div class="bd-card bd-center">
				<div class="bd-done">${__("Invoice {0} submitted", [this.submitted_name])}</div>
				<p class="bd-muted">${__(
					"The backdate entry is recorded on shift {0}. Close the shift again so the closing report includes it — use the button below or the outlet's regular close-shift flow.",
					[this.shift.name]
				)}</p>
				<div class="bd-actions">
					<button class="btn btn-default btn-sm" data-bd-again>${__("Enter Another Entry")}</button>
					<button class="btn btn-primary btn-sm" data-bd-close-now>${__("Close Shift Now")}</button>
				</div>
			</div>`);
		this.$body
			.off("click", "[data-bd-again]")
			.on("click", "[data-bd-again]", () => this._open_entry(this.shift))
			.off("click", "[data-bd-close-now]")
			.on("click", "[data-bd-close-now]", () => this._close_shift_now());
	}

	_close_shift_now() {
		frappe.call({
			method: "pos_next.api.shifts.get_closing_shift_data",
			args: { opening_shift: this.shift.name },
			callback: (r) => {
				const data = r.message;
				const recon = (data && data.payment_reconciliation) || [];
				if (!recon.length) return;
				const fields = recon.map((p) => ({
					fieldname: "amount_" + p.mode_of_payment.replace(/\W+/g, "_"),
					label: `${p.mode_of_payment} (${__("Expected")}: ${this._money(p.expected_amount)})`,
					fieldtype: "Currency",
					// prefill with the expected amount — zero difference unless HO
					// knows the drawer counted differently
					default: p.closing_amount != null ? p.closing_amount : p.expected_amount,
				}));
				frappe.prompt(
					fields,
					(values) => {
						recon.forEach((p) => {
							const key = "amount_" + p.mode_of_payment.replace(/\W+/g, "_");
							p.closing_amount = Number(values[key]) || 0;
							p.difference = p.closing_amount - (Number(p.expected_amount) || 0);
						});
						frappe.call({
							method: "pos_next.api.shifts.submit_closing_shift",
							args: { closing_shift: JSON.stringify(data) },
							freeze: true,
							callback: () => {
								delete this.reopened[this.shift.name];
								this.shift = null;
								this._save_state();
								frappe.show_alert({
									message: __("Shift closed successfully"),
									indicator: "green",
								});
								this._load_context();
							},
						});
					},
					__("Close Shift Now"),
					__("Submit")
				);
			},
		});
	}

	// ------------------------------------------------------------------
	// Minimal debounced search dropdown (customers / items / invoices)
	// ------------------------------------------------------------------

	_attach_search(inputSel, menuSel, fetch, on_pick) {
		const $input = this.$body.find(inputSel);
		const $menu = this.$body.find(menuSel);
		let timer = null;
		const close = () => $menu.prop("hidden", true);
		const pick = (item) => {
			close();
			$input.val(item.label);
			(on_pick || ((value) => (this.customer = value)))(item.value);
		};
		$input
			.off("input")
			.on("input", () => {
				clearTimeout(timer);
				const term = $input.val();
				timer = setTimeout(() => {
					fetch(term, (items) => {
						if (!items.length) return close();
						$menu
							.html(
								items
									.map(
										(item) =>
											`<button type="button" class="bd-menu-item" data-value="${frappe.utils.escape_html(
												item.value
											)}"><span>${frappe.utils.escape_html(item.label)}</span>
											${item.subtitle ? `<span class="bd-muted">${frappe.utils.escape_html(item.subtitle)}</span>` : ""}</button>`
									)
									.join("")
							)
							.prop("hidden", false);
					});
				}, 300);
			})
			.off("keydown")
			.on("keydown", (e) => {
				if (e.key === "Escape") close();
			});
		$menu
			.off("click", ".bd-menu-item")
			.on("click", ".bd-menu-item", (e) => {
				pick({ value: e.currentTarget.dataset.value, label: $(e.currentTarget).find("span").first().text() });
			});
		$(document).off("click.bd-search").on("click.bd-search", (e) => {
			if (!$(e.target).closest($input.parent()).length) close();
		});
	}
}
