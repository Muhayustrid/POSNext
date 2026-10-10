/**
 * Sales recap over a date range: period presets, the 58mm "SALES RECAP" sheet
 * and its print path.
 *
 * The sheet deliberately mirrors the POS Next EOD Report print format (same
 * sections, same typography) so a period recap and a closing recap read the
 * same at the counter. It is built client-side from the session/period
 * summary API because a date range has no POS Closing Shift document for a
 * server print format to render.
 */
import { DEFAULT_CURRENCY, formatCurrency } from "@/utils/currency"

/** YYYY-MM-DD in local time — posting dates are calendar days, not instants. */
export function toISODate(date) {
	const y = date.getFullYear()
	const m = String(date.getMonth() + 1).padStart(2, "0")
	const d = String(date.getDate()).padStart(2, "0")
	return `${y}-${m}-${d}`
}

/**
 * Resolve a preset into the explicit from/to dates the server expects.
 * Returns null when there is nothing to fetch yet (incomplete or reversed
 * custom range).
 */
export function periodRange(
	preset,
	{ today = new Date(), from = "", to = "" } = {},
) {
	const day = (offset) => {
		const d = new Date(today)
		d.setDate(d.getDate() + offset)
		return toISODate(d)
	}
	switch (preset) {
		case "today":
			return { from: day(0), to: day(0) }
		case "yesterday":
			return { from: day(-1), to: day(-1) }
		case "last7":
			return { from: day(-6), to: day(0) }
		case "month":
			return {
				from: toISODate(new Date(today.getFullYear(), today.getMonth(), 1)),
				to: day(0),
			}
		case "year":
			return {
				from: toISODate(new Date(today.getFullYear(), 0, 1)),
				to: day(0),
			}
		case "custom":
			return from && to && from <= to ? { from, to } : null
		default:
			return null
	}
}

// Typography copied from the POS Next EOD Report print format so both sheets
// come out identical on the same printer lane.
const RECAP_CSS = `
	* { box-sizing: border-box; font-weight: normal; }
	body {
		font-family: 'DejaVu Sans', 'Arial', sans-serif;
		font-size: 11px;
		line-height: 1.4;
		color: #000;
		padding: 10px 8px 24px;
	}
	.center { text-align: center; }
	.title { font-size: 15px; font-weight: bold; letter-spacing: 1px; }
	.company { font-size: 12px; font-weight: bold; margin-top: 2px; }
	.divider { border: none; border-top: 2px dashed #333; margin: 6px 0; }
	.subtotal-divider { border: none; border-top: 1px dashed #333; margin: 5px 0; }
	.group-title { text-align: center; font-weight: bold; margin: 2px 0 3px; }
	.row, .item-row { display: -webkit-box; -webkit-box-pack: justify; }
	.row { display: flex; justify-content: space-between; }
	.row.bold { font-weight: bold; }
	.category { margin-top: 6px; }
	.category:first-of-type { margin-top: 0; }
	.category-name { font-weight: bold; margin-bottom: 1px; }
	.item-row { display: flex; justify-content: space-between; }
	.item-row .name { -webkit-box-flex: 1; flex: 1; padding-right: 8px; }
	.component-row { padding-left: 12px; font-size: 10px; }
	.footer { margin-top: 8px; text-align: center; font-size: 10px; }
`

