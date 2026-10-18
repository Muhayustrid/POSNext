// Copyright (c) 2026, POS Next and contributors
// For license information, please see license.txt

frappe.ui.form.on("POS Backdate Entry", {
	setup(frm) {
		frm._touched_closing_modes = new Set()
		frm._touched_payment_rows = new Set()
		frm._available_packages = []
		frm._shift_context = null

		frm.set_query("pos_opening_shift", () => ({
			query: "pos_next.api.backdate_invoices.pos_opening_shift_query",
			filters: frm.doc.company ? { company: frm.doc.company } : {},
		}))

		frm.set_query("mode_of_payment", "payments", () => {
			const modes = frm._shift_context?.payment_modes || []
			return modes.length ? { filters: { name: ["in", modes] } } : {}
		})

		frm.set_query("item_code", "items", () => ({
			filters: {
				is_sales_item: 1,
				disabled: 0,
			},
		}))
	},

	onload(frm) {
		if (frm.is_new()) {
			// "Today"/"Now" defaults use the user's timezone; the server window check uses system time
			const [d, t] = system_now()
			frm.doc.closing_date = d
			frm.doc.closing_time = t
		}
		if (frm.doc.pos_opening_shift) {
			load_shift_context(frm)
		}
	},

	refresh(frm) {
		refresh_invoice_no_options(frm)
		if (frm.doc.docstatus === 0) {
			frm.add_custom_button(__("Add Sale"), () => add_sale(frm), __("Actions"))
			if (
				(frm.doc.items || []).some((it) => it.pos_package_role === "Package")
			) {
				frm.add_custom_button(
					__("Edit Package"),
					() => prompt_edit_package(frm),
					__("Actions"),
				)
			}
		}

		if (frm.doc.pos_opening_shift && !frm._shift_context) {
			load_shift_context(frm)
		} else if (frm._shift_context) {
			render_shift_invoices(frm)
			toggle_confirmation_code(frm)
		}
	},

	pos_opening_shift(frm) {
		if (!frm.doc.pos_opening_shift) {
			frm._shift_context = null
			frm._available_packages = []
			if (frm.fields_dict.shift_invoices_html?.$wrapper) {
				frm.fields_dict.shift_invoices_html.$wrapper.empty()
			}
			return
		}

		// reqd tables get an empty row on new forms; only confirm when something was entered
		if ((frm.doc.items || []).some((r) => r.item_code)) {
			frappe.confirm(
				__(
					"Changing the opening shift will clear all entered invoices, items, and payments. Continue?",
				),
				() => {
					clear_entry_data(frm)
					load_shift_context(frm)
				},
				() => {
					frm.set_value("pos_opening_shift", frm._prev_opening_shift || "")
				},
			)
		} else {
			clear_entry_data(frm)
			load_shift_context(frm)
		}
	},
})

frappe.ui.form.on("POS Backdate Entry Invoice", {
	invoices_remove(frm) {
		sync_invoices_and_recompute(frm)
	},
	entry_type(frm) {
		recompute_all(frm)
	},
	posting_date(frm) {
		recompute_all(frm)
	},
	customer(frm) {
		refresh_invoice_no_options(frm)
	},
})

