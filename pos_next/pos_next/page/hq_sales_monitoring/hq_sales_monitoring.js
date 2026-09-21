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

// One palette for every chart on the page; legend dots reuse it by index.
const HQ_PALETTE = ["#2490ef", "#2e7d54", "#e8590c", "#7048c9", "#d6336c", "#94a3b8"];

// Outlet table pages client-side: the API payload already carries every row.
const HQ_OUTLET_PAGE_SIZE = 8;

/**
 * frappe-charts 2.0-rc27: a ResizeObserver burst (initial observation + size
 * settle in one frame) runs draw() twice and the second pass crashes on
 * removeChild of the not-yet-re-attached svg (uncaught NotFoundError in the
 * observer callback). Coalesce only the observer/resize-triggered draws per
 * animation frame — the constructor's initial draw stays synchronous, so
 * rendering is untouched. # ponytail: drop when frappe-charts is pinned to a
 * release with this fixed
 */
function _harden_chart(chart) {
	if (!chart || !chart.boundDrawFn) return;
	const raw = chart.boundDrawFn;
	let pending = false;
	const wrapped = () => {
		if (pending) return;
		pending = true;
		requestAnimationFrame(() => {
			pending = false;
			try {
				raw();
			} catch (e) {
				if (!(e instanceof DOMException && e.name === "NotFoundError")) throw e;
			}
		});
	};
	chart.boundDrawFn = wrapped;
	if (chart.resizeObserver) {
		chart.resizeObserver.disconnect();
		chart.resizeObserver = new ResizeObserver(wrapped);
		chart.resizeObserver.observe(chart.parent);
	}
	window.removeEventListener("resize", raw);
	window.removeEventListener("orientationchange", raw);
	window.addEventListener("resize", wrapped);
	window.addEventListener("orientationchange", wrapped);
}

// Namespace for this page's per-user preferences inside Frappe's built-in
// user settings (server-side, scoped site + frappe.session.user). Only
// non-sensitive UI choices are stored: hour numbers and Item Group names.
const HQ_PREFS_KEY = "HQ Sales Monitoring";

frappe.pages["hq-sales-monitoring"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("HQ Sales Monitoring"),
		single_column: true,
	});
	wrapper.hq_monitor = new HQSalesMonitor(page);
};

frappe.pages["hq-sales-monitoring"].on_page_show = function (wrapper) {
	// state set = the first (post-preferences) load finished; skips the
	// redundant default-data load fired while user settings are still loading
	if (wrapper.hq_monitor && wrapper.hq_monitor.state) {
		wrapper.hq_monitor.refresh();
	}
};

class HQSalesMonitor {
	constructor(page) {
		this.page = page;
		this.product_page = 1;
		this.outlet_page = 1;
		this.category = "";
		this.outlet_query = "";
		// Peak-hour window (start inclusive, end exclusive; end <= start wraps
		// past midnight) and the two independent category-card picks. Defaults
		// are replaced by the user's last saved choices before the first load.
		this.hour_start = 0;
		this.hour_end = 24;
		this.category_a = "";
		this.category_b = "";
		this.state = null;
		this._charts = [];
		this._hour_chart = null;
		this._setup_actions();
		this._setup_filters();
		this.$root = $('<div class="hq-monitor">').appendTo(page.main);
		this._restore_prefs().then(() => this.refresh());
	}

	_setup_actions() {
		this.page.set_primary_action(__("Refresh"), () => this.refresh());
		this.page.add_menu_item(__("Print"), () => window.print());
		this.page.add_menu_item(__("Export CSV"), () => this._export_csv());
	}

	// Frappe page-field change handlers run before the control commits its
	// new value, so every handler defers one tick: by then get_value() and
	// $input.val() hold what the user actually picked.
	_later(fn) {
		return () => setTimeout(fn, 0);
	}

	_preset() {
		return (this.preset_field && this.preset_field.get_value()) || "today";
	}

	// Read a Date page-field straight from its input (user date format),
	// bypassing the asynchronously-committed control value.
	_dom_date(field) {
		const raw = field.$input && field.$input.val();
		if (!raw) return "";
		if (frappe.datetime.user_to_str) return frappe.datetime.user_to_str(raw);
		return raw;
	}

	_setup_filters() {
		this.preset_field = this.page.add_field({
			fieldname: "range_preset",
			label: __("Time Range"),
			fieldtype: "Select",
			default: "today",
			options: [
				{ label: __("Today"), value: "today" },
				{ label: __("Yesterday"), value: "yesterday" },
				{ label: __("Last 7 Days"), value: "last7" },
				{ label: __("This Month"), value: "month" },
				{ label: __("Custom"), value: "custom" },
			],
			change: this._later(() => this._apply_preset()),
		});
		this.from_field = this.page.add_field({
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			change: frappe.utils.debounce(this._later(() => {
				if (this._preset() === "custom") this.refresh();
			}), 500),
		});
		this.to_field = this.page.add_field({
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			change: this._later(() => {
				this.product_page = 1;
				if (this._preset() === "custom") this.refresh();
			}),
		});
		// Outlet = Company. A Link control gives the searchable dropdown the
		// Select never had; get_query keeps the choices inside the user's
		// permitted companies and only_select blocks free-typed values.
		this.company_field = this.page.add_field({
			fieldname: "company",
			label: __("Outlet"),
			fieldtype: "Link",
			options: "Company",
			placeholder: __("All Outlets"),
			only_select: 1,
			get_query: () => ({
				filters: {
					name: ["in", (this.state && this.state.scope.company_options) || []],
				},
			}),
			change: this._later(() => {
				this.product_page = 1;
				this.refresh();
			}),
		});
		// page.add_field does not forward df.placeholder — set it directly.
		this.company_field.$input.attr("placeholder", __("All Outlets"));
		this.descendants_field = this.page.add_field({
			fieldname: "include_descendants",
			label: __("Include Subsidiaries"),
			fieldtype: "Check",
			change: this._later(() => {
				this.product_page = 1;
				this.refresh();
			}),
		});
		this._apply_preset(true);
	}

	// Preset computes from/to client-side and the API receives exactly these
	// dates — MTD sections stay month-to-date server-side regardless.
	// this.range is the source of truth sent to the API: the page Date
	// controls commit asynchronously, so get_value() right after set_value()
	// still returns the previous range.
	_apply_preset(initial) {
		const preset = this._preset();
		const custom = preset === "custom";
		this.from_field.$wrapper.toggle(custom);
		this.to_field.$wrapper.toggle(custom);
		if (custom) {
			this.range = {
				from_date: this._dom_date(this.from_field) || "",
				to_date: this._dom_date(this.to_field) || "",
			};
			if (initial !== true) this.refresh();
			return;
		}
		const today = frappe.datetime.get_today();
		let from = today;
		let to = today;
		if (preset === "yesterday") {
			from = to = frappe.datetime.add_days(today, -1);
		} else if (preset === "last7") {
			from = frappe.datetime.add_days(today, -6);
		} else if (preset === "month") {
			from = frappe.datetime.month_start();
		}
		this.range = { from_date: from, to_date: to };
		this.from_field.set_value(from);
		this.to_field.set_value(to);
		if (initial !== true) this.refresh();
	}

