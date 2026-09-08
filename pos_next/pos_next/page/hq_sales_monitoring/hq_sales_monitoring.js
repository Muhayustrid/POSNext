// Copyright (c) 2026, BrainWise and contributors
// For license information, please see license.txt

{% include "pos_next/public/js/hq_monitoring_utils.js" %}

// The include registers the UMD module on the global scope.
const HQ_UTILS = hqMonitorUtils;

frappe.pages["hq-sales-monitoring"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("HQ Sales Monitoring"),
		single_column: true,
	});
	wrapper.hq_monitor = new HQSalesMonitor(page);
};

frappe.pages["hq-sales-monitoring"].on_page_show = function (wrapper) {
	if (wrapper.hq_monitor) {
		wrapper.hq_monitor.refresh();
	}
};

class HQSalesMonitor {
	constructor(page) {
		this.page = page;
		this.product_page = 1;
		this.state = null;
		this._setup_actions();
		this._setup_filters();
		this.$root = $('<div class="hq-monitor">').appendTo(page.main);
		this.refresh();
	}

	_setup_actions() {
		this.page.set_primary_action(__("Refresh"), () => this.refresh());
		this.page.add_menu_item(__("Print"), () => window.print());
		this.page.add_menu_item(__("Export CSV"), () => this._export_csv());
	}

	_setup_filters() {
		this.company_field = this.page.add_field({
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Select",
			options: [{ label: __("All Companies"), value: "" }],
			change: () => {
				this.product_page = 1;
				this.refresh();
			},
		});
		this.descendants_field = this.page.add_field({
			fieldname: "include_descendants",
			label: __("Include Subsidiaries"),
			fieldtype: "Check",
			change: () => {
				this.product_page = 1;
				this.refresh();
			},
		});
		this.from_field = this.page.add_field({
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
			change: frappe.utils.debounce(() => this.refresh(), 500),
		});
		this.to_field = this.page.add_field({
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			change: () => {
				this.product_page = 1;
				this.refresh();
			},
		});
		this.category_field = this.page.add_field({
			fieldname: "category",
			label: __("Item Category"),
			fieldtype: "Select",
			options: [{ label: __("All Categories"), value: "" }],
			change: () => {
				this.product_page = 1;
				this.refresh();
			},
		});
	}

	_args() {
		return {
			company: this.company_field.get_value() || "",
			include_descendants: this.descendants_field.get_value() ? 1 : 0,
			from_date: this.from_field.get_value() || "",
			to_date: this.to_field.get_value() || "",
			category: this.category_field.get_value() || "",
			product_page: this.product_page,
			page_size: 10,
		};
	}

	refresh() {
		return new Promise((resolve) => {
			frappe.call({
				method: "pos_next.api.hq_monitoring.get_sales_monitoring",
				args: this._args(),
				freeze: true,
				callback: (r) => {
					if (!r.message) return resolve();
					this.state = r.message;
					this._sync_filter_options();
					this._render();
					resolve();
				},
			});
		});
	}

	_sync_filter_options() {
		const scope = this.state.scope || {};
		this.company_field.df.options = [{ label: __("All Companies"), value: "" }].concat(
			(scope.company_options || []).map((c) => ({ label: c, value: c }))
		);
		this.company_field.set_options();
		this.category_field.df.options = [{ label: __("All Categories"), value: "" }].concat(
			((this.state.product_ranking || {}).categories || []).map((c) => ({ label: c, value: c }))
		);
		this.category_field.set_options();
		this.descendants_field.$wrapper.toggle(!!this.company_field.get_value());
	}

	// ------------------------------------------------------------------
	// Rendering
	// ------------------------------------------------------------------

	_render() {
		const s = this.state;
		if (s.notice) {
			this.$root.html(this._card(__("Scope"), `<p class="hq-muted">${frappe.utils.escape_html(s.notice)}</p>`));
			return;
		}
		const parts = [
			this._scope_line(s.scope),
			'<div class="hq-kpis">',
			this._monthly_cards(s),
			"</div>",
			'<div class="hq-grid">',
			this._daily_card(s.daily),
			this._turnover_card(s.turnover),
			this._highlights_card(s),
			"</div>",
			'<div class="hq-grid">',
			this._hours_card(s.hours),
			this._target_card(s.targets),
			"</div>",
			this._table_card(
				__("Product Ranking (MTD)"),
				this._product_table(s.product_ranking),
				this._product_pager(s.product_ranking)
			),
			this._table_card(__("Outlet Ranking (MTD)"), this._outlet_table(s.outlet_ranking)),
			this._notice_card(s.channels, s.pax),
			this._footer(s),
		];
		this.$root.html(parts.join(""));
		this._bind_pager();
		this._render_hour_chart(s.hours);
	}