frappe.ui.form.on("POS Backdate Entry Item", {
	before_items_remove(frm, cdt, cdn) {
		const row = locals[cdt][cdn]
		if (row && row.pos_package_role === "Package Item") {
			frappe.msgprint(
				__(
					"Cannot remove a package component row directly. Delete or edit the package parent row instead.",
				),
			)
			return false
		}
		if (row && row.pos_package_role === "Package" && row.pos_package_instance) {
			const inst = row.pos_package_instance
			frm.doc.items = (frm.doc.items || []).filter(
				(it) => it.pos_package_instance !== inst || it.name === row.name,
			)
			frm.refresh_field("items")
		}
	},

	items_add(frm, cdt, cdn) {
		default_invoice_no(frm, cdt, cdn)
	},

	items_remove(frm) {
		recompute_all(frm)
	},

	form_render(frm, cdt, cdn) {
		const row = locals[cdt][cdn]
		const grid_row = frm.fields_dict.items.grid.grid_rows_by_docname[cdn]
		if (!grid_row || !grid_row.form) return

		if (row.pos_package_role === "Package Item") {
			;["item_code", "qty", "rate", "discount_percentage", "uom"].forEach(
				(fn) => {
					grid_row.form.set_df_property(fn, "read_only", 1)
				},
			)
		} else if (row.pos_package_role === "Package") {
			const $bar = grid_row.form.wrapper.find(".grid-header-toolbar")
			if ($bar.length && !$bar.find(".btn-edit-pkg").length) {
				$(
					`<button type="button" class="btn btn-xs btn-primary btn-edit-pkg" style="margin-left: 8px;">${__("Edit Package")}</button>`,
				)
					.appendTo($bar)
					.on("click", (e) => {
						e.preventDefault()
						edit_package_dialog(frm, cdt, cdn)
					})
			}
		}
	},

	item_code(frm, cdt, cdn) {
		const row = locals[cdt][cdn]
		if (!row.item_code) return

		if (!frm.doc.pos_opening_shift) {
			frappe.msgprint(__("Please select a POS Opening Shift first."))
			frappe.model.set_value(cdt, cdn, "item_code", "")
			return
		}

		if (row.pos_package_role === "Package Item") {
			frappe.msgprint(__("Cannot change item code on a package component row."))
			frappe.model.set_value(cdt, cdn, "item_code", row._prev_item_code || "")
			return
		}
		row._prev_item_code = row.item_code

		// Check if item is a POS Package parent
		const pkg = (frm._available_packages || []).find(
			(p) => p.parent_item === row.item_code,
		)
		if (pkg) {
			if (!pkg.groups || !pkg.groups.length) {
				// Package without choice groups: quote directly
				frappe.call({
					method: "pos_next.api.backdate_invoices.quote_backdate_package",
					args: {
						pos_opening_shift: frm.doc.pos_opening_shift,
						package: pkg.name,
						choices: JSON.stringify([]),
					},
					freeze: true,
					freeze_message: __("Quoting package..."),
					callback: (r) => {
						if (r.message) {
							apply_package_quote(frm, cdt, cdn, pkg, r.message)
						}
					},
				})
			} else {
				// Package with choice groups: open choice dialog
				open_package_choice_dialog(frm, cdt, cdn, pkg)
			}
			return
		}

		// Regular item: clear any package metadata
		frappe.model.set_value(cdt, cdn, {
			pos_package: "",
			pos_package_instance: "",
			pos_package_role: "",
			pos_package_snapshot: "",
		})

		const inv = (frm.doc.invoices || []).find(
			(i) => cint(i.invoice_no) === cint(row.invoice_no),
		)
		const p_date = inv
			? inv.posting_date
			: frm.doc.closing_date || frappe.datetime.now_date()

		frappe.call({
			method: "pos_next.api.backdate_invoices.get_item_rate",
			args: {
				pos_opening_shift: frm.doc.pos_opening_shift,
				item_code: row.item_code,
				uom: row.uom || null,
				posting_date: p_date,
			},
			callback: (r) => {
				if (!r.message) return
				const d = r.message
				const rate =
					d.price_list_rate !== undefined ? d.price_list_rate : d.rate || 0
				const qty = flt(row.qty) || 1
				const amount = flt(qty * rate, 2)

				frappe.model.set_value(cdt, cdn, {
					item_name: d.item_name || row.item_code,
					uom: d.uom || row.uom,
					conversion_factor: flt(d.conversion_factor) || 1,
					rate: rate,
					qty: qty,
					amount: amount,
					warehouse: d.warehouse || frm._shift_context?.warehouse || "",
				})
				update_invoice_and_closing(frm, row.invoice_no)
			},
		})
	},

	qty(frm, cdt, cdn) {
		calculate_item_amount(frm, cdt, cdn)
	},

	rate(frm, cdt, cdn) {
		calculate_item_amount(frm, cdt, cdn)
	},

	discount_percentage(frm, cdt, cdn) {
		calculate_item_amount(frm, cdt, cdn)
	},
})

frappe.ui.form.on("POS Backdate Entry Payment", {
	amount(frm, cdt, cdn) {
		const row = locals[cdt][cdn]
		frm._touched_payment_rows.add(row.name)
		recompute_closing_payments(frm)
	},
	mode_of_payment(frm) {
		recompute_closing_payments(frm)
	},
	payments_add(frm, cdt, cdn) {
		default_invoice_no(frm, cdt, cdn)
	},
	payments_remove(frm) {
		recompute_closing_payments(frm)
	},
})

frappe.ui.form.on("POS Backdate Entry Closing Payment", {
	closing_amount(frm, cdt, cdn) {
		const row = locals[cdt][cdn]
		if (row?.mode_of_payment) {
			frm._touched_closing_modes.add(row.mode_of_payment)
			const diff = flt(row.closing_amount) - flt(row.expected_amount)
			frappe.model.set_value(cdt, cdn, "difference", diff)
		}
	},
})

// Helper functions

function load_shift_context(frm) {
	if (!frm.doc.pos_opening_shift) return
	frm._prev_opening_shift = frm.doc.pos_opening_shift

	frappe.call({
		method: "pos_next.api.backdate_invoices.get_shift_context",
		args: { pos_opening_shift: frm.doc.pos_opening_shift },
		freeze: true,
		freeze_message: __("Loading shift context..."),
		callback: (r) => {
			if (!r.message) return
			const ctx = r.message
			frm._shift_context = ctx

			if (ctx.shift) {
				frm.set_value("company", ctx.shift.company)
				frm.set_value("pos_profile", ctx.shift.pos_profile)
				if (ctx.shift_period) frm.set_value("shift_period", ctx.shift_period)
			}

			resolve_cash_mode(frm)
			fetch_packages(frm)
			render_shift_invoices(frm)
			toggle_confirmation_code(frm)

			if (
				frm.doc.docstatus === 0 &&
				(!frm.doc.closing_payments || !frm.doc.closing_payments.length)
			) {
				recompute_closing_payments(frm)
			}
		},
	})
}

