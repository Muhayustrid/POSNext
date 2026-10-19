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
// target per outlet, with the standard Frappe filter bar, server-side
// pagination and Excel export/import.
class OutletTargetsPage {
	constructor(page) {
		this.page = page;
		this.state = null;
		this.start = 0;
		this.page_length = 20;
		this.datatable = null;
		this._setup_actions();
		this._setup_filters();
		this.$root = $('<div class="ot-page">').appendTo(page.main);
		this.$root.html(`<p class="text-muted">${__("Loading...")}</p>`);
		this.refresh();
	}

	_setup_actions() {
		this.page.add_button(__("Refresh"), () => this.refresh());
		this.page.add_menu_item(__("Export Excel"), () => this._export_excel());
		this.page.add_menu_item(__("Import Excel"), () => this._import_dialog());
		this.page.add_menu_item(__("Download Template"), () => this._export_excel(true));
		this.page.add_menu_item(__("Copy Targets from Previous Month"), () => {
			const monthLabel = moment(this._month_start()).format("MMMM YYYY");
			frappe.confirm(
				__("Copy last month's targets into {0} for outlets without a target?", [monthLabel]),
				() => {
					frappe.call({
						method: "pos_next.api.hq_monitoring.copy_outlet_targets",
						args: { month_start: this._month_start() },
						freeze: true,
						callback: (r) => {
							const out = r.message || { copied: 0, skipped: 0 };
							frappe.msgprint(__("Copied: {0} · Skipped: {1}", [out.copied || 0, out.skipped || 0]));
							this.refresh();
						},
					});
				}
			);
		});
		this.page.add_menu_item(__("Target History"), () => {
			frappe.set_route("List", "POS Monthly Target");
		});
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
			change: this._later(() => {
				this.start = 0;
				this.refresh();
			}),
		});
		this.year_field = this.page.add_field({
			fieldname: "year",
			label: __("Year"),
			fieldtype: "Select",
			options: yearOpts,
			default: String(year),
			change: this._later(() => {
				this.start = 0;
				this.refresh();
			}),
		});
		this.outlet_field = this.page.add_field({
			fieldname: "outlet",
			label: __("Outlet"),
			fieldtype: "Data",
			placeholder: __("Search outlet"),
			change: frappe.utils.debounce(
				this._later(() => {
					this.start = 0;
					this.refresh();
				}),
				300
			),
		});
	}

	// The API receives exactly this key: year + "-" + month + "-01".
	_month_start() {
		return `${this.year_field.get_value()}-${this.month_field.get_value()}-01`;
	}

	_search() {
		return (this.outlet_field.get_value() || "").trim();
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
				args: {
					month_start: this._month_start(),
					search: this._search(),
					start: this.start,
					page_length: this.page_length,
				},
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
		if (this.datatable) {
			this.datatable.destroy();
			this.datatable = null;
		}
		let msg = __("Failed to load targets");
		const exc = r && r.exc ? String(r.exc).trim().split("\n").filter(Boolean) : [];
		if (exc.length) msg = exc[exc.length - 1];
		this.$root.html(
			`<div class="frappe-card"><p class="text-muted">${frappe.utils.escape_html(msg)}</p></div>`
		);
	}

	// ------------------------------------------------------------------
	// Rendering — native Frappe DataTable with inline autosave
	// ------------------------------------------------------------------

	_render() {
		const rows = this.state.rows || [];
		const total = this.state.total_count || 0;
		if (!rows.length) {
			if (this.datatable) {
				this.datatable.destroy();
				this.datatable = null;
			}
			this.$root.html(
				`<div class="frappe-card"><p class="text-muted">${__("No data")}</p></div>`
			);
			return;
		}

		if (this.datatable) {
			this.datatable.destroy();
			this.datatable = null;
		}
		const mlabel = frappe.utils.escape_html(this._basis_label("monthly"));
		this.$root.html([
			`<div class="frappe-card"><div class="ot-datatable-wrapper"></div></div>`,
			this._pager(total),
			`<div class="ot-sub text-muted">${__("Month")}: ${frappe.utils.escape_html(this.state.month_start)}
				· ${__("days elapsed")}: ${HQ_UTILS.fmtCount(this.state.days_elapsed ?? 0)}
				· ${__("Generated")}: ${frappe.utils.escape_html(this.state.generated_at)}</div>`,
		].join(""));
		this._bind_pager();

		const $wrapper = this.$root.find(".ot-datatable-wrapper");
		const columns = this._get_columns(mlabel);
		this.datatable = new frappe.DataTable($wrapper[0], {
			columns: columns,
			data: rows.map((r) => this._row_object(r)),
			serialNoColumn: false,
			checkboxColumn: false,
			inlineFilters: false,
			headerDropdown: [],
			layout: "fluid",
			cellHeight: 35,
			language: frappe.boot.lang,
			translations: frappe.utils.datatable.get_translations(),
			direction: frappe.utils.is_rtl() ? "rtl" : "ltr",
			getEditor: this._get_editor.bind(this),
		});
	}

	_get_columns(mlabel) {
		const canWriteMonthly =
			frappe.model.can_write("POS Monthly Target") ||
			frappe.model.can_create("POS Monthly Target");
		const canWriteCompany = frappe.model.can_write("Company");

		return [
			{
				id: "company",
				name: __("Outlet"),
				editable: false,
				sortable: false,
				dropdown: false,
				focusable: false,
				format: (value) =>
					`<a href="/desk/pos-monthly-target?company=${encodeURIComponent(value)}">${frappe.utils.escape_html(value)}</a>`,
			},
			{
				id: "target_sales",
				name: `${__("Target")} ${mlabel} (${__("monthly")})`,
				align: "right",
				editable: canWriteMonthly,
				focusable: canWriteMonthly,
				sortable: false,
				dropdown: false,
				docfield: {
					fieldname: "target_sales",
					fieldtype: "Currency",
					label: `${__("Target")} ${mlabel} (${__("monthly")})`,
				},
				format: (value, row, column, data) => {
					const ccy = (data && data.currency) || (row && row.currency) || "";
					return value != null && value !== "" ? format_currency(value, ccy) : "";
				},
			},
			{
				id: "mtd_value",
				name: `${mlabel} ${__("MTD")}`,
				align: "right",
				editable: false,
				sortable: false,
				dropdown: false,
				focusable: false,
				format: (value, row, column, data) => {
					const ccy = (data && data.currency) || (row && row.currency) || "";
					return value != null && value !== "" ? format_currency(value, ccy) : "";
				},
			},
			{
				id: "achievement",
				name: __("Achievement"),
				align: "right",
				editable: false,
				sortable: false,
				dropdown: false,
				focusable: false,
				format: (value) => {
					if (value == null || value === "") return "";
					const num = Number(value);
					if (isNaN(num)) return "";
					const color = num < 50 ? "red" : num < 100 ? "orange" : "green";
					return `<span class="indicator-pill ${color}">${HQ_UTILS.fmtPct(num, 1)}</span>`;
				},
			},
			{
				id: "projection",
				name: __("Projection"),
				align: "right",
				editable: false,
				sortable: false,
				dropdown: false,
				focusable: false,
				format: (value, row, column, data) => {
					const ccy = (data && data.currency) || (row && row.currency) || "";
					return value != null && value !== "" ? format_currency(value, ccy) : "";
				},
			},
			{
				id: "overall_target",
				name: __("Balik Modal Target"),
				align: "right",
				editable: canWriteCompany,
				focusable: canWriteCompany,
				sortable: false,
				dropdown: false,
				docfield: {
					fieldname: "overall_target",
					fieldtype: "Currency",
					label: __("Balik Modal Target"),
				},
				format: (value, row, column, data) => {
					const ccy = (data && data.currency) || (row && row.currency) || "";
					return value != null && value !== "" ? format_currency(value, ccy) : "";
				},
			},
			{
				id: "overall_from",
				name: __("Balik Modal From"),
				editable: canWriteCompany,
				focusable: canWriteCompany,
				sortable: false,
				dropdown: false,
				docfield: {
					fieldname: "overall_from",
					fieldtype: "Date",
					label: __("Balik Modal From"),
				},
				format: (value) => (value ? frappe.datetime.str_to_user(value) : ""),
			},
			{
				id: "overall_pct",
				name: __("Balik Modal %"),
				align: "right",
				editable: false,
				sortable: false,
				dropdown: false,
				focusable: false,
				format: (value) => {
					if (value == null || value === "") return "";
					const num = Number(value);
					if (isNaN(num)) return "";
					const color = num < 50 ? "red" : num < 100 ? "orange" : "green";
					return `<span class="indicator-pill ${color}">${HQ_UTILS.fmtPct(num, 1)}</span>`;
				},
			},
		];
	}

	_row_object(r) {
		const m = r.monthly || {};
		const o = r.overall;
		const missing = !!m.missing;
		return {
			company: r.company,
			target_sales: missing ? null : (m.target_sales ?? null),
			mtd_value: r.mtd_value ?? r.mtd_net_tax_incl ?? 0,
			achievement: missing ? null : (r.achievement_sales_pct ?? null),
			projection: missing ? null : (r.projected_value ?? r.projected_sales ?? null),
			overall_target: (o && o.overall_target != null) ? o.overall_target : null,
			overall_from: (o && o.from_date) ? o.from_date : "",
			overall_pct: (o && o.achievement_pct != null) ? o.achievement_pct : null,
			currency: r.currency || "",
		};
	}

	_row_values(r) {
		const obj = this._row_object(r);
		return [
			obj.company,
			obj.target_sales,
			obj.mtd_value,
			obj.achievement,
			obj.projection,
			obj.overall_target,
			obj.overall_from,
			obj.overall_pct,
		];
	}

	_get_editor(colIndex, rowIndex, value, parent) {
		const col = this.datatable.getColumn(colIndex);
		if (!col || !col.editable || !col.docfield) return false;

		const rowData = (this.state && this.state.rows && this.state.rows[rowIndex]) || {};
		const currency = rowData.currency || frappe.boot.sysdefaults.currency;

		const df = Object.assign({}, col.docfield, {
			options: col.docfield.fieldtype === "Currency" ? currency : col.docfield.options,
		});

		const control = frappe.ui.form.make_control({
			df: df,
			parent: parent,
			render_input: true,
		});
		if (!control) return false;

		control.set_value(value);
		control.toggle_label(false);
		control.toggle_description(false);
		if (control.df) {
			control.df.change = () => control.set_focus && control.set_focus();
		}

		return {
			initValue: (val) => {
				const res = control.set_value(val);
				if (control.set_focus) {
					control.set_focus();
				}
				return res;
			},
			setValue: (val) => {
				control.set_value(val);
				const current = (this.state && this.state.rows && this.state.rows[rowIndex]) || rowData;
				const company = current.company || rowData.company;
				const fieldname = col.docfield.fieldname;
				const args = { company };

				if (fieldname === "target_sales") {
					args.month_start = this._month_start();
					args.target_sales = val || 0;
				} else if (fieldname === "overall_target") {
					args.overall_target = val || 0;
					args.overall_from = (current.overall && current.overall.from_date) || "";
				} else if (fieldname === "overall_from") {
					args.overall_target = (current.overall && current.overall.overall_target) || 0;
					args.overall_from = val || "";
				}

				return new Promise((resolve, reject) => {
					frappe.call({
						method: "pos_next.api.hq_monitoring.set_outlet_target",
						args: args,
						callback: (r) => {
							if (r && r.exc) {
								reject(r.exc);
							} else {
								frappe.show_alert({ message: __("Saved"), indicator: "green" });
								this._refresh_row(rowIndex, company);
								resolve(val);
							}
						},
						error: (err) => {
							reject(err);
						},
					});
				});
			},
			getValue: () => {
				let val = control.get_value();
				if (col.docfield && col.docfield.fieldtype === "Date") {
					val = val || "";
				} else if (val === "" || val === undefined) {
					// blank Currency must equal the row's null so an unedited cell skips the save
					val = null;
				}
				return Promise.resolve(val);
			},
		};
	}

	_refresh_row(rowIndex, company) {
		frappe.call({
			method: "pos_next.api.hq_monitoring.get_outlet_targets",
			args: {
				month_start: this._month_start(),
				company: company,
				start: 0,
				page_length: 1,
			},
			callback: (r) => {
				if (r.message && r.message.rows && r.message.rows.length) {
					const row = r.message.rows[0];
					if (this.state && this.state.rows) {
						this.state.rows[rowIndex] = row;
					}
					if (this.datatable) {
						if (this.datatable.datamanager && this.datatable.datamanager.data) {
							this.datatable.datamanager.data[rowIndex] = this._row_object(row);
						}
						this.datatable.refreshRow(this._row_values(row), rowIndex);
					}
				}
			},
		});
	}

	// Frappe list-view-style pager: page-size buttons + Prev/Next + count.
	_pager(total) {
		const from = this.start + 1;
		const to = Math.min(this.start + this.page_length, total);
		const sizes = [20, 100, 500];
		const sizeBtns = sizes
			.map(
				(n) =>
					`<button class="btn btn-xs btn-default${n === this.page_length ? " btn-primary" : ""}" data-ot-size="${n}">${n}</button>`
			)
			.join("");
		return `<div class="ot-pager">
			<span class="ot-pager-sizes">${sizeBtns}</span>
			<span class="ot-pager-nav">
				<button class="btn btn-xs btn-default" data-ot-prev ${this.start <= 0 ? "disabled" : ""}>${__("Previous")}</button>
				<button class="btn btn-xs btn-default" data-ot-next ${to >= total ? "disabled" : ""}>${__("Next")}</button>
			</span>
			<span class="ot-sub text-muted">${from}–${to} ${__("of {0}", [total])}</span>
		</div>`;
	}

	_bind_pager() {
		this.$root
			.off("click", "[data-ot-size]")
			.on("click", "[data-ot-size]", (e) => {
				this.page_length = Number($(e.currentTarget).data("ot-size"));
				this.start = 0;
				this.refresh();
			})
			.off("click", "[data-ot-prev]")
			.on("click", "[data-ot-prev]", () => {
				this.start = Math.max(0, this.start - this.page_length);
				this.refresh();
			})
			.off("click", "[data-ot-next]")
			.on("click", "[data-ot-next]", () => {
				this.start = this.start + this.page_length;
				this.refresh();
			});
	}

	// ------------------------------------------------------------------
	// Excel export / import
	// ------------------------------------------------------------------

	_export_excel(template) {
		const params = new URLSearchParams({
			month_start: this._month_start(),
		});
		const search = this._search();
		if (search) params.set("search", search);
		if (!template && this.state && this.state.total_count <= this.page_length) {
			params.set("page_length", String(this.state.total_count));
		}
		window.open(
			frappe.urllib.get_full_url(`/api/method/pos_next.api.outlet_targets.export_outlet_targets?${params}`)
		);
	}

	_import_dialog() {
		const d = new frappe.ui.Dialog({
			title: __("Import Excel"),
			fields: [
				{
					fieldname: "attach",
					label: __("Excel File"),
					fieldtype: "Attach",
					options: { restrictions: { allowed_file_types: [".xlsx"], max_file_size: 5 * 1024 * 1024 } },
				},
				{
					fieldname: "help",
					fieldtype: "HTML",
					options: `<p class="text-muted ot-sub">${frappe.utils.escape_html(
						__("Use the Export Excel sheet layout. Only Monthly Target, Balik Modal Target and Balik Modal From are applied.")
					)}</p>`,
				},
			],
			primary_action_label: __("Import"),
			primary_action: (values) => {
				if (!values.attach) {
					frappe.msgprint(__("Please attach a file"));
					return;
				}
				frappe.call({
					method: "pos_next.api.outlet_targets.import_outlet_targets",
					args: { file_url: values.attach, month_start: this._month_start() },
					freeze: true,
					callback: (r) => {
						d.hide();
						this._import_summary(r.message || {});
						this.refresh();
					},
				});
			},
		});
		d.show();
	}

	_import_summary(result) {
		const errors = result.errors || [];
		const html = [
			`<p>${__("Updated")}: <b>${result.updated || 0}</b> · ${__("Skipped")}: <b>${result.skipped || 0}</b> · ${__("Errors")}: <b>${errors.length}</b></p>`,
		];
		if (errors.length) {
			html.push(
				`<ul class="ot-import-errors">${errors
					.map(
						(e) =>
							`<li>${__("Row")} ${e.row} · ${frappe.utils.escape_html(e.company || "")}: ${frappe.utils.escape_html(e.message || "")}</li>`
					)
					.join("")}</ul>`
			);
		}
		frappe.msgprint({
			title: __("Import summary"),
			indicator: errors.length ? "orange" : "green",
			message: html.join(""),
		});
	}
}