	_args() {
		return {
			company: this.company_field.get_value() || "",
			include_descendants: this.descendants_field.get_value() ? 1 : 0,
			from_date: (this.range && this.range.from_date) || "",
			to_date: (this.range && this.range.to_date) || "",
			category: this.category || "",
			category_a: this.category_a || "",
			category_b: this.category_b || "",
			product_page: this.product_page,
			page_size: 10,
		};
	}

	// ------------------------------------------------------------------
	// Per-user preferences (Frappe built-in user settings: per site + user,
	// survives refresh/login; hour numbers + category names only).
	// ------------------------------------------------------------------

	_restore_prefs() {
		const apply = (settings) => {
			const prefs = HQ_UTILS.sanitizePrefs(settings);
			this.hour_start = prefs.hour_start;
			this.hour_end = prefs.hour_end;
			this.category_a = prefs.category_a;
			this.category_b = prefs.category_b;
		};
		return frappe.model.user_settings
			.get(HQ_PREFS_KEY)
			.then(apply)
			.catch(() => {}); // unreadable settings just mean defaults
	}

	_save_prefs() {
		frappe.model.user_settings.save(HQ_PREFS_KEY, "hq_dashboard", {
			hour_start: this.hour_start,
			hour_end: this.hour_end,
			category_a: this.category_a,
			category_b: this.category_b,
		});
	}

	refresh() {
		if (!this.$root) return Promise.resolve();
		this.outlet_page = 1;
		return new Promise((resolve) => {
			frappe.call({
				method: "pos_next.api.hq_monitoring.get_sales_monitoring",
				args: this._args(),
				freeze: true,
				callback: (r) => {
					if (!r.message) return resolve();
					this.state = r.message;
					this._drop_stale_categories();
					this._sync_filter_options();
					this._render();
					resolve();
				},
			});
		});
	}

	// A stored category that no longer exists comes back flagged by the API;
	// clear it locally and persist the fallback so the stale value never
	// reaches the request again.
	_drop_stale_categories() {
		const cp = this.state.category_products || {};
		["a", "b"].forEach((slot) => {
			if (cp[slot] && cp[slot].invalid) {
				this[`category_${slot}`] = "";
				this._save_prefs();
			}
		});
	}

	_sync_filter_options() {
		// The Link outlet filter is scoped through get_query (live, reads
		// this.state); here only the subsidiaries toggle depends on scope.
		this.descendants_field.$wrapper.toggle(!!this.company_field.get_value());
	}

	// ------------------------------------------------------------------
	// Rendering — layout v2: scope strip, KPI row (range + aggregate MTD
	// achievement), the per-outlet target table as the centrepiece, then the
	// monitoring matrices, minis, charts, peak hour and product ranking.
	// ------------------------------------------------------------------

	_render() {
		this._destroy_charts();
		const s = this.state;
		if (s.notice) {
			this.$root.html(
				`<div class="hq-card"><p class="hq-muted">${frappe.utils.escape_html(s.notice)}</p></div>`
			);
			return;
		}
		const parts = [
			this._scope_line(s.scope),
			`<div class="hq-section-title">${__("Selected Range")}
				<span class="hq-period">${this._range_label(s)}</span></div>`,
			'<div class="hq-kpi-row">',
			this._sales_card(s),
			this._transactions_card(s),
			this._apc_card(s),
			this._achievement_card(s),
			"</div>",
			this._daily_rhythm_card(s),
			this._outlet_performance_card(s),
			this._mini_cards(s),
			'<div class="hq-charts">',
			this._category_product_card(s, "a"),
			this._category_product_card(s, "b"),
			this._category_donut_card(s),
			this._outlet_donut_card(s),
			"</div>",
			this._hours_card(s),
			this._product_card(s),
			this._chart_notes(s),
			this._notice_card(s.channels, s.pax),
			this._footer(s),
		];
		this.$root.html(parts.join(""));
		this._bind_delegates();
		this._render_charts(s);
		this._apply_outlet_filter();
	}

	_range_label(s) {
		const scope = s.scope || {};
		const cut = s.range && s.range.cut_at ? ` · ${__("cut at")} ${s.range.cut_at}` : "";
		return `${frappe.utils.escape_html(scope.from_date)} - ${frappe.utils.escape_html(scope.to_date)}${cut}`;
	}

	_scope_line(scope) {
		const companies = (scope.companies || []).length;
		const label = companies > 3 ? `${companies} ${__("companies")}` : (scope.companies || []).join(", ");
		return `<div class="hq-scope">${__("Scope")}: <b>${frappe.utils.escape_html(label)}</b>
			· ${__("Base currency")}: <b>${scope.default_currency || "-"}</b>
			${(scope.companies || []).length > 1 ? `· <span class="hq-muted">${__("each company in its own base currency; no cross-currency sum")}</span>` : ""}
		</div>`;
	}

	// ------------------------------------------------------------------
	// Formatters
	// ------------------------------------------------------------------

	_metric_text(metric) {
		if (!metric) return "-";
		const money = (v, ccy) => `<span class="hq-money">${HQ_UTILS.fmtMoney(v, ccy)}</span>`;
		const extra = Object.entries(metric.by_currency || {})
			.filter(([ccy]) => ccy !== metric.default_currency)
			.map(([ccy, v]) => money(v, ccy));
		const main = money(metric.default, metric.default_currency);
		return extra.length
			? `${main} <span class="hq-extra">+ ${extra.join(" + ")}</span>`
			: main;
	}

	// Per-currency map cell: default currency value + smaller extras (all
	// currencies stay visible; never merged into one number).
	_map_cell(map, default_ccy, fmt, signed) {
		const obj = map || {};
		const render = (v) => {
			if (v === null || v === undefined) return `<span class="hq-muted">N/A</span>`;
			return fmt(v, default_ccy, signed);
		};
		let html = render(obj[default_ccy]);
		const extras = Object.entries(obj)
			.filter(([c]) => c !== default_ccy)
			.map(([c, v]) => `${c}: ${v === null || v === undefined ? "N/A" : fmt(v, c, signed)}`);
		if (extras.length) html += ` <span class="hq-extra">${extras.join(" · ")}</span>`;
		return html;
	}

	_money_cell(map, default_ccy, signed) {
		return this._map_cell(map, default_ccy, (v, ccy, sg) => {
			const n = Number(v);
			const prefix = sg && n > 0 ? "+" : "";
			return `<span class="hq-money">${prefix}${HQ_UTILS.fmtMoney(n, ccy)}</span>`;
		}, signed);
	}

	_pct_cell(map, default_ccy, signed) {
		return this._map_cell(map, default_ccy, (v, ccy, sg) => {
			if (v === null || v === undefined) return "N/A";
			const n = Number(v);
			return (sg && n > 0 ? "+" : "") + HQ_UTILS.fmtPct(n, 1);
		}, signed);
	}

	_na() {
		return '<span class="hq-muted">N/A</span>';
	}