function resolve_cash_mode(frm) {
	const profile = frm.doc.pos_profile || frm._shift_context?.shift?.pos_profile
	if (!profile) {
		frm._cash_mode_of_payment = "Cash"
		return
	}
	frappe.call({
		method:
			"pos_next.pos_next.doctype.pos_closing_shift.pos_closing_shift.get_effective_cash_mode_of_payment",
		args: { pos_profile: profile },
		callback: (r) => {
			frm._cash_mode_of_payment = r.message || "Cash"
		},
	})
}

function fetch_packages(frm) {
	if (!frm.doc.pos_opening_shift) return
	frappe.call({
		method: "pos_next.api.backdate_invoices.get_backdate_packages",
		args: {
			pos_opening_shift: frm.doc.pos_opening_shift,
			posting_date: frm.doc.closing_date || frappe.datetime.now_date(),
		},
		callback: (r) => {
			frm._available_packages = r.message || []
		},
	})
}

function toggle_confirmation_code(frm) {
	const gate = frm._shift_context?.code_gate
	const required = Boolean(
		gate && (gate.discount_code_required || gate.refund_code_required),
	)
	if (frm.fields_dict.invoices?.grid) {
		frm.fields_dict.invoices.grid.update_docfield_property(
			"confirmation_code",
			"hidden",
			required ? 0 : 1,
		)
		frm.fields_dict.invoices.grid.update_docfield_property(
			"confirmation_code",
			"in_list_view",
			required ? 1 : 0,
		)
		frm.fields_dict.invoices.grid.refresh()
	}
}

function render_shift_invoices(frm) {
	const $wrapper = frm.fields_dict.shift_invoices_html?.$wrapper
	if (!$wrapper) return

	$wrapper.empty()
	const invoices = frm._shift_context?.shift_invoices || []
	// shift invoices already being returned/corrected in this entry
	const in_entry = {}
	;(frm.doc.invoices || []).forEach((row) => {
		if (row.against_invoice) {
			in_entry[row.against_invoice] = in_entry[row.against_invoice] || []
			in_entry[row.against_invoice].push(row)
		}
	})

	if (!invoices.length) {
		$wrapper.html(
			`<p class="text-muted" style="padding: 10px 0;">${__("No submitted invoices found in this shift.")}</p>`,
		)
		return
	}

	const rows_html = invoices
		.map((inv) => {
			const doc_url = `/app/${encodeURIComponent((inv.doctype || "pos-invoice").toLowerCase().replace(/ /g, "-"))}/${encodeURIComponent(inv.name)}`
			const type_badge = inv.is_return
				? `<span class="indicator-pill orange">${__("Return")}</span>`
				: `<span class="indicator-pill green">${__("Sale")}</span>`

			// a shift invoice can be returned or corrected once per entry
			const entry_row = (in_entry[inv.name] || [])[0]
			let entry_badge = ""
			let row_style = ""
			if (entry_row) {
				const is_ret = entry_row.entry_type === "Return"
				const label = is_ret ? __("Return") : __("Correct")
				entry_badge = ` <span class="indicator-pill ${is_ret ? "orange" : "blue"}" style="white-space: nowrap;">${label} #${cint(entry_row.invoice_no)}</span>`
				row_style = ` style="background-color: var(${is_ret ? "--bg-orange" : "--bg-blue"});"`
			}

			let actions = `<span class="text-muted">-</span>`
			if (frm.doc.docstatus === 0) {
				if (!inv.is_return) {
					const used_title = entry_row
						? __("Already added in this entry as Invoice No. {0}", [
								cint(entry_row.invoice_no),
							])
						: ""
					const return_attr = entry_row ? `disabled title="${used_title}"` : ""
					const correct_attr =
						entry_row || inv.has_return
							? `disabled title="${used_title || __("Cannot correct an invoice with submitted returns")}"`
							: ""
					actions = `
					<div class="btn-group">
						<button type="button" class="btn btn-xs btn-default btn-return" data-invoice="${frappe.utils.escape_html(inv.name)}" ${return_attr}>${__("Return")}</button>
						<button type="button" class="btn btn-xs btn-default btn-correct" data-invoice="${frappe.utils.escape_html(inv.name)}" ${correct_attr}>${__("Correct")}</button>
					</div>
				`
				}
			}

			return `
			<tr${row_style}>
				<td><a href="${doc_url}" target="_blank"><b>${frappe.utils.escape_html(inv.name)}</b></a></td>
				<td>${frappe.utils.escape_html(inv.customer || "")}</td>
				<td>${frappe.utils.escape_html(inv.posting_date || "")} ${frappe.utils.escape_html(inv.posting_time || "")}</td>
				<td class="text-right">${format_currency(inv.grand_total, frm._shift_context?.currency || "")}</td>
				<td style="white-space: nowrap;">${type_badge}${entry_badge}</td>
				<td class="text-center">${actions}</td>
			</tr>
		`
		})
		.join("")

	const html = `
		<div class="shift-invoices-container" style="margin-top: 5px;">
			<div class="table-responsive">
				<table class="table table-bordered table-sm table-hover" style="margin-bottom: 0;">
					<thead class="thead-light">
						<tr>
							<th style="width: 25%">${__("Invoice")}</th>
							<th style="width: 20%">${__("Customer")}</th>
							<th style="width: 20%">${__("Posting Date/Time")}</th>
							<th style="width: 15%" class="text-right">${__("Grand Total")}</th>
							<th style="width: 10%">${__("Type")}</th>
							<th style="width: 10%" class="text-center">${__("Actions")}</th>
						</tr>
					</thead>
					<tbody>
						${rows_html}
					</tbody>
				</table>
			</div>
		</div>
	`

	$wrapper.html(html)

	$wrapper.find(".btn-return").on("click", function (e) {
		e.preventDefault()
		const inv_name = $(this).attr("data-invoice")
		handle_return(frm, inv_name)
	})

	$wrapper.find(".btn-correct").on("click", function (e) {
		e.preventDefault()
		const inv_name = $(this).attr("data-invoice")
		handle_correct(frm, inv_name)
	})
}