	_scope_line(scope) {
		const companies = (scope.companies || []).length;
		const label = companies > 3 ? `${companies} ${__("companies")}` : (scope.companies || []).join(", ");
		return `<div class="hq-scope">${__("Scope")}: <b>${frappe.utils.escape_html(label)}</b>
			· ${__("Base currency")}: <b>${scope.default_currency || "-"}</b>
			${(scope.companies || []).length > 1 ? `· <span class="hq-muted">${__("each company in its own base currency; no cross-currency sum")}</span>` : ""}
		</div>`;
	}

	_metric_text(metric) {
		if (!metric) return "-";
		const extra = Object.entries(metric.by_currency || {})
			.filter(([ccy]) => ccy !== metric.default_currency)
			.map(([ccy, v]) => HQ_UTILS.fmtMoney(v, ccy));
		const main = HQ_UTILS.fmtMoney(metric.default, metric.default_currency);
		return extra.length
			? `${main} <span class="hq-extra">+ ${extra.join(" + ")}</span>`
			: main;
	}

	_kpi(label, value_html, sub) {
		return `<div class="hq-card hq-kpi">
			<div class="hq-kpi-label">${label}</div>
			<div class="hq-kpi-value">${value_html}</div>
			${sub ? `<div class="hq-kpi-sub">${sub}</div>` : ""}
		</div>`;
	}

	_pct_html(pct) {
		return HQ_UTILS.fmtPct(pct);
	}

	_monthly_cards(s) {
		const m = s.monthly || {};
		const t = s.targets || {};
		const cards = [
			this._kpi(__("Sales (MTD, net incl. tax)"), this._metric_text(m.net_tax_incl)),
			this._kpi(__("Transactions (TC)"), String(m.orders ?? 0)),
			this._kpi(__("APC (Avg / Check)"), this._metric_text(m.apc)),
		];
		if (t.available) {
			cards.push(
				this._kpi(
					__("Target Achievement"),
					this._multi_ccy_pct(t.achievement_sales_pct, s.scope.default_currency),
					__("Sales target") + ": " + this._metric_text(t.target_sales)
				),
				this._kpi(
					__("Surplus / Deficit"),
					this._multi_ccy_amount(t.surplus_sales, s.scope.default_currency, true),
					__("TC achievement") + ": " + this._pct_html(t.achievement_transactions_pct)
				),
				this._kpi(
					__("Monthly Projection"),
					this._projection_text(t.projection, s.scope.default_currency),
					__("Projected achievement") + ": " + this._multi_ccy_pct(
						Object.fromEntries(Object.entries(t.projection || {}).map(([c, p]) => [c, p.projected_achievement_pct])),
						s.scope.default_currency
					)
				)
			);
		} else {
			cards.push(
				this._kpi(
					__("Target Achievement"),
					`<span class="hq-muted">${__("N/A")}</span>`,
					frappe.utils.escape_html(t.notice || __("Monthly target not set"))
				)
			);
		}
		return cards.join("");
	}

	_multi_ccy_pct(map, default_ccy, signed) {
		const map_obj = map || {};
		const val = map_obj[default_ccy];
		let text = this._signed_pct(val, signed);
		const extras = Object.entries(map_obj)
			.filter(([c]) => c !== default_ccy)
			.map(([c, v]) => `${c}: ${this._signed_pct(v, signed)}`);
		return extras.length ? `${text} <span class="hq-extra">${extras.join(" · ")}</span>` : text;
	}

	_multi_ccy_amount(map, default_ccy, signed) {
		const map_obj = map || {};
		const fmt = (ccy, v) => {
			if (v === null || v === undefined) return "N/A";
			const n = Number(v);
			const prefix = signed && n >= 0 ? "+" : "";
			return prefix + HQ_UTILS.fmtMoney(n, ccy);
		};
		let text = fmt(default_ccy, map_obj[default_ccy]);
		const extras = Object.entries(map_obj)
			.filter(([c]) => c !== default_ccy)
			.map(([c, v]) => `${c}: ${fmt(c, v)}`);
		return extras.length ? `${text} <span class="hq-extra">${extras.join(" · ")}</span>` : text;
	}