	// Inline progress bar; pct is clamped for the fill, never for the label.
	_bar(pct, cls) {
		const value = pct === null || pct === undefined ? 0 : Math.max(0, Math.min(100, Number(pct)));
		return `<span class="hq-bar${cls ? ` ${cls}` : ""}" role="img"
			aria-label="${HQ_UTILS.fmtPct(pct === null || pct === undefined ? 0 : pct)}"><span class="hq-bar-fill" style="width:${value}%"></span></span>`;
	}

	// ------------------------------------------------------------------
	// Daily rhythm strip — today vs the SAME weekday last week (the one
	// comparison the range filters cannot align automatically) plus the
	// pro-rated daily target pace. Replaces the old Monthly / Daily
	// matrices whose figures duplicated the KPI row and the outlet table.
	// ------------------------------------------------------------------

	_daily_rhythm_card(s) {
		const d = s.daily;
		if (!d || !d.totals) return "";
		const t = s.targets || {};
		const ccy = s.scope.default_currency;
		const w = s.windows || {};
		const lw = d.last_week_same || {};
		const growth = d.growth_vs_last_week_pct || {};
		const pwGrowth = d.growth_vs_prior_weekday_pct || {};

		const resultSales = ((d.totals.net_tax_incl || {}).by_currency || {})[ccy];
		const lwSales = ((lw.net_tax_incl || {}).by_currency || {})[ccy];
		const resultApc = ((d.totals.apc || {}).by_currency || {})[ccy];
		const lwApc = ((lw.apc || {}).by_currency || {})[ccy];
		const orders = d.totals.orders ?? 0;
		const lwOrders = lw.orders;
		const tcGrowth = HQ_UTILS.growthPct(orders, lwOrders);
		const apcGrowth = HQ_UTILS.growthPct(resultApc, lwApc);
		const dailyTarget = t.available ? (t.daily_target_sales || {})[ccy] : null;

		const cell = (label, value, sub) => `<div class="hq-mini">
			<div class="hq-kpi-label">${label}</div>
			<div class="hq-kpi-value">${value}</div>
			<div class="hq-kpi-sub">${sub}</div>
		</div>`;
		const vs = (today, last) =>
			`<span class="hq-money">${HQ_UTILS.fmtMoney(today, ccy)}</span> ${__("vs")} <span class="hq-money">${HQ_UTILS.fmtMoney(last, ccy)}</span>`;

		return `<div class="hq-card hq-rhythm">
			<div class="hq-card-title">${__("Daily Rhythm")}
				<span class="hq-period">${frappe.utils.escape_html(w.day || "")} · ${__("vs")} ${frappe.utils.escape_html(w.last_week_same || "")} (${__("same weekday last week")})</span></div>
			<div class="hq-minis">
			${cell(__("Sales"), this._signed_pct(growth[ccy]), vs(resultSales, lwSales))}
			${cell(__("TC"), this._signed_pct(tcGrowth), `${HQ_UTILS.fmtCount(orders)} ${__("vs")} ${HQ_UTILS.fmtCount(lwOrders ?? 0)}`)}
			${cell(__("Avg Ticket"), this._signed_pct(apcGrowth), vs(resultApc, lwApc))}
			${cell(
				__("Daily Target"),
				dailyTarget != null
					? `<span class="hq-money">${HQ_UTILS.fmtMoney(dailyTarget, ccy)}</span> / ${__("day")}`
					: this._na(),
				dailyTarget != null ? __("from monthly target") : __("not set yet")
			)}
			</div>
			<div class="hq-kpi-sub hq-muted" style="margin-top: 8px">${__("Growth vs prior weekday")} (${frappe.utils.escape_html(w.prior_weekday || "")}): ${this._signed_pct((pwGrowth || {})[ccy])}${d.cutoff ? ` · ${__("cut at")} ${frappe.utils.escape_html(d.cutoff)}` : ""}</div>
		</div>`;
	}

	_signed(value, ccy) {
		if (value === null || value === undefined) return this._na();
		const n = Number(value);
		const prefix = n > 0 ? "+" : "";
		return `<span class="hq-money">${prefix}${HQ_UTILS.fmtMoney(n, ccy)}</span>`;
	}

	_signed_pct(value) {
		if (value === null || value === undefined) return this._na();
		const n = Number(value);
		return `<span class="hq-money">${n > 0 ? "+" : ""}${HQ_UTILS.fmtPct(n, 1)}</span>`;
	}

	// ------------------------------------------------------------------
	// Mini stats — one strip card, four equal slots split by hairlines
	// ------------------------------------------------------------------

	_mini_cards(s) {
		const ccy = s.scope.default_currency;
		const t = s.turnover || {};
		const h = s.highlights || {};
		const fav = s.favorite_product;
		const cells = [];

		const change = t.change_pct_vs_prev_comparable || {};
		cells.push(`<div class="hq-mini">
			<div class="hq-kpi-label">${__("Turnover MTD")}</div>
			<div class="hq-kpi-value">${this._metric_text(t.this_month_net)}</div>
			<div class="hq-kpi-sub">${__("vs same elapsed days last month")}: ${this._pct_cell(change, ccy, true)}</div>
		</div>`);

		if (h.biggest_outlet) {
			cells.push(`<div class="hq-mini">
				<div class="hq-kpi-label">${__("Biggest Outlet (share)")}</div>
				<div class="hq-kpi-value hq-kpi-name">${frappe.utils.escape_html(h.biggest_outlet.company)}</div>
				<div class="hq-kpi-sub">${this._signed_pct(h.biggest_outlet.share_pct)} · <span class="hq-money">${HQ_UTILS.fmtMoney(h.biggest_outlet.net_tax_incl, h.biggest_outlet.currency)}</span></div>
			</div>`);
		} else {
			cells.push(`<div class="hq-mini"><div class="hq-kpi-label">${__("Biggest Outlet (share)")}</div><div class="hq-kpi-sub">${__("No data")}</div></div>`);
		}

		if (h.most_transactions_outlet) {
			cells.push(`<div class="hq-mini">
				<div class="hq-kpi-label">${__("Outlet with Most Transactions")}</div>
				<div class="hq-kpi-value hq-kpi-name">${frappe.utils.escape_html(h.most_transactions_outlet.company)}</div>
				<div class="hq-kpi-sub">${HQ_UTILS.fmtCount(h.most_transactions_outlet.orders)} ${__("orders")}</div>
			</div>`);
		} else {
			cells.push(`<div class="hq-mini"><div class="hq-kpi-label">${__("Outlet with Most Transactions")}</div><div class="hq-kpi-sub">${__("No data")}</div></div>`);
		}

		if (fav) {
			cells.push(`<div class="hq-mini">
				<div class="hq-kpi-label">${__("Favorite Product")}</div>
				<div class="hq-kpi-value hq-kpi-name">${frappe.utils.escape_html(fav.item_name)}</div>
				<div class="hq-kpi-sub">${HQ_UTILS.fmtCount(fav.qty)} ${__("qty")} · ${frappe.utils.escape_html(fav.item_group || "")}</div>
			</div>`);
		} else {
			cells.push(`<div class="hq-mini"><div class="hq-kpi-label">${__("Favorite Product")}</div><div class="hq-kpi-sub">${__("No data")}</div></div>`);
		}
		return `<div class="hq-card hq-minis">${cells.join("")}</div>`;
	}

