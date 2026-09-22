// Copyright (c) 2026, BrainWise and contributors
// For license information, please see license.txt

{% include "pos_next/public/js/hq_monitoring_utils.js" %}

// The include registers the UMD module on the global scope.
const HQ_UTILS = hqMonitorUtils;

// Route every number on this page through Frappe's authoritative formatters:
// the site's System Settings number format (per-currency format when
// use_number_format_from_currency is set), configured currency_precision for
// money (honours "0"), and float precision / integer trim via the standard
// Float formatter for counts and qty. Without this the utils fall back to
// en-US (node-test fallback only).
if (typeof format_number === "function" && typeof get_number_format === "function") {
	HQ_UTILS.setNumberAdapter({
		// format_currency() minus the symbol: the page shows the ISO code and
		// keeps currencies isolated. Same default-precision logic as
		// format_currency(): configured currency_precision, else the precision
		// carried by the number format itself.
		money: (value, currency) =>
			format_number(value, get_number_format(currency), frappe.boot.sysdefaults.currency_precision || null),
		percent: (value, digits) => format_number(value, null, digits),
		// frappe.form.formatters.Float with no docfield: configured
		// float_precision + "1.000000 shows as 1" trim; only_value skips the
		// right-align wrapper div.
		count: (value) => frappe.form.formatters.Float(value, {}, { only_value: true }),
	});
}

frappe.pages["outlet-targets"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Outlet Targets"),
		single_column: true,
	});
	wrapper.outlet_targets = new OutletTargetsPage(page);
};

frappe.pages["outlet-targets"].on_page_show = function (wrapper) {
	// state set = the first load finished; skips the redundant load fired
	// while navigating back to an already-initialised page
	if (wrapper.outlet_targets && wrapper.outlet_targets.state) {
		wrapper.outlet_targets.refresh();
	}
};

// One table, one job: monthly targets + the overall payback ("balik modal")
// target per outlet. Replaces the Set Target dialog on the HQ page.
class OutletTargetsPage {
	constructor(page) {
		this.page = page;
		this.state = null;
		this.search = "";
		this._setup_actions();
		this._setup_filters();
		this.$root = $('<div class="ot-page">').appendTo(page.main);
		this.$root.html(`<p class="ot-muted">${__("Loading...")}</p>`);
		this.refresh();
	}

	_setup_actions() {
		this.page.set_primary_action(__("Refresh"), () => this.refresh());
		this.page.add_menu_item(__("Print"), () => window.print());
		this.page.add_menu_item(__("Export CSV"), () => this._export_csv());
	}

	// Frappe page-field change handlers run before the control commits its
	// new value, so every handler defers one tick: by then get_value() holds
	// what the user actually picked.
	_later(fn) {
		return () => setTimeout(fn, 0);
	}

	_setup_filters() {
		const start = frappe.datetime.month_start().split("-");
		const year = Number(start[0]);
		const monthOpts = [
			"January", "February", "March", "April", "May", "June",
			"July", "August", "September", "October", "November", "December",
		].map((name, i) => ({ label: __(name), value: String(i + 1).padStart(2, "0") }));
		const yearOpts = [];
		for (let y = year - 1; y <= year + 1; y++) yearOpts.push({ label: String(y), value: String(y) });
		this.month_field = this.page.add_field({
			fieldname: "month",
			label: __("Month"),
			fieldtype: "Select",
			options: monthOpts,
			default: start[1],
			change: this._later(() => this.refresh()),
		});
		this.year_field = this.page.add_field({
			fieldname: "year",
			label: __("Year"),
			fieldtype: "Select",
			options: yearOpts,
			default: String(year),
			change: this._later(() => this.refresh()),
		});
	}

	// The API receives exactly this key: year + "-" + month + "-01".
	_month_start() {
		return `${this.year_field.get_value()}-${this.month_field.get_value()}-01`;
	}

	// Target basis (enum + server-translated labels arrive in the payload's
	// target_basis; the fallbacks keep the page usable on an older payload).
	_basis(which) {
		return ((this.state && this.state.target_basis) || {})[which] || "Net Sales";
	}