	_signed_pct(value, signed) {
		if (value === null || value === undefined) return "N/A";
		const n = Number(value);
		const prefix = signed && n >= 0 ? "+" : "";
		return prefix + n.toFixed(1) + "%";
	}

	_projection_text(projection, default_ccy) {
		const p = (projection || {})[default_ccy];
		if (!p) return "N/A";
		return HQ_UTILS.fmtMoney(p.projected_sales, default_ccy);
	}

	_daily_card(d) {
		if (!d || !d.totals) return "";
		const growth = (map) =>
			Object.entries(map || {})
				.map(([ccy, v]) => `${ccy}: ${this._signed_pct(v)}`)
				.join(" · ") || "N/A";
		return this._card(
			__("Daily Monitoring") + ` — ${d.date}${d.is_today ? ` (${__("today")}, ${__("cut at")} ${d.cutoff})` : ""}`,
			`<table class="hq-table">
				<thead><tr><th></th><th>${__("Net Sales")}</th><th>${__("TC")}</th><th>${__("APC")}</th></tr></thead>
				<tbody>
				<tr><td><b>${__("Selected Day")}</b></td>
					<td>${this._metric_text(d.totals.net_tax_incl)}</td>
					<td>${d.totals.orders ?? 0}</td><td>${this._metric_text(d.totals.apc)}</td></tr>
				<tr><td>${__("Prior Weekday")} (${d.prior_weekday.date})</td>
					<td>${this._metric_text(d.prior_weekday.net_tax_incl)}</td>
					<td>${d.prior_weekday.orders ?? 0}</td><td>${this._metric_text(d.prior_weekday.apc)}</td></tr>
				<tr><td>${__("Same Day Last Week")} (${d.last_week_same.date})</td>
					<td>${this._metric_text(d.last_week_same.net_tax_incl)}</td>
					<td>${d.last_week_same.orders ?? 0}</td><td>${this._metric_text(d.last_week_same.apc)}</td></tr>
				</tbody>
			</table>
			<div class="hq-kpi-sub">${__("Growth vs prior weekday")}: ${growth(d.growth_vs_prior_weekday_pct)}
				· ${__("Growth vs last week")}: ${growth(d.growth_vs_last_week_pct)}</div>
			<div class="hq-kpi-sub hq-muted">${frappe.utils.escape_html(d.daily_target_note || "")}</div>`
		);
	}

	_turnover_card(t) {
		if (!t || !t.this_month_net) return "";
		const change = Object.entries(t.change_pct_vs_prev_comparable || {})
			.map(([ccy, v]) => `${ccy}: ${this._signed_pct(v)}`)
			.join(" · ") || "N/A";
		return this._card(
			__("Turnover This Month"),
			`<table class="hq-table">
				<tbody>
				<tr><td>${__("Net Turnover (MTD)")}</td><td><b>${this._metric_text(t.this_month_net)}</b></td></tr>
				<tr><td>${__("Change vs same elapsed days last month")}</td><td>${change}</td></tr>
				<tr><td>${__("Refunds")}</td><td>${this._metric_text(t.refunds)} (${t.refund_orders ?? 0} ${__("inv.")})</td></tr>
				<tr><td>${__("Net pre-tax")}</td><td>${this._metric_text(t.net_pretax)}</td></tr>
				<tr><td>${__("Taxes & Charges")}</td><td>${this._metric_text(t.taxes)}</td></tr>
				<tr><td>${__("Orders / refund invoices")}</td><td>${t.orders ?? 0} / ${t.refund_orders ?? 0}</td></tr>
				</tbody>
			</table>
			<div class="hq-kpi-sub hq-muted">${frappe.utils.escape_html(t.note || "")}</div>`
		);
	}

	_highlights_card(s) {
		const h = s.highlights || {};
		const fav = s.favorite_product;
		const rows = [];
		if (h.biggest_outlet) {
			rows.push(`<tr><td>${__("Biggest Outlet")}</td><td><b>${frappe.utils.escape_html(h.biggest_outlet.pos_profile)}</b>
				(${this._pct_html(h.biggest_outlet.share_pct)} ${__("share")})</td></tr>`);
		}
		if (h.most_transactions_outlet) {
			rows.push(`<tr><td>${__("Most Transactions")}</td><td><b>${frappe.utils.escape_html(h.most_transactions_outlet.pos_profile)}</b>
				(${h.most_transactions_outlet.orders} ${__("orders")})</td></tr>`);
		}
		if (fav) {
			rows.push(`<tr><td>${__("Favorite Product")}</td><td><b>${frappe.utils.escape_html(fav.item_name)}</b>
				(${fav.qty} ${__("qty")}, ${frappe.utils.escape_html(fav.item_group || "")})</td></tr>`);
		}
		return this._card(__("Highlights"), `<table class="hq-table"><tbody>${rows.join("")}</tbody></table>`);
	}