function clear_entry_data(frm) {
	frm.doc.invoices = []
	frm.doc.items = []
	frm.doc.payments = []
	frm.doc.closing_payments = []
	frm._touched_closing_modes = new Set()
	frm._touched_payment_rows = new Set()
	frm.refresh_fields(["invoices", "items", "payments", "closing_payments"])
}

function refresh_invoice_no_options(frm) {
	// Select options for items/payments invoice_no, built from the Invoices table
	const options = (frm.doc.invoices || []).map((inv) => ({
		value: String(cint(inv.invoice_no)),
		label: [
			cint(inv.invoice_no),
			__(inv.entry_type || "Sale"),
			frappe.utils.escape_html(inv.customer || ""),
		]
			.filter(Boolean)
			.join(" · "),
	}))
	;["items", "payments"].forEach((table) => {
		frm.fields_dict[table].grid.update_docfield_property(
			"invoice_no",
			"options",
			options,
		)
	})
}

function default_invoice_no(frm, cdt, cdn) {
	const invoices = frm.doc.invoices || []
	if (!invoices.length || locals[cdt][cdn].invoice_no) return
	frappe.model.set_value(
		cdt,
		cdn,
		"invoice_no",
		String(cint(invoices[invoices.length - 1].invoice_no)),
	)
}

function get_next_invoice_no(frm) {
	let max_no = 0
	;(frm.doc.invoices || []).forEach((inv) => {
		if (cint(inv.invoice_no) > max_no) max_no = cint(inv.invoice_no)
	})
	return max_no + 1
}

function handle_return(frm, invoice_name) {
	if (!frm._shift_context || !frm._shift_context.shift_invoices) return
	const inv = frm._shift_context.shift_invoices.find(
		(i) => i.name === invoice_name,
	)
	if (!inv) return
	if (inv.is_return) {
		frappe.msgprint(__("Cannot return a return invoice."))
		return
	}

	const next_no = get_next_invoice_no(frm)
	// ERPNext requires a return to be posted after its original invoice
	const after_orig = moment(`${inv.posting_date} ${inv.posting_time}`)
		.add(1, "second")
		.format("YYYY-MM-DD HH:mm:ss")
	const [p_date, p_time] = after_orig.split(" ")

	frm.add_child("invoices", {
		invoice_no: next_no,
		entry_type: "Return",
		against_invoice: inv.name,
		against_doctype: inv.doctype || "POS Invoice",
		customer: inv.customer,
		posting_date: p_date,
		posting_time: p_time,
	})

	frappe.call({
		method: "pos_next.api.backdate_invoices.get_return_items",
		args: { invoice: inv.name },
		freeze: true,
		freeze_message: __("Fetching returnable items..."),
		callback: (r) => {
			const items = r.message || []
			if (!items.length) {
				frappe.msgprint(
					__("No returnable items found for invoice {0}.", [inv.name]),
				)
			}
			items.forEach((it) => {
				frm.add_child("items", {
					invoice_no: next_no,
					item_code: it.item_code,
					item_name: it.item_name,
					uom: it.uom,
					conversion_factor: flt(it.conversion_factor) || 1,
					qty: 0,
					remaining_qty: flt(it.remaining_qty),
					rate: flt(it.price_list_rate) || flt(it.rate),
					amount: 0,
					against_item_row: it.against_item_row,
					warehouse: it.warehouse || frm._shift_context?.warehouse || "",
				})
			})

			let first_mode = ""
			if (inv.payments?.length) {
				first_mode = inv.payments[0].mode_of_payment
			} else if (frm._shift_context.payment_modes?.length) {
				first_mode = frm._shift_context.payment_modes[0]
			}
			if (first_mode) {
				frm.add_child("payments", {
					invoice_no: next_no,
					mode_of_payment: first_mode,
					amount: 0,
				})
			}

			frm.refresh_fields(["invoices", "items", "payments"])
			recompute_all(frm)
			frappe.show_alert({
				message: __(
					"Return for Invoice {0} added (Invoice No. {1}). Please enter return quantities.",
					[inv.name, next_no],
				),
				indicator: "blue",
			})
		},
	})
}