	_basis_label(which) {
		const tb = (this.state && this.state.target_basis) || {};
		return tb[`${which}_label`] || __("Net Sales");
	}

	refresh() {
		if (!this.$root) return Promise.resolve();
		return new Promise((resolve) => {
			frappe.call({
				method: "pos_next.api.hq_monitoring.get_outlet_targets",
				args: { month_start: this._month_start() },
				freeze: true,
				callback: (r) => {
					if (r.message) {
						this.state = r.message;
						this._render();
					}
					resolve();
				},
				error: (r) => {
					this._render_error(r);
					resolve();
				},
			});
		});
	}

	// The traceback's last line carries the exception message; the generic
	// sentence covers transport errors where no server message arrived.
	_render_error(r) {
		let msg = __("Failed to load targets");
		const exc = r && r.exc ? String(r.exc).trim().split("\n").filter(Boolean) : [];
		if (exc.length) msg = exc[exc.length - 1];
		this.$root.html(
			`<div class="ot-card"><p class="ot-error">${frappe.utils.escape_html(msg)}</p></div>`
		);
	}

	// ------------------------------------------------------------------
	// Rendering — one card, one row per outlet (= company)
	// ------------------------------------------------------------------

	_render() {
		const rows = this.state.rows || [];
		if (!rows.length) {
			this.$root.html(`<div class="ot-card"><p class="ot-muted">${__("No data")}</p></div>`);
			return;
		}
		// Target columns are headed by the configured basis (e.g.
		// "Target Laba Kotor (bulanan)"); the sales columns stay sales.
		const mlabel = frappe.utils.escape_html(this._basis_label("monthly"));
		this.$root.html([
			`<div class="ot-toolbar">
				<input type="search" class="ot-input" data-ot-search
					placeholder="${__("Search outlet…")}" value="${frappe.utils.escape_html(this.search)}"
					aria-label="${__("Search outlet")}">
				<button class="btn btn-xs btn-default" data-ot-export>${__("Export CSV")}</button>
			</div>`,
			`<div class="ot-card"><div class="ot-table-scroll"><table class="ot-table">
				<thead><tr>
					<th>${__("Outlet (Company)")}</th>
					<th class="ot-num">${__("Target")} ${mlabel} (${__("monthly")})</th>
					<th class="ot-num">${mlabel} ${__("MTD")}</th>
					<th class="ot-num">${__("Achievement")}</th>
					<th class="ot-num">${__("Projection")}</th>
					<th class="ot-num">${__("Balik Modal")}</th>
					<th></th>
				</tr></thead>
				<tbody>${rows.map((r) => this._row(r)).join("")}</tbody>
			</table></div></div>`,
			`<div class="ot-sub ot-muted">${__("Month")}: ${frappe.utils.escape_html(this.state.month_start)}
				· ${__("days elapsed")}: ${HQ_UTILS.fmtCount(this.state.days_elapsed ?? 0)}
				· ${__("Generated")}: ${frappe.utils.escape_html(this.state.generated_at)}</div>`,
		].join(""));
		this._bind_delegates();
		this._apply_search();
	}