	// ------------------------------------------------------------------
	// KPI row: Total Sales / Transactions / APC / Achievement (MTD aggregate)
	// ------------------------------------------------------------------

	_sales_card(s) {
		const r = s.range || {};
		return `<div class="hq-card hq-kpi hq-kpi--primary hq-sales-toggle">
			<div class="hq-kpi-label">${__("Total Sales")} <button type="button" class="hq-toggle" data-hq-toggle>${__("incl. taxes & charges")}</button></div>
			<div class="hq-kpi-value hq-sales-incl">${this._metric_text(r.net_tax_incl)}</div>
			<div class="hq-kpi-value hq-sales-pretax" hidden>${this._metric_text(r.net_pretax)}</div>
			<div class="hq-kpi-sub">${__("Taxes & charges")}: ${this._metric_text(r.taxes)}
				· ${__("Refunds")}: ${this._metric_text(r.refunds)}</div>
		</div>`;
	}

	_transactions_card(s) {
		const r = s.range || {};
		return `<div class="hq-card hq-kpi">
			<div class="hq-kpi-label">${__("Total Transactions")}</div>
			<div class="hq-kpi-value">${HQ_UTILS.fmtCount(r.orders ?? 0)}</div>
			<div class="hq-kpi-sub">${__("Refund invoices")}: ${HQ_UTILS.fmtCount(r.refund_orders ?? 0)}
				· <span class="hq-muted">${__("Pax")}: ${__("N/A")} (${__("no source field on POS invoices")})</span></div>
		</div>`;
	}

	_apc_card(s) {
		const r = s.range || {};
		return `<div class="hq-card hq-kpi">
			<div class="hq-kpi-label">${__("Avg per Transaction")}</div>
			<div class="hq-kpi-value">${this._metric_text(r.apc)}</div>
			<div class="hq-kpi-sub">${__("Net incl. tax ÷ orders")} · ${__("per currency, never merged")}</div>
		</div>`;
	}

	_achievement_card(s) {
		const t = s.targets || {};
		const ccy = s.scope.default_currency;
		const has = !!t.available;
		const pct = has ? (t.achievement_sales_pct || {})[ccy] : null;
		const target = has ? ((t.target_sales || {}).by_currency || {})[ccy] : null;
		const mtd = ((s.monthly.net_tax_incl || {}).by_currency || {})[ccy];
		const sub = has
			? `${__("MTD")} <span class="hq-money">${HQ_UTILS.fmtMoney(mtd ?? 0, ccy)}</span> / ${__("Target")} <span class="hq-money">${HQ_UTILS.fmtMoney(target ?? 0, ccy)}</span>`
			: frappe.utils.escape_html(t.notice || __("Monthly target not set"));
		return `<div class="hq-card hq-kpi hq-kpi--ach">
			<div class="hq-kpi-label">${__("Achievement (MTD)")} <span class="hq-period">${frappe.utils.escape_html(s.windows.month_start || "")}</span></div>
			<div class="hq-kpi-value">${pct == null ? this._na() : HQ_UTILS.fmtPct(pct, 1)}</div>
			${this._bar(pct, "hq-bar--lg")}
			<div class="hq-kpi-sub">${sub}</div>
		</div>`;
	}

	// ------------------------------------------------------------------
	// Outlet Performance — the centrepiece table. One row per outlet
	// (= company): monthly target vs MTD actual with progress, the overall
	// payback ("balik modal") progress, then the selected-range figures.
	// ------------------------------------------------------------------

	_outlet_rows(s) {
		const monthly = {};
		((s.targets || {}).by_company || []).forEach((r) => {
			monthly[r.company] = r;
		});
		const overall = ((s.targets || {}).overall || {}).by_company || {};
		const ranked = (s.outlet_ranking || []).map((r) => ({
			...r,
			target: monthly[r.company] || null,
			overall: overall[r.company] || null,
		}));
		// Outlets with no sales in the selected range still carry their
		// targets — append them (zero sales) instead of hiding them.
		const seen = new Set(ranked.map((r) => r.company));
		const quiet = Object.keys(monthly)
			.filter((c) => !seen.has(c))
			.sort()
			.map((c) => ({
				company: c,
				currency: (monthly[c] || {}).currency,
				gross: 0,
				net_tax_incl: 0,
				orders: 0,
				apc: null,
				share_pct: null,
				profiles: [],
				target: monthly[c],
				overall: overall[c] || null,
			}));
		return ranked.concat(quiet);
	}

	_outlet_row(r) {
		const t = r.target || {};
		const o = r.overall;
		const ccy = r.currency || (this.state.scope || {}).default_currency;
		const missing = !t || t.missing;

		const targetCell = missing
			? `<div class="hq-item-code">${__("not set yet")}</div>`
			: `<span class="hq-money">${HQ_UTILS.fmtMoney(t.target_sales, ccy)}</span>
				<div class="hq-item-code">${HQ_UTILS.fmtCount(t.target_transactions ?? 0)} ${__("target TC")}</div>`;
		const mtdCell = `<span class="hq-money">${HQ_UTILS.fmtMoney(t.mtd_net_tax_incl, ccy)}</span>
			<div class="hq-item-code">${HQ_UTILS.fmtCount(t.mtd_orders ?? 0)} ${__("TC")}</div>`;
		// The only per-row bar lives in Achievement; Target / MTD / Balik
		// Modal are plain numbers.
		const achCell = missing
			? this._na()
			: `${this._bar(t.achievement_sales_pct)} <b class="hq-ach-pct">${HQ_UTILS.fmtPct(t.achievement_sales_pct, 1)}</b>
				${t.projected_sales != null ? `<div class="hq-item-code">${__("proj.")} <span class="hq-money">${HQ_UTILS.fmtMoney(t.projected_sales, ccy)}</span> · ${HQ_UTILS.fmtPct(t.projected_achievement_pct, 1)}</div>` : ""}`;
		const overallCell = o
			? `<span class="hq-money">${HQ_UTILS.fmtMoney(o.cumulative_net_tax_incl, ccy)}</span>
				<div class="hq-item-code"><span class="hq-money">${HQ_UTILS.fmtMoney(o.overall_target, ccy)}</span> · <b class="hq-ach-pct">${HQ_UTILS.fmtPct(o.achievement_pct, 1)}</b>${o.from_date ? ` · ${frappe.utils.escape_html(o.from_date)}` : ""}</div>`
			: this._na();

		return `<tr data-hq-outlet-row data-hq-company="${frappe.utils.escape_html(r.company)}">
			<td class="hq-outlet-name">${frappe.utils.escape_html(r.company)}</td>
			<td class="hq-num"><span class="hq-money">${HQ_UTILS.fmtMoney(r.net_tax_incl, r.currency)}</span>
				${r.share_pct != null ? `<div class="hq-item-code">${HQ_UTILS.fmtPct(r.share_pct)}</div>` : ""}</td>
			<td class="hq-num">${HQ_UTILS.fmtCount(r.orders)}</td>
			<td class="hq-num">${r.apc === null ? "N/A" : `<span class="hq-money">${HQ_UTILS.fmtMoney(r.apc, r.currency)}</span>`}</td>
			<td class="hq-num">${targetCell}</td>
			<td class="hq-num">${mtdCell}</td>
			<td class="hq-num hq-ach">${achCell}</td>
			<td class="hq-num">${overallCell}</td>
		</tr>`;
	}