function handle_correct(frm, invoice_name) {
	if (!frm._shift_context || !frm._shift_context.shift_invoices) return
	const inv = frm._shift_context.shift_invoices.find(
		(i) => i.name === invoice_name,
	)
	if (!inv) return
	if (inv.is_return) {
		frappe.msgprint(
			__("Cannot correct a return invoice. Use Return + new Sale instead."),
		)
		return
	}
	if (inv.has_return) {
		frappe.msgprint(
			__(
				"Cannot correct invoice {0} because a submitted return exists against it. Use Return + new Sale instead.",
				[invoice_name],
			),
		)
		return
	}

	const next_no = get_next_invoice_no(frm)
	frappe.call({
		method: "frappe.client.get",
		args: {
			doctype: inv.doctype || "POS Invoice",
			name: inv.name,
		},
		freeze: true,
		freeze_message: __("Loading invoice details..."),
		callback: (r) => {
			const orig = r.message
			if (!orig) return

			frm.add_child("invoices", {
				invoice_no: next_no,
				entry_type: "Correction",
				against_invoice: orig.name,
				against_doctype: orig.doctype,
				customer: orig.customer,
				posting_date: orig.posting_date,
				posting_time: orig.posting_time || frappe.datetime.now_time(),
				grand_total: flt(orig.grand_total),
			})
			;(orig.items || []).forEach((it) => {
				frm.add_child("items", {
					invoice_no: next_no,
					item_code: it.item_code,
					item_name: it.item_name,
					uom: it.uom,
					conversion_factor: flt(it.conversion_factor) || 1,
					qty: Math.abs(flt(it.qty)),
					rate: flt(it.price_list_rate) || flt(it.rate),
					discount_percentage: flt(it.discount_percentage) || 0,
					amount: Math.abs(flt(it.amount)),
					warehouse: it.warehouse,
					against_item_row: it.name,
					pos_package: it.pos_package,
					pos_package_instance: it.pos_package_instance,
					pos_package_role: it.pos_package_role,
					pos_package_snapshot: it.pos_package_snapshot,
				})
			})
			;(orig.payments || []).forEach((p) => {
				frm.add_child("payments", {
					invoice_no: next_no,
					mode_of_payment: p.mode_of_payment,
					amount: Math.abs(flt(p.amount)),
				})
			})

			frm.refresh_fields(["invoices", "items", "payments"])
			recompute_all(frm)
			frappe.show_alert({
				message: __(
					"Correction for Invoice {0} added (Invoice No. {1}). Edit items/payments as needed.",
					[orig.name, next_no],
				),
				indicator: "blue",
			})
		},
	})
}

function system_now() {
	return frappe.datetime.system_datetime().split(" ")
}

function add_sale(frm) {
	if (!frm.doc.pos_opening_shift) {
		frappe.msgprint(__("Please select a POS Opening Shift first."))
		return
	}
	const next_no = get_next_invoice_no(frm)
	const def_customer = frm._shift_context?.default_customer || ""
	// default to the closing datetime so the new sale never lands after the re-close
	const [now_d, now_t] = system_now()
	const p_date = frm.doc.closing_date || now_d
	const p_time = (frm.doc.closing_date && frm.doc.closing_time) || now_t

	frm.add_child("invoices", {
		invoice_no: next_no,
		entry_type: "Sale",
		customer: def_customer,
		posting_date: p_date,
		posting_time: p_time,
	})

	if (frm._shift_context?.payment_modes?.length) {
		frm.add_child("payments", {
			invoice_no: next_no,
			mode_of_payment: frm._shift_context.payment_modes[0],
			amount: 0,
		})
	}

	frm.refresh_fields(["invoices", "payments"])
	recompute_all(frm)
	frappe.show_alert({
		message: __("Sale added (Invoice No. {0}). Add items in the Items table.", [
			next_no,
		]),
		indicator: "blue",
	})
}

