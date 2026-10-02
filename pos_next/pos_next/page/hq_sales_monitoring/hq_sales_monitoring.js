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
function _harden_chart(chart, after) {
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
				if (after) after();
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
		// Outlet-table view toggle: empty outlets (no sales, no TC, no target)
		// stay hidden until asked for. Session-only, never persisted.
		this.show_empty_outlets = false;
		this._autofilled = false;
		// The two independent category-card picks. Defaults are replaced by
		// the user's last saved choices before the first load.
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
		// Custom range: ONE calendar input, two sequential picks — the first
		// click sets From (applied at once; To stays open-ended and the
		// server defaults it to today), the second sets To and the field
		// reads "{from} to {to}". The picker stays open between the picks.
		this.custom_field = this.page.add_field({
			fieldname: "custom_range",
			label: __("Custom Range"),
			fieldtype: "Data",
		});
		const $cinput = this.custom_field.$input;
		$cinput
			.attr("readonly", "readonly")
			.attr("placeholder", __("Pick a start date"))
			.css("cursor", "pointer");
		const lang = frappe.boot && frappe.boot.user && frappe.boot.user.language;
		this._custom_picker = $cinput
			.datepicker({
				language: $.fn.datepicker.language[lang] ? lang : "en",
				range: true,
				autoClose: false,
				toggleSelected: false,
				// the server clamps To to today and rejects From > To, so a
				// future pick can only ever produce an error — cap the picker
				maxDate: new Date(),
				dateFormat: frappe.boot.sysdefaults.date_format || "yyyy-mm-dd",
				onSelect: (_formatted, _date, inst) => {
					const dates = (inst && inst.selectedDates) || [];
					if (!dates.length) return;
					this.range = {
						from_date: frappe.datetime.obj_to_str(dates[0]),
						to_date: dates[1] ? frappe.datetime.obj_to_str(dates[1]) : "",
					};
					this._update_custom_display();
					this.product_page = 1;
					this._custom_refresh();
				},
			})
			.data("datepicker");
		this._custom_refresh = frappe.utils.debounce(() => this.refresh(), 300);
		this._apply_preset(true);
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
	// this.range is the source of truth sent to the API.
	_preset_range(preset) {
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
		return { from_date: from, to_date: to };
	}

	// Preset computes from/to client-side; the Custom preset leaves this.range
	// exactly as the calendar picked it (from-only until the second pick —
	// the server defaults a missing To to today).
	_apply_preset(initial) {
		const custom = this._preset() === "custom";
		this.custom_field.$wrapper.toggle(custom);
		if (initial !== true) this.product_page = 1;
		if (custom) return;
		this.range = this._preset_range(this._preset());
		if (initial !== true) this.refresh();
	}

	// The custom field is display-only: the calendar writes this.range and
	// this redraws the human-readable "from to" summary in the user format.
	_update_custom_display() {
		const r = this.range || {};
		const fmt = (s) => (s ? frappe.datetime.str_to_user(s) : "");
		const value =
			r.from_date && r.to_date
				? __("{0} to {1}", [fmt(r.from_date), fmt(r.to_date)])
				: fmt(r.from_date);
		this.custom_field.$input.val(value);
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
	// survives refresh/login; category names only — the peak-hour window is
	// always auto-scaled now, no stored hour pick).
	// ------------------------------------------------------------------

	_restore_prefs() {
		const apply = (settings) => {
			const prefs = HQ_UTILS.sanitizePrefs(settings);
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
			category_a: this.category_a,
			category_b: this.category_b,
		});
	}

	refresh() {
		if (!this.$root) return Promise.resolve();
		// Preset ranges are relative to today: recompute on every refresh so a
		// page left open overnight never keeps fetching yesterday's "Today".
		if (this.preset_field && this._preset() !== "custom") {
			this.range = this._preset_range(this._preset());
		}
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
				// First load: default the Top Selling slots to the strongest
				// categories, then fetch once more with those picks. A saved
				// manual pick always wins (and is never overwritten here).
				if (this._autofill_categories()) {
					this.refresh().then(() => resolve());
					return;
				}
				this._sync_filter_options();
				this._render();
				resolve();
			},
			// A server-side throw (e.g. From > To on a custom range) must not
			// leave this promise pending forever — frappe has already shown
			// the msgprint; keep the last rendered state on screen.
			error: () => resolve(),
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

	// First refresh WITH data: an unpicked Top Selling slot defaults to the
	// category with the highest net sales (slot b takes the runner-up, so the
	// pair is an immediate comparison instead of an empty placeholder). A
	// no-sales-yet morning leaves the one-shot unconsumed so the next
	// refresh (e.g. after switching range, or once sales start) fills it.
	// Transient only — preferences are written solely by an explicit pick.
	_autofill_categories() {
		if (this._autofilled) return false;
		const top = ((this.state.category_top || {}).rows || [])
			.filter((r) => r && Number(r.net_amount) > 0)
			.map((r) => r.item_group);
		if (!top.length) return false;
		this._autofilled = true;
		let refetch = false;
		if (!this.category_a && top[0]) {
			this.category_a = top[0];
			refetch = true;
		}
		if (!this.category_b && top[1]) {
			this.category_b = top[1];
			refetch = true;
		}
		return refetch;
	}

	_sync_filter_options() {
		// The Link outlet filter is scoped through get_query (live, reads
		// this.state); here only the subsidiaries toggle depends on scope.
		this.descendants_field.$wrapper.toggle(!!this.company_field.get_value());
	}

	// ------------------------------------------------------------------
	// Rendering — layout v7: scope strip, ONE hero metric block (range
	// figures + the daily-rhythm growth as a footnote), the per-outlet
	// table (6 columns, targets folded into one "monthly progress" cell),
	// one Rankings card with four ranked bar-lists, peak hour and product
	// ranking.
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
			this._hero_card(s),
			this._outlet_performance_card(s),
			this._ranking_card(s),
			this._hours_card(s),
			this._ranking_tables_card(s),
			'<div class="hq-duo">',
			this._shifts_card(s),
			this._returns_card(s),
			"</div>",
			'<div class="hq-duo">',
			this._recent_card(s),
			this._activity_card(s),
			"</div>",
			this._footer(s),
		];
		this.$root.html(parts.join(""));
		this._bind_delegates();
		this._render_charts(s);
		this._apply_outlet_filter();
	}

	_scope_line(scope) {
		const companies = (scope.companies || []).length;
		const label = companies > 3 ? `${companies} ${__("companies")}` : (scope.companies || []).join(", ");
		return `<div class="hq-scope">${__("Scope")}: <b>${frappe.utils.escape_html(label)}</b>
			· ${__("Base currency")}: <b>${scope.default_currency || "-"}</b>
			${(scope.companies || []).length > 1 ? this._tip(__("each company in its own base currency; no cross-currency sum")) : ""}
		</div>`;
	}

	// ------------------------------------------------------------------
	// Target basis: the configured metric behind every target figure
	// (enum + server-translated labels arrive in state.target_basis; the
	// fallbacks keep the page usable on an older payload).
	// ------------------------------------------------------------------

	_basis(which) {
		return ((this.state && this.state.target_basis) || {})[which] || "Net Sales";
	}

	_basis_label(which) {
		const tb = (this.state && this.state.target_basis) || {};
		return tb[`${which}_label`] || __("Net Sales");
	}

	// Gross Profit counts unvalued SLE rows so its figures carry a caveat.
	_zero_cost_tip(basis, rows) {
		if (this._basis(basis) !== "Gross Profit" || !(Number(rows) > 0)) return "";
		return ` ${this._cell_tip(__("{0} rows without cost of goods", [Number(rows)]))}`;
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
	// Quiet explainer: an "i" glyph whose text appears on hover / keyboard
	// focus, replacing the permanent caption lines that used to sit under
	// every figure. The text stays in the DOM (and in the page language).
	// ------------------------------------------------------------------

	_tip(text) {
		const safe = frappe.utils.escape_html(text);
		return `<span class="hq-tip" tabindex="0"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9.2"></circle><line x1="12" y1="10.6" x2="12" y2="16.6"></line><circle cx="12" cy="7.4" r="1.1"></circle></svg><span class="hq-tip-body">${safe}</span></span>`;
	}

	// Table-cell variant: the table's scroll container clips absolutely
	// positioned bubbles, so cells use the native title tooltip on the same
	// glyph instead. Text stays identical to what the card tooltips show.
	_cell_tip(text) {
		const safe = frappe.utils.escape_html(text);
		return `<span class="hq-tip hq-tip--cell" title="${safe}" aria-label="${safe}"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9.2"></circle><line x1="12" y1="10.6" x2="12" y2="16.6"></line><circle cx="12" cy="7.4" r="1.1"></circle></svg></span>`;
	}

	_signed_pct(value) {
		if (value === null || value === undefined) return this._na();
		const n = Number(value);
		return `<span class="hq-money">${n > 0 ? "+" : ""}${HQ_UTILS.fmtPct(n, 1)}</span>`;
	}

	// ------------------------------------------------------------------
	// Hero block — ONE card for the selected range: Total Sales is the
	// dominant figure, the companions (TC / Avg Ticket / Achievement) share
	// a hairline row, and the daily-rhythm growth is a quiet footnote. Every
	// headline metric appears exactly once here; its only second appearance
	// is per-outlet, in the performance table.
	// ------------------------------------------------------------------

	_hero_card(s) {
		const r = s.range || {};
		const t = s.targets || {};
		const ccy = s.scope.default_currency;
		const has = !!t.available;
		const d = s.daily;
		const w = s.windows || {};

		// Daily rhythm: growth vs the SAME weekday last week (the one
		// comparison the range filters cannot align automatically). Growth
		// only — today's absolute figures already live in this card. The
		// footnote stays hidden on a day with nothing to compare yet (no
		// orders today NOR last week same weekday) instead of saying N/A
		// three times.
		const lw = (d && d.last_week_same) || {};
		const orders = (d && d.totals && d.totals.orders) || 0;
		const hasRhythm = !!(d && d.totals && (orders > 0 || lw.orders > 0));
		const tcGrowth = HQ_UTILS.growthPct(orders, lw.orders);
		const apcGrowth = HQ_UTILS.growthPct(
			((d && d.totals && d.totals.apc || {}).by_currency || {})[ccy],
			((lw.apc || {}).by_currency || {})[ccy]
		);
		const rhythm = hasRhythm
				? `<div class="hq-kpi-sub hq-muted" style="margin-top: 8px">
					${__("vs")} ${frappe.utils.escape_html(w.last_week_same || "")} (${__("same weekday last week")}): ${__("Sales")} ${this._signed_pct((d.growth_vs_last_week_pct || {})[ccy])}
					· ${__("TC")} ${this._signed_pct(tcGrowth)} · ${__("Avg Ticket")} ${this._signed_pct(apcGrowth)}
					· ${__("Growth vs prior weekday")} (${frappe.utils.escape_html(w.prior_weekday || "")}): ${this._signed_pct((d.growth_vs_prior_weekday_pct || {})[ccy])}
				</div>`
				: "";

		// Achievement (MTD aggregate) keeps its progress bar in the row. The
		// numerator follows the configured monthly target basis (neutral key
		// mtd_value_by_currency, falling back to net sales on older payloads).
		const mlabel = this._basis_label("monthly");
		const pct = has ? (t.achievement_sales_pct || {})[ccy] : null;
		const target = has ? ((t.target_sales || {}).by_currency || {})[ccy] : null;
		const mtd =
			((t.mtd_value_by_currency || {}).by_currency || {})[ccy] ??
			((s.monthly.net_tax_incl || {}).by_currency || {})[ccy];
		const dailyTarget = has ? (t.daily_target_sales || {})[ccy] : null;
		const achSub = has
			? `${__("MTD")} <span class="hq-money">${HQ_UTILS.fmtMoney(mtd ?? 0, ccy)}</span> / ${__("Target")} <span class="hq-money">${HQ_UTILS.fmtMoney(target ?? 0, ccy)}</span>` +
				(dailyTarget != null ? ` · ± <span class="hq-money">${HQ_UTILS.fmtMoney(dailyTarget, ccy)}</span>/${__("day")}` : "")
			: frappe.utils.escape_html(__(t.notice) || __("Monthly target not set"));

		const slot = (label, value, sub) => `<div class="hq-mini">
			<div class="hq-kpi-label">${label}</div>
			<div class="hq-kpi-value">${value}</div>
			<div class="hq-kpi-sub">${sub}</div>
		</div>`;

		return `<div class="hq-card hq-hero hq-sales-toggle">
			<div class="hq-hero-head">
				<div class="hq-card-title">${__("Total Sales")}
					<button type="button" class="hq-toggle" data-hq-toggle>${__("incl. taxes & charges")}</button></div>
			</div>
			<div class="hq-hero-value hq-sales-incl">${this._metric_text(r.net_tax_incl)}</div>
			<div class="hq-hero-value hq-sales-pretax" hidden>${this._metric_text(r.net_pretax)}</div>
			<div class="hq-kpi-sub">${__("Taxes & charges")}: ${this._metric_text(r.taxes)} · ${__("Refunds")}: ${this._metric_text(r.refunds)}</div>
			<div class="hq-minis">
			${slot(
				__("Total Transactions"),
				HQ_UTILS.fmtCount(r.orders ?? 0),
				`${__("Refund invoices")}: ${HQ_UTILS.fmtCount(r.refund_orders ?? 0)} ${this._tip(__("Pax is not recorded on POS invoices"))}`
			)}
			${slot(
				`${__("Avg per Transaction")} ${this._tip(`${__("Net incl. tax ÷ orders")} · ${__("per currency, never merged")}`)}`,
				this._metric_text(r.apc),
				"&nbsp;"
			)}
			${`<div class="hq-mini">
				<div class="hq-kpi-label">${__("Achievement (MTD)")} · ${frappe.utils.escape_html(mlabel)}</div>
				<div class="hq-kpi-value">${pct == null ? this._na() : HQ_UTILS.fmtPct(pct, 1)}</div>
				${pct == null ? "" : this._bar(pct, "hq-bar--lg")}
				<div class="hq-kpi-sub">${achSub}</div>
			</div>`}
			</div>
			${rhythm}
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
		const rows = ranked
			.concat(quiet)
			.map((r) => ({ ...r, empty: HQ_UTILS.isEmptyOutlet(r) }));
		// Net sales descending — but currencies never merge into one ranking:
		// base-currency rows lead, every other currency follows in its own
		// descending block.
		const ccy = (s.scope || {}).default_currency;
		rows.sort((a, b) => {
			const ac = a.currency === ccy ? 0 : 1;
			const bc = b.currency === ccy ? 0 : 1;
			if (ac !== bc) return ac - bc;
			return (Number(b.net_tax_incl) || 0) - (Number(a.net_tax_incl) || 0);
		});
		return rows;
	}

	_outlet_row(r) {
		const t = r.target || {};
		const o = r.overall;
		const scopeCcy = (this.state.scope || {}).default_currency;
		const ccy = r.currency || scopeCcy;
		const missing = !t || t.missing;
		// Default-currency rows show bare numbers (the scope strip names the
		// currency once); foreign-currency rows keep their code so nothing
		// ever looks merged across currencies.
		const bare = ccy === scopeCcy ? "" : ccy;
		const money = (v) => `<span class="hq-money">${HQ_UTILS.fmtMoney(v, bare)}</span>`;

		// One stacked cell replaces the old target/MTD/achievement trio: the
		// progress bar + bold pct on top, "MTD / Target" as a quiet sub line.
		// Secondary figures (target/MTD transactions) ride one native-title
		// tooltip so the sub stays a single line. Money figures read the
		// basis-neutral keys (mtd_value/target_value/...) with the old
		// net-sales keys as fallback for older payloads.
		const projTip =
			!missing && (t.projected_value ?? t.projected_sales) != null
				? this._cell_tip(`${__("proy.")} ${HQ_UTILS.fmtMoney(t.projected_value ?? t.projected_sales, bare)} · ${HQ_UTILS.fmtPct(t.projected_achievement_pct, 1)}`)
				: "";
		const progressCell = missing
			? `<span class="hq-muted">${__("not set yet")}</span>`
			: `<div class="hq-ach-top">${this._bar(t.achievement_sales_pct, Number(t.achievement_sales_pct) >= 100 ? "hq-bar--ok" : "")}<b class="hq-ach-pct">${HQ_UTILS.fmtPct(t.achievement_sales_pct, 1)}</b>${projTip}</div>
				<div class="hq-ach-sub">${__("MTD")} ${money(t.mtd_value ?? t.mtd_net_tax_incl ?? 0)} / ${__("Target")} ${money(t.target_value ?? t.target_sales)}${this._zero_cost_tip("monthly", t.zero_cost_rows)}</div>`;
		const overallCell = o
			? `${money(o.cumulative_value ?? o.cumulative_net_tax_incl)} <b class="hq-ach-pct">${HQ_UTILS.fmtPct(o.achievement_pct, 1)}</b>${this._cell_tip(`${__("of")} ${HQ_UTILS.fmtMoney(o.overall_target, bare)}${o.from_date ? ` · ${__("since")} ${frappe.utils.escape_html(o.from_date)}` : ""}`)}${this._zero_cost_tip("overall", o.zero_cost_rows)}`
			: this._na();

		return `<tr data-hq-outlet-row${r.empty ? ` data-hq-empty="1" class="hq-row--empty"` : ""} data-hq-company="${frappe.utils.escape_html(r.company)}">
			<td class="hq-outlet-name">${frappe.utils.escape_html(r.company)}</td>
			<td class="hq-num">${money(r.net_tax_incl)}</td>
			<td class="hq-num">${HQ_UTILS.fmtCount(r.orders)}</td>
			<td class="hq-num">${r.apc === null ? "N/A" : money(r.apc)}</td>
			<td class="hq-num hq-ach">${progressCell}</td>
			<td class="hq-num">${overallCell}</td>
		</tr>`;
	}

	_outlet_performance_card(s) {
		const rows = this._outlet_rows(s).map((r) => this._outlet_row(r)).join("");
		// The two target columns name their basis in a native-title tooltip:
		// Monthly Progress follows the monthly basis, Balik Modal the
		// overall (payback) one.
		const monthlyTip = this._cell_tip(__("Monthly basis: {0}", [this._basis_label("monthly")]));
		const overallTip = this._cell_tip(__("Overall (payback) basis: {0}", [this._basis_label("overall")]));
		return `<div class="hq-card hq-card--table hq-card--outlets">
			<div class="hq-card-title">${__("Outlet Performance")}
				${this._tip(__("Outlet = company; balik modal = cumulative sales vs overall target"))}</div>
			<div class="hq-rank-controls">
				<div class="hq-rank-group">
					<div class="hq-search"><input type="search" class="hq-input" data-hq-outlet-search
						placeholder="${__("Search outlet…")}" value="${frappe.utils.escape_html(this.outlet_query)}" aria-label="${__("Search outlet")}"></div>
					<label class="hq-empty-toggle">
						<input type="checkbox" data-hq-show-empty${this.show_empty_outlets ? " checked" : ""}>
						${__("Show empty outlets")}
					</label>
				</div>
				<div class="hq-rank-group">
					<button class="btn btn-xs btn-default" data-hq-goto-targets>${__("Manage Targets")}</button>
					<button class="btn btn-xs btn-default" data-hq-export-xlsx="outlet_perf">${__("Export")}</button>
				</div>
			</div>
			<div class="hq-table-scroll"><table class="hq-table hq-table--outlets">
				<thead><tr>
					<th>${__("Outlet (Company)")}</th>
					<th class="hq-num">${__("Net Sales")}</th>
					<th class="hq-num">${__("TC")}</th>
					<th class="hq-num">${__("Avg Ticket")}</th>
					<th class="hq-num hq-th--progress">${__("Monthly Progress")} ${monthlyTip}</th>
					<th class="hq-num">${__("Balik Modal")} ${overallTip}</th>
				</tr></thead>
				<tbody>${rows || `<tr><td colspan="6" class="hq-muted">${__("No data")}</td></tr>`}</tbody>
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
		const showEmpty = !!this.show_empty_outlets;
		const $rows = this.$root.find("[data-hq-outlet-row]");
		const isEmpty = (_, el) => el.getAttribute("data-hq-empty") === "1";
		const hiddenEmpty = showEmpty ? 0 : $rows.filter(isEmpty).length;
		const matched = $rows.filter((_, el) => {
			if (!showEmpty && isEmpty(_, el)) return false;
			return !q || String($(el).data("hq-company") || "").toLowerCase().includes(q);
		});
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
			`${start} - ${end} ${__("of")} ${total} ${__("outlets")}${hiddenEmpty ? ` · ${HQ_UTILS.fmtCount(hiddenEmpty)} ${__("empty hidden")}` : ""}`
		);
		// A lone page needs no dead Previous/Next buttons.
		this.$root.find('[data-hq-outlet-page="prev"]').toggle(pages > 1).prop("disabled", this.outlet_page <= 1);
		this.$root.find('[data-hq-outlet-page="next"]').toggle(pages > 1).prop("disabled", this.outlet_page >= pages);
	}

	// ------------------------------------------------------------------
	// Peak Hour card (full width) — the axis hugs the hours that actually
	// sold (min-1 .. max+1); with no sales it stays the full day. No manual
	// window: the From/To controls were removed with the per-user hour pick.
	// ------------------------------------------------------------------

	_effective_hour_window(s) {
		const auto = HQ_UTILS.autoHourWindow((s.hours || {}).rows);
		if (auto) return { win: auto, auto: true };
		return { win: { start: 0, end: 24, overnight: false }, auto: false };
	}

	_hours_card(s) {
		const { win, auto } = this._effective_hour_window(s);
		const bins = HQ_UTILS.hourWindowBins((s.hours || {}).rows, win.start, win.end);
		const stats = HQ_UTILS.windowStats(bins);
		const ccy = s.scope.default_currency;
		const peak = stats.peak
			? `${__("Peak")} <b>${HQ_UTILS.hourLabel(stats.peak.hour)}</b> (<span class="hq-money">${HQ_UTILS.fmtMoney(stats.peak.net_sales, ccy)}</span>, ${HQ_UTILS.fmtCount(stats.peak.orders)} ${__("orders")})`
			: __("No data");
		const winLabel = `${HQ_UTILS.hourLabel(win.start)} – ${HQ_UTILS.hourLabel(win.end)}${win.overnight ? ` · ${__("overnight")}` : ""}`;
		return `<div class="hq-card hq-kpi hq-card--hours">
			<div class="hq-kpi-label">${__("Peak Hour")}
				<span class="hq-period">${auto ? `${__("auto")} · ` : ""}${winLabel}</span>
				${auto ? this._tip(__("Auto-scaled to hours with sales")) : ""}</div>
			<div class="hq-kpi-sub">${peak}</div>
			<div class="hq-hour-scroll"><div class="hq-hour-chart"></div></div>
		</div>`;
	}

	// ------------------------------------------------------------------
	// Rankings — ONE card, four ranked bar-lists in a 2x2 internal grid:
	// the two independent "Top Selling <category>" slots (a / b), the top
	// categories and the top outlets. Bars beat rings for 1-5 entries (a
	// lone leader is a full bar, never a "100%" donut) and drop four
	// frappe.Chart instances from the page.
	// ------------------------------------------------------------------

	_ranking_card(s) {
		const blocks = [
			this._rank_category_block(s, "a"),
			this._rank_category_block(s, "b"),
			this._rank_payments_block(s),
			this._rank_outlets_block(s),
		];
		return `<div class="hq-card hq-card--rankings">
			<div class="hq-card-title">${__("Rankings")}</div>
			<div class="hq-rank-grid">${blocks.join("")}</div>
		</div>`;
	}

	// One ranked bar-list: rank number, label (full text in the title
	// tooltip), value, share %, and a thin bar proportional to the list max.
	_rank_list_html(entries, ccy) {
		const max = Math.max(...entries.map((e) => e.value), 0);
		return `<ol class="hq-rank-list">${entries
			.map(
				(e, i) => `<li class="hq-rank-item">
				<div class="hq-rank-row">
					<span class="hq-rank-no">${i + 1}</span>
					<span class="hq-rank-label" title="${frappe.utils.escape_html(e.label)}">${frappe.utils.escape_html(e.label)}</span>
					<span class="hq-rank-value hq-money">${HQ_UTILS.fmtMoney(e.value, ccy)}</span>
					<span class="hq-rank-pct">${HQ_UTILS.fmtPct(e.share_pct)}</span>
				</div>
				<div class="hq-rank-track"><div class="hq-rank-bar" style="width:${max > 0 ? Math.max(2, (e.value / max) * 100) : 0}%"></div></div>
			</li>`
			)
			.join("")}</ol>`;
	}

	// The two independent category slots (a / b). Slots are never shown to
	// the user (the dropdown selection IS the heading); only the accessible
	// labels say first/second category.
	_rank_category_block(s, slot) {
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
		let body;
		if (cp.invalid) {
			body = this._empty_line(__("Category no longer exists, please choose another"));
		} else if (!sel) {
			body = this._empty_line(__("Choose a category to see its top selling items"));
		} else {
			const entries = (cp.items || [])
				.filter((r) => Number(r.net_amount) > 0)
				.map((r) => ({ label: r.item_name || r.item_code || "", value: Number(r.net_amount), share_pct: r.share_pct }));
			if (!entries.length) {
				body = this._empty_line(__("No positive sales in this category in this period"));
			} else {
				const of =
					entries.length < cp.items_with_sales
						? ` · ${__("top 5 of")} ${cp.items_with_sales} ${__("items")}`
						: "";
				body = `${this._rank_list_html(entries, ccy)}
					<div class="hq-rank-foot hq-muted">${__("Share of category net revenue")} <span class="hq-money">${HQ_UTILS.fmtMoney(cp.category_total, ccy)}</span>${of}</div>`;
			}
		}
		return `<div class="hq-rank-block">
			<div class="hq-rank-head">${__("Top Selling Items")} ${select}</div>
			${body}
		</div>`;
	}

	_rank_payments_block(s) {
		const pm = s.payments || {};
		const ccy = pm.currency || s.scope.default_currency;
		const entries = (pm.rows || []).map((r) => ({
			label: r.mode_of_payment,
			value: Number(r.amount),
			share_pct: r.share_pct,
		}));
		const foot =
			entries.length && entries.length < (pm.modes_with_sales || entries.length)
				? `<div class="hq-rank-foot hq-muted">${__("top 5 of")} ${pm.modes_with_sales} ${__("payment methods")}</div>`
				: "";
		const body = entries.length
			? `${this._rank_list_html(entries, ccy)}${foot}`
			: this._empty_line(__("No positive sales in this period"));
		return `<div class="hq-rank-block">
			<div class="hq-rank-head">${__("Payment Methods")}</div>
			${body}
		</div>`;
	}

	_rank_outlets_block(s) {
		const ccy = s.scope.default_currency;
		const entries = (s.outlet_ranking || [])
			.filter((r) => r.currency === ccy && Number(r.net_tax_incl) > 0)
			.slice(0, 5)
			.map((r) => ({ label: r.company, value: Number(r.net_tax_incl), share_pct: r.share_pct }));
		const body = entries.length
			? this._rank_list_html(entries, ccy)
			: this._empty_line(__("No positive sales in this period"));
		return `<div class="hq-rank-block">
			<div class="hq-rank-head">${__("Top Outlets")} <span class="hq-rank-head-sub">${__("net incl. tax")} · ${frappe.utils.escape_html(ccy || "")}</span></div>
			${body}
		</div>`;
	}

	// Compact empty state: one quiet line — an empty block must not carry the
	// height of a full one.
	_empty_line(msg) {
		return `<p class="hq-empty-line">${frappe.utils.escape_html(msg)}</p>`;
	}

	// ------------------------------------------------------------------
	// Product + Outlet Ranking — one card, two tables side by side with
	// their tables top-aligned: each block head is a single row carrying
	// its tool on the right (category select on the product side, the Excel
	// Export button on the outlet side).
	// ------------------------------------------------------------------

	_ranking_tables_card(s) {
		const pr = s.product_ranking || {};
		const options = [{ label: __("All Categories"), value: "" }].concat(
			(pr.categories || []).map((c) => ({ label: c, value: c }))
		);
		const select = `<select class="hq-select" data-hq-category aria-label="${__("Filter by category")}">
			${options.map((o) => `<option value="${frappe.utils.escape_html(o.value)}"${(o.value || "") === (pr.category || "") ? " selected" : ""}>${frappe.utils.escape_html(o.label)}</option>`).join("")}
		</select>`;
		return `<div class="hq-card hq-card--tables">
			<div class="hq-rank-grid">
				<div class="hq-rank-block">
					<div class="hq-rank-head hq-rank-head--tools">${__("Product Ranking")} ${select}
						<button class="btn btn-xs btn-default hq-rank-export" data-hq-export-xlsx="products">${__("Export")}</button></div>
					${this._product_table(pr)}
					${this._product_pager(pr)}
				</div>
				<div class="hq-rank-block">
					<div class="hq-rank-head hq-rank-head--tools">${__("Outlet Ranking")}
						<button class="btn btn-xs btn-default hq-rank-export" data-hq-export-xlsx="outlets">${__("Export")}</button></div>
					${this._outlet_rank_table(s)}
				</div>
			</div>
		</div>`;
	}

	// ------------------------------------------------------------------
	// Bottom monitors — shift recap & returns side by side, then the recent
	// transactions table with the activity feed (derived client-side from
	// the same recent rows — no extra request) as the page's last row.
	// ------------------------------------------------------------------

	_shifts_card(s) {
		const scopeCcy = (s.scope || {}).default_currency;
		const rows = (s.shifts || [])
			.map((r) => {
				const bare = r.currency && r.currency !== scopeCcy ? r.currency : "";
				const money = (v) => `<span class="hq-money">${HQ_UTILS.fmtMoney(v, bare)}</span>`;
				const open = r.status === "Open";
				return `<tr>
				<td><div class="hq-shift-cashier">${frappe.utils.escape_html(r.cashier_name || r.cashier)}</div>
					<div class="hq-item-code">${frappe.utils.escape_html(r.outlet)}</div></td>
				<td class="hq-num">${money(r.opening)}</td>
				<td class="hq-num">${money(r.sales)}</td>
				<td class="hq-num">${money(r.cash)}</td>
				<td class="hq-num"><b>${money(r.expected_closing)}</b>
					<div class="hq-item-code hq-status${open ? " hq-status--open" : ""}">${frappe.utils.escape_html(r.status)}</div></td>
			</tr>`;
			})
			.join("");
		return `<div class="hq-card hq-card--table hq-card--shifts">
			<div class="hq-card-title">${__("Shift Summary")}</div>
			<div class="hq-table-scroll"><table class="hq-table">
				<thead><tr><th>${__("Cashier / Outlet")}</th><th class="hq-num">${__("Opening Balance")}</th>
				<th class="hq-num">${__("Sales")}</th><th class="hq-num">${__("Cash")}</th>
				<th class="hq-num">${__("Expected Closing")}</th></tr></thead>
				<tbody>${rows || `<tr><td colspan="5" class="hq-muted">${__("No data")}</td></tr>`}</tbody>
			</table></div>
			<div class="hq-kpi-sub hq-muted">${__("Expected closing = opening balance + cash sales - cash returns - change")}</div>
		</div>`;
	}

	_returns_card(s) {
		const ret = s.returns || {};
		const scopeCcy = (s.scope || {}).default_currency;
		const ccy = ret.currency || scopeCcy;
		const tile = (label, value) =>
			`<div class="hq-ret-kpi"><div class="hq-kpi-label">${label}</div><div class="hq-kpi-value hq-money">${value}</div></div>`;
		const tiles = `<div class="hq-ret-kpis">
			${tile(__("Return Value"), `<span class="hq-neg">${HQ_UTILS.fmtMoney(ret.value || 0, ccy)}</span>`)}
			${tile(__("Return Qty"), HQ_UTILS.fmtCount(ret.count || 0))}
			${tile(__("Return Rate"), HQ_UTILS.fmtPct(ret.rate == null ? null : ret.rate * 100, 1))}
		</div>`;
		const rows = (ret.rows || [])
			.map(
				(r) => `<div class="hq-ret-row">
				<div class="hq-ret-name"><b>${frappe.utils.escape_html(r.name)}</b>
					<div class="hq-item-code">${frappe.utils.escape_html(r.first_item || "")}</div></div>
				<span class="hq-money hq-neg">-${HQ_UTILS.fmtMoney(r.amount, r.currency === ccy ? "" : r.currency)}</span>
			</div>`
			)
			.join("");
		return `<div class="hq-card hq-card--returns">
			<div class="hq-card-title">${__("Returns")}</div>
			${tiles}
			<div class="hq-ret-rows">${rows || `<p class="hq-empty-line">${__("No data")}</p>`}</div>
		</div>`;
	}

	_recent_card(s) {
		const scopeCcy = (s.scope || {}).default_currency;
		const rows = ((s.recent || {}).rows || [])
			.map((r) => {
				const bare = r.currency && r.currency !== scopeCcy ? r.currency : "";
				const money = `<span class="hq-money${r.is_return ? " hq-neg" : ""}">${
					r.is_return ? "-" : ""
				}${HQ_UTILS.fmtMoney(Math.abs(Number(r.grand_total) || 0), bare)}</span>`;
				return `<tr>
				<td><b>${frappe.utils.escape_html(r.name)}</b></td>
				<td class="hq-num">${frappe.utils.escape_html(r.time)}</td>
				<td>${frappe.utils.escape_html(r.company)}</td>
				<td>${frappe.utils.escape_html(r.customer)}</td>
				<td>${frappe.utils.escape_html(r.mode_of_payment || "N/A")}</td>
				<td class="hq-num">${money}</td>
			</tr>`;
			})
			.join("");
		return `<div class="hq-card hq-card--table hq-card--recent">
			<div class="hq-card-title">${__("Recent Transactions")}</div>
			<div class="hq-table-scroll"><table class="hq-table">
				<thead><tr><th>${__("Invoice")}</th><th class="hq-num">${__("Time")}</th>
				<th>${__("Outlet")}</th><th>${__("Customer")}</th><th>${__("Payment")}</th>
				<th class="hq-num">${__("Amount")}</th></tr></thead>
				<tbody>${rows || `<tr><td colspan="6" class="hq-muted">${__("No data")}</td></tr>`}</tbody>
			</table></div>
		</div>`;
	}

	_activity_card(s) {
		const rows = ((s.recent || {}).rows || []).map((r) => {
			const ret = !!r.is_return;
			const bare = r.currency && r.currency !== (s.scope || {}).default_currency ? r.currency : "";
			const money = `<span class="hq-money${ret ? " hq-neg" : ""}">${ret ? "-" : ""}${HQ_UTILS.fmtMoney(Math.abs(Number(r.grand_total) || 0), bare)}</span>`;
			return `<div class="hq-act-row">
				<div class="hq-act-body">
					<div class="hq-act-title">${ret ? __("Return processed") : __("Sale completed")}</div>
					<div class="hq-item-code">${frappe.utils.escape_html(r.company)} · ${frappe.utils.escape_html(r.time)}</div>
					<div class="hq-item-code">${frappe.utils.escape_html(r.name)}</div>
				</div>
				${money}
			</div>`;
		});
		const generated = ((s.generated_at || "").split(" ")[1] || "").slice(0, 5);
		return `<div class="hq-card hq-card--activity">
			<div class="hq-card-title">${__("Live Activity")}
				${generated ? `<span class="hq-period">${__("per")} ${frappe.utils.escape_html(generated)}</span>` : ""}</div>
			<div class="hq-act-rows">${rows.join("") || `<p class="hq-empty-line">${__("No data")}</p>`}</div>
		</div>`;
	}

	_product_table(pr) {
		const rows = (pr.rows || [])
			.map(
				(r, i) => `<tr>
				<td class="hq-num">${(pr.page - 1) * pr.page_size + i + 1}</td>
				<td>${frappe.utils.escape_html(r.item_name)}
					${r.item_code ? `<div class="hq-item-code">${frappe.utils.escape_html(r.item_code)}</div>` : ""}</td>
				<td class="hq-num">${HQ_UTILS.fmtCount(r.qty)}</td>
				<td class="hq-num"><span class="hq-money">${HQ_UTILS.fmtMoney(r.net_amount, "")}</span></td>
				<td class="hq-num">${HQ_UTILS.fmtPct(r.share_pct)}</td>
			</tr>`
			)
			.join("");
		return `<div class="hq-table-scroll"><table class="hq-table">
			<thead><tr><th class="hq-num">${__("No")}</th><th>${__("Name")}</th>
			<th class="hq-num">${__("Sold Quantity")}</th><th class="hq-num">${__("Total Sales")}</th><th class="hq-num">%</th></tr></thead>
			<tbody>${rows || `<tr><td colspan="5" class="hq-muted">${__("No data")}</td></tr>`}</tbody>
		</table></div>`;
	}

	_outlet_rank_table(s) {
		const ccy = s.scope.default_currency;
		const rows = (s.outlet_ranking || [])
			.map((r, i) => {
				// Base-currency rows show bare numbers; foreign-currency rows
				// keep their code so nothing ever looks merged (same rule as
				// the Outlet Performance table).
				const bare = r.currency === ccy ? "" : r.currency;
				const money = (v) =>
					v === null || v === undefined
						? "N/A"
						: `<span class="hq-money">${HQ_UTILS.fmtMoney(v, bare)}</span>`;
				return `<tr>
				<td class="hq-num">${i + 1}</td>
				<td>${frappe.utils.escape_html(r.company)}</td>
				<td class="hq-num">${money(r.net_tax_incl)}</td>
				<td class="hq-num">${HQ_UTILS.fmtCount(r.orders)}</td>
				<td class="hq-num">${r.apc === null || r.apc === undefined ? "N/A" : money(r.apc)}</td>
				<td class="hq-num">${HQ_UTILS.fmtPct(r.share_pct)}</td>
			</tr>`;
			})
			.join("");
		return `<div class="hq-table-scroll"><table class="hq-table hq-table--outlet-rank">
			<thead><tr><th class="hq-num">${__("No")}</th><th>${__("Name")}</th>
			<th class="hq-num">${__("Total Sales")}</th><th class="hq-num">${__("Transactions")}</th>
			<th class="hq-num">${__("Average")}</th><th class="hq-num">%</th></tr></thead>
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
		// The rankings are DOM bar-lists now; the peak-hour chart is the
		// page's only frappe.Chart instance left.
		this._render_hour_chart(s);
	}

	// Every remaining chart routes through the hardened draw wrapper; the
	// bar-list rankings need no chart instance at all.
	_render_hour_chart(s) {
		const el = this.$root.find(".hq-hour-chart").get(0);
		if (!el || !frappe.Chart) return;
		// Only the selected window is drawn; hours without sales stay explicit
		// zero bins so every bar maps to its hour. An untouched full-day
		// setting auto-scales to the hours that actually sold.
		const { win } = this._effective_hour_window(s);
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
			.off("change", "[data-hq-show-empty]")
			.on("change", "[data-hq-show-empty]", (e) => {
				this.show_empty_outlets = e.currentTarget.checked;
				this.outlet_page = 1;
				this._apply_outlet_filter();
			})
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
			})
			.off("click", "[data-hq-export-xlsx]")
			.on("click", "[data-hq-export-xlsx]", (e) => {
				this._export_xlsx($(e.currentTarget).data("hq-export-xlsx"));
			});
	}

	// Excel export through Frappe's own xlsx builder: one endpoint, three
	// sections — the kind picks which rows the client sends.
	_export_xlsx(kind) {
		const s = this.state;
		if (!s) return;
		const params = new URLSearchParams({
			from_date: (s.scope || {}).from_date || "",
			to_date: (s.scope || {}).to_date || "",
		});
		if (kind === "products") {
			const pr = s.product_ranking || {};
			params.set(
				"products",
				JSON.stringify(
					(pr.rows || []).map((r, i) => [
						(pr.page - 1) * pr.page_size + i + 1,
						r.item_name,
						r.qty,
						r.net_amount,
						r.share_pct,
					])
				)
			);
		} else if (kind === "outlet_perf") {
			params.set("outlet_performance", JSON.stringify(this._outlet_export_rows().rows));
		} else {
			params.set(
				"outlets",
				JSON.stringify(
					(s.outlet_ranking || []).map((r, i) => [
						i + 1,
						r.company,
						r.net_tax_incl,
						r.orders,
						r.apc,
						r.share_pct,
					])
				)
			);
		}
		window.location.href = `/api/method/pos_next.api.hq_monitoring.export_rankings_xlsx?${params}`;
	}

	// The Outlet Performance export keeps the machine-readable full schema
	// the old CSV had (target basis labels included), just as xlsx now.
	_outlet_export_rows() {
		const s = this.state;
		const mlabel = this._basis_label("monthly");
		const olabel = this._basis_label("overall");
		return {
			headers: [
				__("Outlet (Company)"),
				__("POS Profiles"),
				__("Currency"),
				__("Net Sales"),
				__("Transactions"),
				__("Avg Ticket"),
				__("Share %"),
				`${__("Target")} ${mlabel} (${__("monthly")})`,
				__("Target Transactions"),
				`${mlabel} ${__("MTD")}`,
				__("MTD Transactions"),
				__("Achievement %"),
				`${__("Projected")} ${mlabel}`,
				`${__("Overall Target")} (${olabel})`,
				`${olabel} ${__("Cumulative")}`,
				__("Overall Achievement %"),
			],
			rows: this._outlet_rows(s).map((r) => [
				r.company,
				(r.profiles || []).map((p) => p.pos_profile).join("; "),
				r.currency,
				r.net_tax_incl,
				r.orders,
				r.apc,
				r.share_pct,
				r.target && !r.target.missing ? (r.target.target_value ?? r.target.target_sales) : "",
				r.target && !r.target.missing ? r.target.target_transactions : "",
				r.target ? (r.target.mtd_value ?? r.target.mtd_net_tax_incl) : "",
				r.target ? r.target.mtd_orders : "",
				r.target && !r.target.missing ? r.target.achievement_sales_pct : "",
				r.target ? (r.target.projected_value ?? r.target.projected_sales) : "",
				r.overall ? r.overall.overall_target : "",
				r.overall ? (r.overall.cumulative_value ?? r.overall.cumulative_net_tax_incl) : "",
				r.overall ? r.overall.achievement_pct : "",
			]),
		};
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
		const out = this._outlet_export_rows();
		sections.push(HQ_UTILS.toCsv(out.headers, out.rows));
	}
		const blob = new Blob(["\ufeff" + sections.join("\n")], { type: "text/csv;charset=utf-8;" });
		const a = document.createElement("a");
		a.href = URL.createObjectURL(blob);
		a.download = `hq-sales-monitoring-${s.scope.to_date}.csv`;
		a.click();
		URL.revokeObjectURL(a.href);
	}
}