	_outlet_performance_card(s) {
		const rows = this._outlet_rows(s).map((r) => this._outlet_row(r)).join("");
		return `<div class="hq-card hq-card--table hq-card--outlets">
			<div class="hq-card-title">${__("Outlet Performance")}
				<span class="hq-period">${__("MTD targets")} · ${frappe.utils.escape_html(s.windows.month_start || "")} · ${__("range figures")}: ${this._range_label(s)}</span></div>
			<div class="hq-rank-controls">
				<div class="hq-search"><input type="search" class="hq-input" data-hq-outlet-search
					placeholder="${__("Search outlet…")}" value="${frappe.utils.escape_html(this.outlet_query)}" aria-label="${__("Search outlet")}"></div>
				<span class="hq-kpi-sub hq-muted">${__("Outlet = company; balik modal = cumulative sales vs overall target")}</span>
				<button class="btn btn-xs btn-default" data-hq-goto-targets>${__("Manage Targets")}</button>
				<button class="btn btn-xs btn-default" data-hq-export="outlets">${__("Export CSV")}</button>
			</div>
			<div class="hq-table-scroll"><table class="hq-table hq-table--outlets">
			<thead><tr>
				<th>${__("Outlet (Company)")}</th>
				<th class="hq-num">${__("Net Sales")}</th>
				<th class="hq-num">${__("TC")}</th>
				<th class="hq-num">${__("Avg Ticket")}</th>
				<th class="hq-num">${__("Monthly Target")}</th>
				<th class="hq-num">${__("MTD")}</th>
				<th class="hq-num">${__("Achievement")}</th>
				<th class="hq-num">${__("Balik Modal")}</th>
			</tr></thead>
				<tbody>${rows || `<tr><td colspan="8" class="hq-muted">${__("No data")}</td></tr>`}</tbody>
			</table></div>
			<div class="hq-pager">
				<button class="btn btn-xs btn-default" disabled data-hq-outlet-page="prev">${__("Previous")}</button>
				<span data-hq-outlet-pager></span>
				<button class="btn btn-xs btn-default" disabled data-hq-outlet-page="next">${__("Next")}</button>
			</div>
		</div>`;
	}

	// ------------------------------------------------------------------
	// Outlet table filtering + client-side paging (no refetch: the payload
	// already carries every row). Search filters, the active page slices.
	// ------------------------------------------------------------------

	_apply_outlet_filter() {
		const q = (this.outlet_query || "").toLowerCase();
		const $rows = this.$root.find("[data-hq-outlet-row]");
		const matched = $rows.filter(
			(_, el) => !q || String($(el).data("hq-company") || "").toLowerCase().includes(q)
		);
		const total = matched.length;
		const pages = Math.max(1, Math.ceil(total / HQ_OUTLET_PAGE_SIZE));
		this.outlet_page = Math.min(Math.max(1, this.outlet_page), pages);
		$rows.hide();
		matched
			.slice((this.outlet_page - 1) * HQ_OUTLET_PAGE_SIZE, this.outlet_page * HQ_OUTLET_PAGE_SIZE)
			.show();
		const start = total ? (this.outlet_page - 1) * HQ_OUTLET_PAGE_SIZE + 1 : 0;
		const end = Math.min(this.outlet_page * HQ_OUTLET_PAGE_SIZE, total);
		this.$root.find("[data-hq-outlet-pager]").text(
			`${start} - ${end} ${__("of")} ${total} ${__("outlets")}`
		);
		this.$root.find('[data-hq-outlet-page="prev"]').prop("disabled", this.outlet_page <= 1);
		this.$root.find('[data-hq-outlet-page="next"]').prop("disabled", this.outlet_page >= pages);
	}

	// ------------------------------------------------------------------
	// Peak Hour card (full width) — the hour window reshapes THIS card only
	// ------------------------------------------------------------------

	_hours_card(s) {
		const win = HQ_UTILS.parseHourWindow(this.hour_start, this.hour_end) || {
			start: 0,
			end: 24,
			overnight: false,
		};
		const bins = HQ_UTILS.hourWindowBins((s.hours || {}).rows, win.start, win.end);
		const stats = HQ_UTILS.windowStats(bins);
		const ccy = s.scope.default_currency;
		const peak = stats.peak
			? `${__("Peak")} <b>${HQ_UTILS.hourLabel(stats.peak.hour)}</b> (<span class="hq-money">${HQ_UTILS.fmtMoney(stats.peak.net_sales, ccy)}</span>, ${HQ_UTILS.fmtCount(stats.peak.orders)} ${__("orders")})`
			: __("No data");
		const hourOpts = (from, to, sel) =>
			Array.from({ length: to - from + 1 }, (_, i) => {
				const h = from + i;
				return `<option value="${h}"${h === sel ? " selected" : ""}>${HQ_UTILS.hourLabel(h)}</option>`;
			}).join("");
		const winLabel = `${HQ_UTILS.hourLabel(win.start)} – ${HQ_UTILS.hourLabel(win.end)}${win.overnight ? ` · ${__("overnight")}` : ""}`;
		return `<div class="hq-card hq-kpi hq-card--hours">
			<div class="hq-kpi-label">${__("Peak Hour")} <span class="hq-period">${winLabel}</span></div>
			<div class="hq-kpi-sub">${peak}</div>
			<div class="hq-hour-controls">
				<label class="hq-ctl">${__("From")}
					<select class="hq-select" data-hq-hour-from aria-label="${__("Peak window start hour")}">${hourOpts(0, 23, win.start)}</select></label>
				<label class="hq-ctl">${__("To")}
					<select class="hq-select" data-hq-hour-to aria-label="${__("Peak window end hour")}">${hourOpts(1, 24, win.end)}</select></label>
				<span class="hq-kpi-sub hq-muted">${__("this chart only")}</span>
			</div>
			<div class="hq-hour-scroll"><div class="hq-hour-chart"></div></div>
		</div>`;
	}

	// ------------------------------------------------------------------
	// Two independent "Top Selling <category>" donut cards (slots a / b).
	// Slots are never shown to the user (the dropdown selection IS the
	// title); only the accessible labels say first/second category.
	// ------------------------------------------------------------------