function calculate_item_amount(frm, cdt, cdn) {
	const row = locals[cdt][cdn]
	const qty = flt(row.qty)
	const rate = flt(row.rate)
	const disc = flt(row.discount_percentage) || 0
	const amount = flt(qty * rate * (1 - disc / 100), 2)

	if (row.remaining_qty && flt(row.qty) > flt(row.remaining_qty)) {
		frappe.show_alert({
			message: __("Return qty {0} exceeds remaining qty {1} for item {2}", [
				row.qty,
				row.remaining_qty,
				row.item_code,
			]),
			indicator: "orange",
		})
	}

	if (row.amount !== amount) {
		frappe.model.set_value(cdt, cdn, "amount", amount)
	}
	update_invoice_and_closing(frm, row.invoice_no)
}

function update_invoice_and_closing(frm, raw_invoice_no) {
	const invoice_no = cint(raw_invoice_no)
	const inv_items = (frm.doc.items || []).filter(
		(it) => cint(it.invoice_no) === invoice_no,
	)
	const total = inv_items.reduce((sum, it) => sum + flt(it.amount), 0)

	const inv_row = (frm.doc.invoices || []).find(
		(i) => cint(i.invoice_no) === invoice_no,
	)
	if (inv_row && flt(inv_row.grand_total) !== flt(total)) {
		inv_row.grand_total = flt(total, 2)
		frm.refresh_field("invoices")
	}

	sync_single_payment_amount(frm, invoice_no)
	recompute_closing_payments(frm)
}

function sync_invoices_and_recompute(frm) {
	const valid_inv_nos = new Set(
		(frm.doc.invoices || []).map((i) => cint(i.invoice_no)),
	)
	let items_changed = false
	let payments_changed = false

	if (frm.doc.items) {
		const new_items = frm.doc.items.filter((it) =>
			valid_inv_nos.has(cint(it.invoice_no)),
		)
		if (new_items.length !== frm.doc.items.length) {
			frm.doc.items = new_items
			items_changed = true
		}
	}
	if (frm.doc.payments) {
		const new_payments = frm.doc.payments.filter((p) =>
			valid_inv_nos.has(cint(p.invoice_no)),
		)
		if (new_payments.length !== frm.doc.payments.length) {
			frm.doc.payments = new_payments
			payments_changed = true
		}
	}

	if (items_changed) frm.refresh_field("items")
	if (payments_changed) frm.refresh_field("payments")
	recompute_all(frm)
}

function sync_single_payment_amount(frm, raw_invoice_no) {
	const invoice_no = cint(raw_invoice_no)
	const inv_payments = (frm.doc.payments || []).filter(
		(p) => cint(p.invoice_no) === invoice_no,
	)
	if (inv_payments.length === 1) {
		const pay_row = inv_payments[0]
		if (!frm._touched_payment_rows.has(pay_row.name)) {
			const inv = (frm.doc.invoices || []).find(
				(i) => cint(i.invoice_no) === invoice_no,
			)
			if (inv) {
				const expected_amt =
					inv.entry_type === "Return"
						? -Math.abs(flt(inv.grand_total))
						: flt(inv.grand_total)
				if (flt(pay_row.amount) !== expected_amt) {
					pay_row.amount = expected_amt
					frm.refresh_field("payments")
				}
			}
		}
	}
}

function recompute_all(frm) {
	const inv_totals = {}
	;(frm.doc.items || []).forEach((it) => {
		const no = cint(it.invoice_no)
		inv_totals[no] = (inv_totals[no] || 0) + flt(it.amount)
	})

	let inv_changed = false
	;(frm.doc.invoices || []).forEach((inv) => {
		const total = flt(inv_totals[cint(inv.invoice_no)] || 0, 2)
		if (flt(inv.grand_total) !== total) {
			inv.grand_total = total
			inv_changed = true
		}
		sync_single_payment_amount(frm, inv.invoice_no)
	})

	if (inv_changed) frm.refresh_field("invoices")
	refresh_invoice_no_options(frm)
	if (frm._shift_context) render_shift_invoices(frm)
	recompute_closing_payments(frm)
}