	_hours_card(hours) {
		if (!hours) return "";
		const peak = hours.peak
			? `${__("Peak")} <b>${HQ_UTILS.hourLabel(hours.peak.hour)}</b> (${HQ_UTILS.fmtMoney(hours.peak.net_sales)}, ${hours.peak.orders} ${__("orders")})`
			: __("No data");
		const fmt = (list) =>
			(list || []).map((r) => `${HQ_UTILS.hourLabel(r.hour)} (${HQ_UTILS.fmtMoney(r.net_sales)})`).join(", ") || "—";
		return `<div class="hq-card">
			<div class="hq-card-title">${__("Hours (MTD)")}</div>
			<div class="hq-kpi-sub">${peak}</div>
			<div class="hq-kpi-sub">${__("Top")}: ${fmt(hours.top)}</div>
			<div class="hq-kpi-sub">${__("Lowest")}: ${fmt(hours.lowest)}</div>
			<div class="hq-hour-chart"></div>
		</div>`;
	}

	_render_hour_chart(hours) {
		const el = this.$root.find(".hq-hour-chart").get(0);
		if (!el || !hours || !hours.rows || !hours.rows.length || !frappe.Chart) return;
		new frappe.Chart(el, {
			data: {
				labels: hours.rows.map((r) => HQ_UTILS.hourLabel(r.hour)),
				datasets: [{ name: __("Net Sales"), values: hours.rows.map((r) => r.net_sales) }],
			},
			type: "bar",
			colors: ["#2490ef"],
			height: 160,
			barOptions: { spaceRatio: 0.3 },
			axisOptions: { xIsSeries: false },
		});
	}

	_target_card(t) {
		if (!t) return "";
		if (!t.available) {
			return this._card(
				__("Monthly Target"),
				`<p class="hq-muted">${frappe.utils.escape_html(t.notice || __("Not set"))}</p>`
			);
		}
		const apc_target = Object.entries(t.apc_target || {})
			.map(([c, v]) => `${c}: ${v === null || v === undefined ? "N/A" : HQ_UTILS.fmtMoney(v, c)}`)
			.join(" · ");
		return this._card(
			__("Monthly Target") + ` — ${t.month_start}`,
			`<table class="hq-table"><tbody>
				<tr><td>${__("Sales Target")}</td><td>${this._metric_text(t.target_sales)}</td></tr>
				<tr><td>${__("TC Target")}</td><td>${t.target_transactions}</td></tr>
				<tr><td>${__("APC Target (Sales / TC)")}</td><td>${apc_target || "N/A"}</td></tr>
				<tr><td>${__("Daily Sales Target (pro-rata)")}</td><td>${Object.entries(t.daily_target_sales || {})
					.map(([c, v]) => `${c}: ${v === null ? "N/A" : HQ_UTILS.fmtMoney(v, c)}`)
					.join(" · ")}</td></tr>
			</tbody></table>
			<div class="hq-kpi-sub hq-muted">${frappe.utils.escape_html(t.daily_target_note || "")}
			${frappe.utils.escape_html(t.projection_note || "")}</div>`
		);
	}

	_table_card(title, table_html, pager_html) {
		return `<div class="hq-card hq-card--table">
			<div class="hq-card-title">${title}</div>
			${table_html}
			${pager_html || ""}
		</div>`;
	}

	_product_table(pr) {
		if (!pr) return "";
		const rows = (pr.rows || [])
			.map(
				(r, i) => `<tr>
				<td>${(pr.page - 1) * pr.page_size + i + 1}</td>
				<td>${frappe.utils.escape_html(r.item_name)}</td>
				<td>${frappe.utils.escape_html(r.item_group || "")}</td>
				<td class="hq-num">${r.qty}</td>
				<td class="hq-num">${HQ_UTILS.fmtMoney(r.net_amount, "")}</td>
				<td class="hq-num">${this._pct_html(r.share_pct)}</td>
			</tr>`
			)
			.join("");
		return `<table class="hq-table">
			<thead><tr><th>#</th><th>${__("Item")}</th><th>${__("Category")}</th>
			<th>${__("Qty")}</th><th>${__("Net Sales")}</th><th>${__("Share %")}</th></tr></thead>
			<tbody>${rows || `<tr><td colspan="6" class="hq-muted">${__("No data")}</td></tr>`}</tbody>
		</table>`;
	}