	_row(r) {
		const m = r.monthly || {};
		const o = r.overall;
		const ccy = r.currency || "";
		const missing = !!m.missing;
		const unset = `<span class="ot-small ot-muted">${__("not set yet")}</span>`;
		// Neutral basis keys with the old net-sales keys as fallback; MTD TC
		// stays a sales count on every basis.
		const mtdValue = r.mtd_value ?? r.mtd_net_tax_incl;
		const projected = r.projected_value ?? r.projected_sales;

		const targetCell = missing
			? unset
			: `<span class="ot-money">${HQ_UTILS.fmtMoney(r.target_value ?? m.target_sales, ccy)}</span>
				<div class="ot-sub">${HQ_UTILS.fmtCount(m.target_transactions ?? 0)} ${__("target TC")}</div>`;
		const mtdCell = `<span class="ot-money">${HQ_UTILS.fmtMoney(mtdValue ?? 0, ccy)}</span>
			<div class="ot-sub">${HQ_UTILS.fmtCount(r.mtd_orders ?? 0)} ${__("TC")}</div>`;
		const achCell = missing
			? unset
			: `${this._bar(r.achievement_sales_pct)} <b class="ot-pct">${HQ_UTILS.fmtPct(r.achievement_sales_pct, 1)}</b>`;
		const projCell = missing || projected == null
			? `<span class="ot-muted">-</span>`
			: `<span class="ot-money">${HQ_UTILS.fmtMoney(projected, ccy)}</span>`;
		const overallCell = o && o.overall_target != null
			? `${this._bar(o.achievement_pct, "ot-bar--thin")} <b class="ot-pct">${HQ_UTILS.fmtPct(o.achievement_pct, 1)}</b>
				<div class="ot-sub">${__("cum.")} <span class="ot-money">${HQ_UTILS.fmtMoney(o.cumulative_value ?? o.cumulative_net_tax_incl, ccy)}</span>
				/ <span class="ot-money">${HQ_UTILS.fmtMoney(o.overall_target, ccy)}</span>${o.from_date ? ` · ${frappe.utils.escape_html(o.from_date)} →` : ""}</div>`
			: `<span class="ot-muted">-</span>`;

		return `<tr data-ot-row data-ot-company="${frappe.utils.escape_html(r.company)}">
			<td class="ot-outlet">${frappe.utils.escape_html(r.company)}</td>
			<td class="ot-num">${targetCell}</td>
			<td class="ot-num">${mtdCell}</td>
			<td class="ot-num">${achCell}</td>
			<td class="ot-num">${projCell}</td>
			<td class="ot-num">${overallCell}</td>
			<td class="ot-num"><button class="btn btn-xs btn-default" data-ot-set="${frappe.utils.escape_html(r.company)}">${__("Set")}</button></td>
		</tr>`;
	}

	// Inline progress bar; pct is clamped for the fill, never for the label.
	_bar(pct, cls) {
		const value = pct === null || pct === undefined ? 0 : Math.max(0, Math.min(100, Number(pct)));
		return `<span class="ot-bar${cls ? ` ${cls}` : ""}" role="img"
			aria-label="${HQ_UTILS.fmtPct(pct === null || pct === undefined ? 0 : pct)}"><span class="ot-bar-fill" style="width:${value}%"></span></span>`;
	}

	// ------------------------------------------------------------------
	// Set Target dialog — monthly targets for the selected month plus the
	// overall payback target on the Company master. Blank fields keep stored
	// values; the server enforces permissions per store.
	// ------------------------------------------------------------------

	_set_dialog(company) {
		const row = (this.state.rows || []).find((r) => r.company === company) || {};
		const m = row.monthly || {};
		const o = row.overall || {};
		const month_start = this._month_start();
		// Field labels follow the configured bases; only Net Sales keeps the
		// "net incl. tax" qualifier (the profit bases carry their own meaning).
		const mlabel = this._basis_label("monthly");
		const olabel = this._basis_label("overall");
		const netHint = this._basis("monthly") === "Net Sales" ? ` (${__("net incl. tax")})` : "";
		const d = new frappe.ui.Dialog({
			title: `${__("Set Target")} · ${frappe.utils.escape_html(company)}`,
			fields: [
				{
					fieldname: "monthly_head",
					fieldtype: "HTML",
					options: `<div class="ot-dialog-head">${__("Monthly targets")} (${frappe.utils.escape_html(mlabel)}) · <b>${frappe.utils.escape_html(month_start)}</b></div>`,
				},
				{
					fieldname: "target_sales",
					label: `${__("Target")} ${mlabel}${netHint}`,
					fieldtype: "Currency",
					default: m.target_sales ?? "",
				},
				{
					fieldname: "target_transactions",
					label: __("Target Transactions"),
					fieldtype: "Int",
					default: m.target_transactions ?? "",
				},
				{
					fieldname: "overall_head",
					fieldtype: "HTML",
					options: `<div class="ot-dialog-head">${__("Overall / balik modal")} (${frappe.utils.escape_html(olabel)}) · ${__("one-time target on the outlet")}</div>`,
				},
				{
					fieldname: "overall_target",
					label: `${__("Overall Target")} (${olabel})`,
					fieldtype: "Currency",
					default: o.overall_target ?? "",
					description: `${__("0 clears the overall target")}.`,
				},
				{
					fieldname: "overall_from",
					label: __("Count cumulative from"),
					fieldtype: "Date",
					default: o.from_date || "",
					description: `${__("Empty = all time")}.`,
				},
			],
			primary_action_label: __("Save"),
			primary_action: (values) => {
				frappe.call({
					method: "pos_next.api.hq_monitoring.set_outlet_target",
					args: {
						company,
						month_start,
						target_sales: values.target_sales ?? "",
						target_transactions: values.target_transactions ?? "",
						overall_target: values.overall_target ?? "",
						overall_from: values.overall_from || "",
					},
					freeze: true,
					callback: () => {
						d.hide();
						frappe.show_alert({ message: __("Targets updated"), indicator: "green" });
						this.refresh();
					},
				});
			},
		});
		d.show();
	}