function recompute_closing_payments(frm) {
	if (!frm._shift_context || !frm._shift_context.closing_counts) return
	frm._touched_closing_modes = frm._touched_closing_modes || new Set()

	const old_counts = frm._shift_context.closing_counts
	const counts_by_mode = {}
	old_counts.forEach((row) => {
		counts_by_mode[row.mode_of_payment] = {
			opening_amount: flt(row.opening_amount),
			expected_amount: flt(row.expected_amount),
			closing_amount: flt(row.closing_amount),
		}
	})

	const all_modes = new Set(Object.keys(counts_by_mode))
	;(frm._shift_context.payment_modes || []).forEach((m) => all_modes.add(m))

	const sale_corr_payments = {}
	const return_refunds = {}
	const corrected_orig_payments = {}
	;(frm.doc.invoices || []).forEach((inv) => {
		const pays = (frm.doc.payments || []).filter(
			(p) => cint(p.invoice_no) === cint(inv.invoice_no),
		)
		if (inv.entry_type === "Sale" || inv.entry_type === "Correction") {
			pays.forEach((p) => {
				sale_corr_payments[p.mode_of_payment] =
					(sale_corr_payments[p.mode_of_payment] || 0) + flt(p.amount)
			})
		} else if (inv.entry_type === "Return") {
			pays.forEach((p) => {
				return_refunds[p.mode_of_payment] =
					(return_refunds[p.mode_of_payment] || 0) + Math.abs(flt(p.amount))
			})
		}

		if (inv.entry_type === "Correction" && inv.against_invoice) {
			const orig_inv = (frm._shift_context.shift_invoices || []).find(
				(i) => i.name === inv.against_invoice,
			)
			if (orig_inv?.payments) {
				orig_inv.payments.forEach((p) => {
					corrected_orig_payments[p.mode_of_payment] =
						(corrected_orig_payments[p.mode_of_payment] || 0) + flt(p.amount)
				})
			}
		}
	})

	const cash_mode = frm._cash_mode_of_payment || "Cash"
	const existing_rows = {}
	;(frm.doc.closing_payments || []).forEach((row) => {
		existing_rows[row.mode_of_payment] = row
	})

	frm.doc.closing_payments = []
	Array.from(all_modes).forEach((mode) => {
		const old = counts_by_mode[mode] || {
			opening_amount: 0,
			expected_amount: 0,
			closing_amount: 0,
		}
		const delta =
			(sale_corr_payments[mode] || 0) -
			(return_refunds[mode] || 0) -
			(corrected_orig_payments[mode] || 0)
		const new_expected = flt(old.expected_amount + delta, 2)

		let closing_amount = 0
		const existing = existing_rows[mode]
		if (frm._touched_closing_modes.has(mode) && existing) {
			closing_amount = flt(existing.closing_amount)
		} else {
			const is_cash = mode === cash_mode || mode.toLowerCase().includes("cash")
			closing_amount = is_cash ? old.closing_amount : new_expected
		}

		const diff = flt(closing_amount - new_expected, 2)

		frm.add_child("closing_payments", {
			mode_of_payment: mode,
			opening_amount: old.opening_amount,
			expected_amount: new_expected,
			closing_amount: closing_amount,
			difference: diff,
		})
	})

	frm.refresh_field("closing_payments")
}

function open_package_choice_dialog(frm, cdt, cdn, pkg, existing_selections) {
	const fields = []
	const selected_map = {}
	if (existing_selections?.length) {
		existing_selections.forEach((sel) => {
			if (sel.option_id) selected_map[sel.option_id] = sel.qty || 1
		})
	}
	;(pkg.groups || []).forEach((group) => {
		fields.push({
			fieldtype: "Heading",
			label: `${group.label} (${__("Min: {0}, Max: {1}", [group.min_qty, group.max_qty])})`,
		})

		const group_opts = (pkg.options || []).filter(
			(o) => o.group_key === group.group_key,
		)
		group_opts.forEach((opt) => {
			const curr = frm._shift_context?.currency || ""
			const price_str = opt.price_adjustment
				? ` (+${format_currency(opt.price_adjustment, curr)})`
				: ""
			const label = `${opt.item_name}${price_str}`
			const fieldname = `opt_${opt.option_id.replace(/[^a-zA-Z0-9_]/g, "_")}`

			if (group.max_qty === 1) {
				fields.push({
					fieldtype: "Check",
					fieldname: fieldname,
					label: label,
					default: selected_map[opt.option_id] ? 1 : 0,
				})
			} else {
				fields.push({
					fieldtype: "Int",
					fieldname: fieldname,
					label: label,
					default: selected_map[opt.option_id] || 0,
				})
			}
		})
	})

	const dialog = new frappe.ui.Dialog({
		title: __("Select Options for {0}", [pkg.package_name]),
		fields: fields,
		primary_action_label: __("Apply"),
		primary_action(values) {
			let valid = true
			const choices = []

			for (const group of pkg.groups || []) {
				const group_opts = (pkg.options || []).filter(
					(o) => o.group_key === group.group_key,
				)
				let picked_qty = 0
				const selected_opts = []

				for (const opt of group_opts) {
					const fn = `opt_${opt.option_id.replace(/[^a-zA-Z0-9_]/g, "_")}`
					const val = Number(values[fn]) || 0
					if (val > 0) {
						picked_qty += val
						selected_opts.push({ option_id: opt.option_id, qty: val })
					}
				}

				if (picked_qty < group.min_qty) {
					frappe.msgprint(
						__("Choose at least {0} item(s) from {1}.", [
							group.min_qty,
							group.label,
						]),
					)
					valid = false
					break
				}
				if (picked_qty > group.max_qty) {
					frappe.msgprint(
						__("Choose at most {0} item(s) from {1}.", [
							group.max_qty,
							group.label,
						]),
					)
					valid = false
					break
				}

				choices.push({
					group_key: group.group_key,
					options: selected_opts,
				})
			}

			if (!valid) return
			dialog.hide()

			const row = locals[cdt][cdn]
			const inv = (frm.doc.invoices || []).find(
				(i) => row && cint(i.invoice_no) === cint(row.invoice_no),
			)
			const p_date =
				inv?.posting_date || frm.doc.closing_date || frappe.datetime.now_date()

			frappe.call({
				method: "pos_next.api.backdate_invoices.quote_backdate_package",
				args: {
					pos_opening_shift: frm.doc.pos_opening_shift,
					package: pkg.name,
					choices: JSON.stringify(choices),
					posting_date: p_date,
				},
				freeze: true,
				freeze_message: __("Quoting package..."),
				callback: (r) => {
					if (r.message) {
						apply_package_quote(frm, cdt, cdn, pkg, r.message)
					}
				},
			})
		},
	})

	dialog.show()
}