	_category_product_card(s, slot) {
		const cp = (s.category_products || {})[slot] || {};
		const groups = s.item_groups || [];
		const sel = cp.invalid ? "" : cp.category || "";
		const aria = slot === "a" ? __("First category") : __("Second category");
		const options = [{ label: __("Choose category…"), value: "" }].concat(
			groups.map((g) => ({ label: g, value: g }))
		);
		const select = `<select class="hq-select hq-select--inline" data-hq-cat="${slot}" aria-label="${aria}">
			${options
				.map(
					(o) =>
						`<option value="${frappe.utils.escape_html(o.value)}"${o.value === sel ? " selected" : ""}>${frappe.utils.escape_html(o.label)}</option>`
				)
				.join("")}
		</select>`;
		const ccy = cp.currency || s.scope.default_currency;
		const rows = HQ_UTILS.categoryDonutRows(cp, __("Other"));
		let body;
		if (cp.invalid) {
			body = this._donut_placeholder(__("Category no longer exists — please choose another"));
		} else if (!sel) {
			body = this._donut_placeholder(__("Choose a category to see its top selling items"));
		} else if (!rows.length) {
			body = this._donut_placeholder(__("No positive sales in this category in this period"));
		} else {
			const of =
				rows.length - (Number(cp.other_net) > 0 ? 1 : 0) < cp.items_with_sales
					? ` · ${__("top 5 of")} ${cp.items_with_sales} ${__("items")}`
					: "";
			body = `<div class="hq-donut"><div class="hq-donut-chart" data-hq-donut="catprod-${slot}"></div>
				${this._donut_legend(rows, "net_amount", "item_name", ccy, "qty")}</div>
				<div class="hq-kpi-sub hq-muted">${__("Share of category net revenue")} <span class="hq-money">${HQ_UTILS.fmtMoney(cp.category_total, ccy)}</span>${of}</div>`;
		}
		return `<div class="hq-card hq-card--donut">
			<div class="hq-card-title">${__("Top Selling")} ${select}${sel && !cp.invalid ? `<span class="hq-period">${__("pre-tax net")} · ${frappe.utils.escape_html(ccy || "")}</span>` : ""}</div>
			${body}
		</div>`;
	}

	// Neutral empty state: a dashed ring (no fake arcs) with the reason.
	_donut_placeholder(msg) {
		return `<div class="hq-donut"><div class="hq-donut-empty"><span>${frappe.utils.escape_html(msg)}</span></div></div>`;
	}

	// ------------------------------------------------------------------
	// Donut cards (dynamic categories / top outlets) with value legends
	// ------------------------------------------------------------------

	_donut_legend(rows, key, labelKey, ccy, subKey) {
		return `<ul class="hq-legend">${rows
			.map(
				(r, i) => `<li><span class="hq-dot" style="background:${HQ_PALETTE[i % HQ_PALETTE.length]}"></span>
				<span class="hq-legend-label">${frappe.utils.escape_html(String(r[labelKey]))}${
					subKey && Number(r[subKey]) > 0
						? ` <span class="hq-legend-sub">${HQ_UTILS.fmtCount(r[subKey])} ${__("qty")}</span>`
						: ""
				}</span>
				<span class="hq-legend-value hq-money">${HQ_UTILS.fmtMoney(r[key], ccy)}</span>
				<span class="hq-legend-pct">${HQ_UTILS.fmtPct(r.share_pct)}</span></li>`
			)
			.join("")}</ul>`;
	}

	_category_donut_card(s) {
		const ct = s.category_top || {};
		const rows = (ct.rows || []).filter((r) => Number(r.net_amount) > 0);
		const title = `${__("Top Selling Categories")} <span class="hq-period">${__("pre-tax net")} · ${frappe.utils.escape_html(ct.currency || "")}</span>`;
		let body;
		if (!rows.length) {
			body = this._donut_placeholder(__("No positive sales in this period"));
		} else {
			body = `<div class="hq-donut"><div class="hq-donut-chart" data-hq-donut="category"></div>
				${this._donut_legend(rows, "net_amount", "item_group", ct.currency)}</div>
				${rows.length < (ct.groups_with_sales || rows.length) ? `<div class="hq-kpi-sub hq-muted">${__("top 5 of")} ${ct.groups_with_sales} ${__("groups")}</div>` : ""}`;
		}
		return `<div class="hq-card hq-card--donut"><div class="hq-card-title">${title}</div>${body}</div>`;
	}

	// One compact note for the whole chart grid: each card title carries the
	// short currency basis; the detailed exclusion sentences used to repeat
	// inline under every card.
	_chart_notes(s) {
		const lines = [];
		["a", "b"].forEach((slot) => {
			const cp = (s.category_products || {})[slot] || {};
			if (!cp.category) return;
			const bits = [];
			if ((cp.excluded_currencies || []).length)
				bits.push(`${__("excluded (different currency, never merged)")}: ${cp.excluded_currencies.join(", ")}`);
			if (cp.excluded_nonpositive)
				bits.push(`${cp.excluded_nonpositive} ${__("item(s) net ≤ 0 excluded")}`);
			if (bits.length)
				lines.push(`<b>${frappe.utils.escape_html(cp.category)}</b>: ${frappe.utils.escape_html(bits.join(" · "))}`);
		});
		const ct = s.category_top || {};
		if ((ct.excluded_currencies || []).length)
			lines.push(`<b>${__("Top Selling Categories")}</b>: ${__("excluded (different currency, never merged)")}: ${ct.excluded_currencies.join(", ")}`);
		if (ct.excluded_nonpositive)
			lines.push(`<b>${__("Top Selling Categories")}</b>: ${ct.excluded_nonpositive} ${__("group(s) net ≤ 0 excluded")}`);
		return `<details class="hq-notes"><summary>${__("Currency & basis notes")}</summary>
			<div class="hq-kpi-sub hq-muted">${__("Category charts show pre-tax net revenue, sub-groups included, positive amounts only")}</div>
			${lines.map((l) => `<div class="hq-kpi-sub hq-muted">${l}</div>`).join("")}
		</details>`;
	}

	_outlet_donut_card(s) {
		const ccy = s.scope.default_currency;
		const rows = (s.outlet_ranking || [])
			.filter((r) => r.currency === ccy && Number(r.net_tax_incl) > 0)
			.slice(0, 5);
		const title = `${__("Top Outlets")} <span class="hq-period">${__("net incl. tax")} · ${ccy || ""}</span>`;
		let body;
		if (!rows.length) {
			body = `<p class="hq-muted hq-empty">${__("No positive sales in this period")}</p>`;
		} else {
			body = `<div class="hq-donut"><div class="hq-donut-chart" data-hq-donut="outlet"></div>
				${this._donut_legend(rows, "net_tax_incl", "company", ccy)}</div>`;
		}
		return `<div class="hq-card hq-card--donut"><div class="hq-card-title">${title}</div>${body}</div>`;
	}

	// ------------------------------------------------------------------
	// Product Ranking (full width)
	// ------------------------------------------------------------------