	// ------------------------------------------------------------------
	// Events delegated from the DOM
	// ------------------------------------------------------------------

	_bind_delegates() {
		this.$root
			.off("click", "[data-ot-set]")
			.on("click", "[data-ot-set]", (e) => {
				this._set_dialog($(e.currentTarget).data("ot-set"));
			})
			.off("input", "[data-ot-search]")
			.on("input", "[data-ot-search]", frappe.utils.debounce((e) => {
				this.search = (e.currentTarget.value || "").trim();
				this._apply_search();
			}, 150))
			.off("click", "[data-ot-export]")
			.on("click", "[data-ot-export]", () => this._export_csv());
	}

	_apply_search() {
		const q = (this.search || "").toLowerCase();
		this.$root.find("[data-ot-row]").each((_, el) => {
			const name = String($(el).data("ot-company") || "").toLowerCase();
			$(el).toggle(!q || name.includes(q));
		});
	}

	// CSV stays machine-readable: raw ungrouped numbers, never the localized
	// display strings. Per-row currency gets its own column instead of being
	// baked into the amount.
	_export_csv() {
		const s = this.state;
		if (!s) return;
		// Target columns are headed by the configured basis labels; values
		// read the neutral keys with the old net-sales keys as fallback.
		const mlabel = this._basis_label("monthly");
		const olabel = this._basis_label("overall");
		const csv = HQ_UTILS.toCsv(
			[
				__("Outlet (Company)"),
				__("Currency"),
				`${__("Target")} ${mlabel} (${__("monthly")})`,
				__("Target Transactions"),
				`${mlabel} ${__("MTD")}`,
				__("MTD Transactions"),
				__("Achievement %"),
				`${__("Projected")} ${mlabel}`,
				`${__("Overall Target")} (${olabel})`,
				__("Overall From"),
				`${olabel} ${__("Cumulative")}`,
				__("Overall Achievement %"),
			],
			(s.rows || []).map((r) => {
				const m = r.monthly || {};
				const o = r.overall || {};
				return [
					r.company,
					r.currency,
					m.missing ? "" : (r.target_value ?? m.target_sales),
					m.missing ? "" : m.target_transactions,
					r.mtd_value ?? r.mtd_net_tax_incl,
					r.mtd_orders,
					m.missing ? "" : r.achievement_sales_pct,
					m.missing ? "" : (r.projected_value ?? r.projected_sales),
					o.overall_target ?? "",
					o.from_date || "",
					o.cumulative_value ?? o.cumulative_net_tax_incl ?? "",
					o.achievement_pct ?? "",
				];
			})
		);
		const blob = new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8;" });
		const a = document.createElement("a");
		a.href = URL.createObjectURL(blob);
		a.download = `outlet-targets-${s.month_start}.csv`;
		a.click();
		URL.revokeObjectURL(a.href);
	}
}