function apply_package_quote(frm, cdt, cdn, pkg, quote_res) {
	const row = locals[cdt][cdn]
	const inv_no = row.invoice_no
	const instance_id =
		row.pos_package_instance ||
		`pkg-${Date.now()}-${Math.floor(Math.random() * 1000)}`
	const warehouse = row.warehouse || frm._shift_context?.warehouse || ""

	const parent_line = quote_res.lines?.[0]
	const parent_rate =
		parent_line && parent_line.rate !== undefined
			? parent_line.rate
			: quote_res.total || 0

	frappe.model.set_value(cdt, cdn, {
		item_code: pkg.parent_item,
		item_name: pkg.package_name,
		qty: 1,
		rate: parent_rate,
		amount: parent_rate,
		discount_percentage: 0,
		warehouse: warehouse,
		pos_package: pkg.name,
		pos_package_instance: instance_id,
		pos_package_role: "Package",
		pos_package_snapshot: JSON.stringify(quote_res.snapshot || {}),
	})

	// Remove any existing component rows for this package instance
	frm.doc.items = (frm.doc.items || []).filter(
		(it) =>
			!(
				it.pos_package_instance === instance_id &&
				it.pos_package_role === "Package Item"
			),
	)

	// Add component rows
	const comp_lines = (quote_res.lines || []).slice(1)
	comp_lines.forEach((comp) => {
		frm.add_child("items", {
			invoice_no: inv_no,
			item_code: comp.item_code,
			item_name: comp.item_name,
			uom: comp.uom || "",
			conversion_factor: 1,
			qty: comp.qty,
			rate: comp.rate || 0,
			amount: flt((comp.rate || 0) * comp.qty, 2),
			warehouse: warehouse,
			pos_package: pkg.name,
			pos_package_instance: instance_id,
			pos_package_role: "Package Item",
		})
	})

	frm.refresh_field("items")
	update_invoice_and_closing(frm, inv_no)
}

function edit_package_dialog(frm, cdt, cdn) {
	const row = locals[cdt][cdn]
	if (!row.pos_package) return

	const pkg = (frm._available_packages || []).find(
		(p) => p.name === row.pos_package,
	)
	if (!pkg) {
		frappe.msgprint(__("Package definition {0} not found.", [row.pos_package]))
		return
	}

	let existing_selections = []
	if (row.pos_package_snapshot) {
		try {
			const snap =
				typeof row.pos_package_snapshot === "string"
					? JSON.parse(row.pos_package_snapshot)
					: row.pos_package_snapshot
			existing_selections = snap.selections || []
		} catch (e) {
			// ignore parse error
		}
	}

	open_package_choice_dialog(frm, cdt, cdn, pkg, existing_selections)
}

function prompt_edit_package(frm) {
	const package_rows = (frm.doc.items || []).filter(
		(it) => it.pos_package_role === "Package",
	)
	if (!package_rows.length) {
		frappe.msgprint(__("No packages found in the Items table."))
		return
	}
	if (package_rows.length === 1) {
		const row = package_rows[0]
		edit_package_dialog(frm, row.doctype, row.name)
		return
	}
	const d = new frappe.ui.Dialog({
		title: __("Select Package to Edit"),
		fields: [
			{
				fieldtype: "Select",
				fieldname: "row_name",
				label: __("Package"),
				options: package_rows.map((r) => ({
					label: `${r.item_name} (Invoice #${r.invoice_no})`,
					value: r.name,
				})),
				reqd: 1,
			},
		],
		primary_action_label: __("Edit"),
		primary_action(values) {
			d.hide()
			const selected = package_rows.find((r) => r.name === values.row_name)
			if (selected) {
				edit_package_dialog(frm, selected.doctype, selected.name)
			}
		},
	})
	d.show()
}