	_product_card(s) {
		const pr = s.product_ranking || {};
		const options = [{ label: __("All Categories"), value: "" }].concat(
			(pr.categories || []).map((c) => ({ label: c, value: c }))
		);
		const select = `<select class="hq-select" data-hq-category aria-label="${__("Filter by category")}">
			${options.map((o) => `<option value="${frappe.utils.escape_html(o.value)}"${(o.value || "") === (pr.category || "") ? " selected" : ""}>${frappe.utils.escape_html(o.label)}</option>`).join("")}
		</select>`;
		return `<div class="hq-card hq-card--table hq-card--rank">
			<div class="hq-card-title">${__("Product Ranking")} <span class="hq-period">${this._range_label(s)}</span></div>
			<div class="hq-rank-controls">${select}
				<button class="btn btn-xs btn-default" data-hq-export="products">${__("Export CSV")}</button></div>
			${this._product_table(pr)}
			${this._product_pager(pr)}
		</div>`;
	}

	_product_table(pr) {
		const rows = (pr.rows || [])
			.map(
				(r, i) => `<tr>
				<td class="hq-num">${(pr.page - 1) * pr.page_size + i + 1}</td>
				<td>${frappe.utils.escape_html(r.item_name)}
					${r.item_code ? `<div class="hq-item-code">${frappe.utils.escape_html(r.item_code)}</div>` : ""}</td>
				<td>${frappe.utils.escape_html(r.item_group || "")}</td>
				<td class="hq-num">${HQ_UTILS.fmtCount(r.qty)}</td>
				<td class="hq-num"><span class="hq-money">${HQ_UTILS.fmtMoney(r.net_amount, "")}</span></td>
				<td class="hq-num">${HQ_UTILS.fmtPct(r.share_pct)}</td>
			</tr>`
			)
			.join("");
		return `<div class="hq-table-scroll"><table class="hq-table">
			<thead><tr><th class="hq-num">#</th><th>${__("Item")}</th><th>${__("Category")}</th>
			<th class="hq-num">${__("Qty (net)")}</th><th class="hq-num">${__("Net Sales (pre-tax)")}</th><th class="hq-num">${__("Share %")}</th></tr></thead>
			<tbody>${rows || `<tr><td colspan="6" class="hq-muted">${__("No data")}</td></tr>`}</tbody>
		</table></div>`;
	}

	_product_pager(pr) {
		if (!pr || !pr.total) return "";
		const pages = Math.ceil(pr.total / pr.page_size);
		return `<div class="hq-pager">
			<button class="btn btn-xs btn-default" ${pr.page <= 1 ? "disabled" : ""} data-hq-page="prev">${__("Previous")}</button>
			<span>${pr.page} / ${pages} (${HQ_UTILS.fmtCount(pr.total)} ${__("items")})</span>
			<button class="btn btn-xs btn-default" ${pr.page >= pages ? "disabled" : ""} data-hq-page="next">${__("Next")}</button>
		</div>`;
	}

	_notice_card(...sections) {
		const rows = sections
			.filter((sec) => sec && !sec.available)
			.map((sec) => `<div class="hq-kpi-sub hq-muted">ℹ ${frappe.utils.escape_html(sec.notice || "")}</div>`);
		if (!rows.length) return "";
		return `<details class="hq-card hq-notes">
			<summary>${__("Data Source Notes")}</summary>
			${rows.join("")}
		</details>`;
	}

	_footer(s) {
		const w = s.windows || {};
		return `<div class="hq-footer hq-muted">
			${__("Month")}: ${w.month_start || "-"} · ${__("days elapsed")}: ${w.days_elapsed ?? "-"}/${w.days_in_month ?? "-"}
			· ${__("Generated")}: ${s.generated_at} (${__("server time")})
		</div>`;
	}

	// ------------------------------------------------------------------
	// Charts — every chart is tracked and destroyed before re-render
	// (frappe-charts leaks ResizeObserver/resize listeners otherwise).
	// ------------------------------------------------------------------

	_destroy_charts() {
		(this._charts || []).forEach((c) => {
			try {
				c.destroy();
			} catch (e) {
				// chart already gone — nothing to clean up
			}
		});
		this._charts = [];
		this._hour_chart = null;
	}

	_render_charts(s) {
		this._render_hour_chart(s);
		if (!frappe.Chart) return;
		const ccy = s.scope.default_currency;
		const cp = s.category_products || {};
		["a", "b"].forEach((slot) => {
			const card = cp[slot] || {};
			this._add_donut(
				`[data-hq-donut="catprod-${slot}"]`,
				HQ_UTILS.categoryDonutRows(card, __("Other")),
				"item_name",
				"net_amount",
				card.currency || ccy
			);
		});
		this._add_donut('[data-hq-donut="category"]', (s.category_top || {}).rows, "item_group", "net_amount", (s.category_top || {}).currency);
		this._add_donut(
			'[data-hq-donut="outlet"]',
			(s.outlet_ranking || []).filter((r) => r.currency === ccy).slice(0, 5),
			"company",
			"net_tax_incl",
			ccy
		);
	}

	// Every donut on the page routes through here: positive-only rows, one
	// palette, value tooltips, hardened observer lifecycle. Empty data draws
	// nothing (the card shows its placeholder instead).
	_add_donut(selector, rows, labelKey, valueKey, ccy) {
		const el = this.$root.find(selector).get(0);
		if (!el) return;
		const data = (rows || []).filter((r) => Number(r[valueKey]) > 0);
		if (!data.length) return;
		const chart = new frappe.Chart(el, {
			data: {
				labels: data.map((r) => String(r[labelKey])),
				datasets: [{ values: data.map((r) => Number(r[valueKey])) }],
			},
			type: "donut",
			colors: HQ_PALETTE,
			height: 190,
			// The page renders its own accessible value legend (.hq-legend);
			// frappe-charts' built-in legend would duplicate and truncate it.
			showLegend: false,
			tooltipOptions: {
				formatTooltipY: (value) => HQ_UTILS.fmtMoney(value, ccy),
			},
		});
		_harden_chart(chart);
		this._charts.push(chart);
	}

	_render_hour_chart(s) {
		const el = this.$root.find(".hq-hour-chart").get(0);
		if (!el || !frappe.Chart) return;
		// Only the selected window is drawn; hours without sales stay explicit
		// zero bins so every bar maps to its hour.
		const win = HQ_UTILS.parseHourWindow(this.hour_start, this.hour_end) || { start: 0, end: 24 };
		const bins = HQ_UTILS.hourWindowBins((s.hours || {}).rows, win.start, win.end);
		// Min-width scales with the bin count, so a short window (e.g. 05–18)
		// fits its container instead of forcing a 940px scroll; longer windows
		// scroll horizontally with the identical full axis.
		el.style.minWidth = `${HQ_UTILS.hourChartMinWidth(bins.length)}px`;
		const chart = new frappe.Chart(el, {
			data: {
				labels: bins.map((r) => HQ_UTILS.hourLabel(r.hour)),
				datasets: [{ name: __("Net Sales"), values: bins.map((r) => r.net_sales) }],
			},
			type: "bar",
			colors: ["#2490ef"],
			height: 180,
			barOptions: { spaceRatio: 0.3 },
			// xIsSeries skips (never truncates) labels that don't fit; the
			// proportional min-width above keeps every "HH:00" label drawable.
			showLegend: false,
			axisOptions: { xIsSeries: true, seriesLabelSpaceRatio: 1.15, shortenYAxisNumbers: true },
			tooltipOptions: {
				formatTooltipX: (label) => HQ_UTILS.hourRangeLabel(label),
				formatTooltipY: (value) => HQ_UTILS.fmtMoney(value, (s.scope || {}).default_currency),
			},
		});
		_harden_chart(chart);
		this._hour_chart = chart;
		this._charts.push(chart);
	}