	_product_pager(pr) {
		if (!pr || !pr.total) return "";
		const pages = Math.ceil(pr.total / pr.page_size);
		return `<div class="hq-pager">
			<button class="btn btn-xs btn-default" ${pr.page <= 1 ? "disabled" : ""} data-hq-page="prev">${__("Previous")}</button>
			<span>${pr.page} / ${pages} (${pr.total} ${__("items")})</span>
			<button class="btn btn-xs btn-default" ${pr.page >= pages ? "disabled" : ""} data-hq-page="next">${__("Next")}</button>
		</div>`;
	}

	_outlet_table(rows) {
		const body = (rows || [])
			.map(
				(r) => `<tr>
				<td>${frappe.utils.escape_html(r.pos_profile)}</td>
				<td>${frappe.utils.escape_html(r.company)}</td>
				<td class="hq-num">${HQ_UTILS.fmtMoney(r.net_tax_incl, r.currency)}</td>
				<td class="hq-num">${r.orders}</td>
				<td class="hq-num">${r.apc === null ? "N/A" : HQ_UTILS.fmtMoney(r.apc, r.currency)}</td>
				<td class="hq-num">${this._pct_html(r.share_pct)}</td>
			</tr>`
			)
			.join("");
		return `<table class="hq-table">
			<thead><tr><th>${__("Outlet")}</th><th>${__("Company")}</th><th>${__("Net Sales")}</th>
			<th>${__("Transactions")}</th><th>${__("Avg Ticket")}</th><th>${__("Share %")}</th></tr></thead>
			<tbody>${body || `<tr><td colspan="6" class="hq-muted">${__("No data")}</td></tr>`}</tbody>
		</table>`;
	}

	_notice_card(...sections) {
		const rows = sections
			.filter((s) => s && !s.available)
			.map((s) => `<div class="hq-kpi-sub hq-muted">ℹ ${frappe.utils.escape_html(s.notice || "")}</div>`);
		if (!rows.length) return "";
		return this._card(__("Data Source Notes"), rows.join(""));
	}

	_footer(s) {
		const w = s.windows || {};
		return `<div class="hq-footer hq-muted">
			${__("Month")}: ${w.month_start} · ${__("days elapsed")}: ${w.days_elapsed}/${w.days_in_month}
			· ${__("Generated")}: ${s.generated_at} (${__("server time")})
		</div>`;
	}

	_card(title, body) {
		return `<div class="hq-card"><div class="hq-card-title">${title}</div>${body}</div>`;
	}

	// ------------------------------------------------------------------
	// Events delegated from the DOM
	// ------------------------------------------------------------------

	_bind_pager() {
		this.$root.off("click", "[data-hq-page]").on("click", "[data-hq-page]", (e) => {
			const dir = $(e.currentTarget).data("hq-page");
			this.product_page += dir === "next" ? 1 : -1;
			this.product_page = Math.max(1, this.product_page);
			this.refresh();
		});
	}

	_export_csv() {
		const s = this.state;
		if (!s) return;
		const sections = [];
		const pr = s.product_ranking || {};
		sections.push(
			HQ_UTILS.toCsv(
				[__("Item"), __("Category"), __("Qty"), __("Net Sales"), __("Share %")],
				(pr.rows || []).map((r) => [r.item_name, r.item_group, r.qty, r.net_amount, r.share_pct])
			)
		);
		sections.push("");
		sections.push(
			HQ_UTILS.toCsv(
				[__("Outlet"), __("Company"), __("Net Sales"), __("Transactions"), __("Avg Ticket"), __("Share %")],
				(s.outlet_ranking || []).map((r) => [
					r.pos_profile,
					r.company,
					HQ_UTILS.fmtMoney(r.net_tax_incl, r.currency),
					r.orders,
					r.apc,
					r.share_pct,
				])
			)
		);
		const blob = new Blob(["\ufeff" + sections.join("\n")], { type: "text/csv;charset=utf-8;" });
		const a = document.createElement("a");
		a.href = URL.createObjectURL(blob);
		a.download = `hq-sales-monitoring-${s.scope.to_date}.csv`;
		a.click();
		URL.revokeObjectURL(a.href);
	}
}