const ESCAPES = {
	"&": "&amp;",
	"<": "&lt;",
	">": "&gt;",
	'"': "&quot;",
	"'": "&#39;",
}
const esc = (value) =>
	String(value ?? "").replace(/[&<>"']/g, (c) => ESCAPES[c])

// dd/mm/yyyy [HH:MM] — the fixed shape the EOD sheet prints, locale-agnostic.
function fmtDateTime(value) {
	if (!value) return "-"
	if (value instanceof Date) {
		const hh = String(value.getHours()).padStart(2, "0")
		const mm = String(value.getMinutes()).padStart(2, "0")
		return `${fmtDate(toISODate(value))} ${hh}:${mm}`
	}
	const m = String(value).match(
		/^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?/,
	)
	if (!m) return String(value)
	const [, y, mo, d, hh, mm] = m
	return hh ? `${d}/${mo}/${y} ${hh}:${mm}` : `${d}/${mo}/${y}`
}

function fmtDate(value) {
	const m = String(value ?? "").match(/^(\d{4})-(\d{2})-(\d{2})/)
	return m ? `${m[3]}/${m[2]}/${m[1]}` : String(value ?? "-")
}

const fmtQty = (qty) =>
	new Intl.NumberFormat(undefined, { maximumFractionDigits: 3 }).format(
		Number.parseFloat(qty || 0),
	)

const row = (label, value, bold = false) =>
	`<div class="row${bold ? " bold" : ""}"><span>${esc(label)}</span><span>${esc(value)}</span></div>`
const itemRow = (qty, name, amount) =>
	`<div class="item-row"><span class="name">${esc(fmtQty(qty))}x ${esc(name)}</span><span>${esc(amount)}</span></div>`
const title = (text) => `<div class="group-title">${esc(text)}</div>`
const note = (text) => `<div class="center">${esc(text)}</div>`
const DIVIDER = '<hr class="divider">'

/**
 * Full HTML document for one recap — a session summary (has opening_shift)
 * prints the shift header, a period summary prints the date-range header;
 * every section below the header is shared.
 */
export function buildRecapHTML(summary, { printedAt = new Date() } = {}) {
	const currency = summary.company_currency || DEFAULT_CURRENCY
	const money = (value) =>
		formatCurrency(Number.parseFloat(value || 0), currency)

	const header = summary.opening_shift
		? [
				row(__("Cashier"), summary.cashier || "-"),
				row(__("Shift"), summary.opening_shift),
				row(__("Start"), fmtDateTime(summary.period_start_date)),
				row(
					__("Closing"),
					summary.closing_time ? fmtDateTime(summary.closing_time) : "-",
				),
			]
		: [
				row(__("Outlet"), summary.pos_profile || "-"),
				row(
					__("Period"),
					`${fmtDate(summary.period_from)} - ${fmtDate(summary.period_to)}`,
				),
				row(__("Shifts"), String(summary.shift_count ?? 0)),
			]
	header.push(row(__("Print Date"), fmtDateTime(printedAt)))

	const payments = (summary.payments || []).map((p) =>
		row(p.mode_of_payment, money(p.amount)),
	)
	const charges = (summary.other_charges || []).map((c) =>
		row(c.label || c.account_head, money(c.amount)),
	)

	// Same nested shape as the EOD sheet: category name, then its items.
	// Falls back to the flat category line for a server that predates the
	// per-category item breakdown.
	const categoryBlocks = (summary.categories || []).map((c) => {
		const label = c.category || __("No category")
		const items = c.items || []
		if (!items.length) {
			return itemRow(c.qty, label, money(c.base_net_amount))
		}
		const lines = items.flatMap((i) => [
			itemRow(
				i.qty,
				`${i.item_name || i.item_code}${i.is_return ? ` (${__("Return")})` : ""}`,
				money(i.base_net_amount),
			),
			...(i.components || []).map(
				(c) =>
					`<div class="component-row">${esc(fmtQty(c.qty))}x ${esc(c.item_name || c.item_code)}</div>`,
			),
		])
		if (c.items_truncated) {
			lines.push(
				note(__("Top {0} items shown", [c.items_shown ?? items.length])),
			)
		}
		return `<div class="category"><div class="category-name">${esc(label)}</div>${lines.join("")}</div>`
	})

	const body = [
		`<div class="center title">${esc(__("SALES RECAP"))}</div>`,
		`<div class="center company">${esc(summary.company || "")}</div>`,
		DIVIDER,
		...header,
		DIVIDER,
		title(__("SALES SUMMARY")),
		row(__("Total Sales"), money(summary.net_sales)),
		row(__("Total Order"), String(summary.sales_count ?? 0)),
		row(__("Average / Order"), money(summary.average_sale)),
		DIVIDER,
		title(__("CASH SUMMARY")),
		row(__("Opening Balance"), money(summary.opening_cash)),
		row(__("Cash Payment"), money(summary.cash_collected)),
		// no expense ledger exists: print an honest dash, never a fake zero
		row(
			__("Total Expense"),
			summary.expense == null ? "-" : money(summary.expense),
		),
		row(__("Cash in Hand"), money(summary.cash_in_hand)),
		DIVIDER,
		title(__("PAYMENT METHOD")),
		...(payments.length ? payments : [note("-")]),
		'<hr class="subtotal-divider">',
		row(__("Total Cash"), money(summary.total_cash)),
		row(__("Total Non Cash"), money(summary.total_non_cash)),
		row(__("GRAND TOTAL"), money(summary.methods_grand_total), true),
		DIVIDER,
		title(__("CHARGE")),
		...charges,
		row(__("Tax / PPN"), money(summary.tax_total)),
		DIVIDER,
		title(__("DISCOUNT")),
		row(__("Product Discount"), money(summary.item_discount)),
		row(__("Invoice Discount"), money(summary.invoice_discount)),
		DIVIDER,
		title(__("REFUND")),
		row(
			`${__("Total Refund")} (${summary.returns_count ?? 0})`,
			money(summary.returns_total),
		),
		DIVIDER,
		title(__("SALES PER CATEGORY")),
		...(categoryBlocks.length ? categoryBlocks : [note("-")]),
	]
	if (summary.categories_truncated) {
		body.push(
			note(
				__("Top {0} of {1} categories", [
					summary.categories_shown,
					summary.categories_total_groups,
				]),
			),
		)
	}
	body.push(DIVIDER, `<div class="footer">${__("-- Akhir Laporan --")}</div>`)

	return `<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><title>${esc(__("Sales Recap"))}</title><style>${RECAP_CSS}</style></head>
<body>
${body.join("\n")}
</body>
</html>`
}

/**
 * Print a recap through the EOD lane of the print transport (imin -> qz ->
 * browser, per POS Settings). On driver failure there is NO automatic
 * fallback: the caller shows the retry panel (Print Ulang / Export PDF /
 * Tutup) and can produce the PDF on demand via downloadRecapPDF().
 * A sheet that already came out (postPrint) is never re-printed.
 *
 * The print stack (transport, drivers, offline caches) is loaded on demand:
 * the summary view itself never needs it.
 *
 * @returns {Promise<"printed"|"failed">} whether the sheet came out
 */
export async function printSalesRecap(summary, { posProfile = null } = {}) {
	const reference = summary.opening_shift
		? {
				reference_doctype: "POS Opening Shift",
				reference_name: summary.opening_shift,
			}
		: { reference_doctype: "POS Profile", reference_name: summary.pos_profile }
	try {
		const html = buildRecapHTML(summary)
		const { silentPrintHTML } = await import("@/utils/printInvoice")
		await silentPrintHTML(html, {
			posProfile,
			kind: "eod",
			logContext: reference,
		})
		return "printed"
	} catch (error) {
		if (error?.postPrint) throw error
		return "failed"
	}
}

/** Default download name for a summary's PDF export (see downloadRecapPDF). */
export function recapPDFFilename(summary) {
	const reference = summary.opening_shift || summary.pos_profile || "recap"
	return `${__("Sales Recap")} ${reference}.pdf`
}

/** Render the sheet server-side (wkhtmltopdf) and hand it to the browser. */
export async function downloadRecapPDF(html, filename) {
	const response = await fetch(
		"/api/method/frappe.utils.print_format.report_to_pdf",
		{
			method: "POST",
			headers: {
				"Content-Type": "application/x-www-form-urlencoded",
				"X-Frappe-CSRF-Token": window.csrf_token || "",
			},
			body: new URLSearchParams({ html, orientation: "Portrait" }),
		},
	)
	if (!response.ok) throw new Error(__("Could not create the sales recap PDF"))
	const url = URL.createObjectURL(await response.blob())
	const link = document.createElement("a")
	link.href = url
	link.download = filename
	document.body.appendChild(link)
	link.click()
	link.remove()
	URL.revokeObjectURL(url)
}