	// Re-render the Peak Hour card in place — the hour window changes only
	// this card, so no server round-trip is needed (rows are already loaded).
	_update_peak_card() {
		const s = this.state;
		if (!s) return;
		const card = this.$root.find(".hq-card--hours");
		if (!card.length) return;
		if (this._hour_chart) {
			try {
				this._hour_chart.destroy();
			} catch (e) {
				// already gone
			}
			this._charts = this._charts.filter((c) => c !== this._hour_chart);
			this._hour_chart = null;
		}
		card.replaceWith(this._hours_card(s));
		this._render_hour_chart(s);
	}

	_set_hour_window() {
		const from = Number(this.$root.find("[data-hq-hour-from]").val());
		const to = Number(this.$root.find("[data-hq-hour-to]").val());
		const win = HQ_UTILS.parseHourWindow(from, to);
		if (!win) {
			frappe.show_alert({
				message: __("Peak window cannot be empty — From and To must differ"),
				indicator: "orange",
			});
			this.$root.find("[data-hq-hour-from]").val(this.hour_start);
			this.$root.find("[data-hq-hour-to]").val(this.hour_end);
			return;
		}
		this.hour_start = win.start;
		this.hour_end = win.end;
		this._save_prefs();
		this._update_peak_card();
	}

	_set_category(slot, value) {
		this[`category_${slot}`] = value || "";
		this.product_page = 1;
		this._save_prefs();
		this.refresh();
	}

	// ------------------------------------------------------------------
	// Events delegated from the DOM
	// ------------------------------------------------------------------

	_bind_delegates() {
		this.$root
			.off("click", "[data-hq-page]")
			.on("click", "[data-hq-page]", (e) => {
				const dir = $(e.currentTarget).data("hq-page");
				this.product_page += dir === "next" ? 1 : -1;
				this.product_page = Math.max(1, this.product_page);
				this.refresh();
			})
			.off("change", "[data-hq-category]")
			.on("change", "[data-hq-category]", (e) => {
				this.category = e.currentTarget.value || "";
				this.product_page = 1;
				this.refresh();
			})
			.off("change", "[data-hq-hour-from], [data-hq-hour-to]")
			.on("change", "[data-hq-hour-from], [data-hq-hour-to]", () => this._set_hour_window())
			.off("change", "[data-hq-cat]")
			.on("change", "[data-hq-cat]", (e) => {
				this._set_category($(e.currentTarget).data("hq-cat"), e.currentTarget.value || "");
			})
			.off("input", "[data-hq-outlet-search]")
			.on("input", "[data-hq-outlet-search]", frappe.utils.debounce((e) => {
				this.outlet_query = (e.currentTarget.value || "").trim();
				this.outlet_page = 1;
				this._apply_outlet_filter();
			}, 150))
			.off("click", "[data-hq-outlet-page]")
			.on("click", "[data-hq-outlet-page]", (e) => {
				this.outlet_page += $(e.currentTarget).data("hq-outlet-page") === "next" ? 1 : -1;
				this.outlet_page = Math.max(1, this.outlet_page);
				this._apply_outlet_filter();
			})
			.off("click", "[data-hq-goto-targets]")
			.on("click", "[data-hq-goto-targets]", () => frappe.set_route("outlet-targets"))
			.off("click", "[data-hq-toggle]")
			.on("click", "[data-hq-toggle]", (e) => {
				const card = $(e.currentTarget).closest(".hq-sales-toggle");
				const incl = card.find(".hq-sales-incl").get(0);
				const pretax = card.find(".hq-sales-pretax").get(0);
				const showPretax = !incl.hidden;
				// Toggle the hidden property (not jQuery display): the [hidden]
				// UA rule is what keeps these values mutually exclusive.
				incl.hidden = showPretax;
				pretax.hidden = !showPretax;
				$(e.currentTarget).text(
					showPretax ? __("incl. taxes & charges") : __("pre-tax net")
				);
			})
			.off("click", "[data-hq-export]")
			.on("click", "[data-hq-export]", (e) => {
				this._export_csv($(e.currentTarget).data("hq-export"));
			});
	}

	_export_csv(kind) {
		const s = this.state;
		if (!s) return;
		const sections = [];
		if (!kind || kind === "products") {
			const pr = s.product_ranking || {};
			sections.push(
				HQ_UTILS.toCsv(
					[__("Item"), __("Category"), __("Qty (net)"), __("Net Sales (pre-tax)"), __("Share %")],
					(pr.rows || []).map((r) => [r.item_name, r.item_group, r.qty, r.net_amount, r.share_pct])
				)
			);
		}
		if (!kind) sections.push("");
		if (!kind || kind === "outlets") {
			// CSV stays machine-readable: raw ungrouped numbers ("." decimal),
			// never the localized display strings. Per-row currency gets its own
			// column instead of being baked into the amount.
			sections.push(
				HQ_UTILS.toCsv(
					[
						__("Outlet (Company)"),
						__("POS Profiles"),
						__("Currency"),
						__("Net Sales"),
						__("Transactions"),
						__("Avg Ticket"),
						__("Share %"),
						__("Monthly Target"),
						__("Target Transactions"),
						__("MTD Net Sales"),
						__("MTD Transactions"),
						__("Achievement %"),
						__("Projected Sales"),
						__("Overall Sales Target"),
						__("Cumulative Net Sales"),
						__("Overall Achievement %"),
					],
					this._outlet_rows(s).map((r) => [
						r.company,
						(r.profiles || []).map((p) => p.pos_profile).join("; "),
						r.currency,
						r.net_tax_incl,
						r.orders,
						r.apc,
						r.share_pct,
						r.target && !r.target.missing ? r.target.target_sales : "",
						r.target && !r.target.missing ? r.target.target_transactions : "",
						r.target ? r.target.mtd_net_tax_incl : "",
						r.target ? r.target.mtd_orders : "",
						r.target && !r.target.missing ? r.target.achievement_sales_pct : "",
						r.target ? r.target.projected_sales : "",
						r.overall ? r.overall.overall_target : "",
						r.overall ? r.overall.cumulative_net_tax_incl : "",
						r.overall ? r.overall.achievement_pct : "",
					])
				)
			);
		}
		const blob = new Blob(["\ufeff" + sections.join("\n")], { type: "text/csv;charset=utf-8;" });
		const a = document.createElement("a");
		a.href = URL.createObjectURL(blob);
		a.download = `hq-sales-monitoring-${s.scope.to_date}.csv`;
		a.click();
		URL.revokeObjectURL(a.href);
	}
}
