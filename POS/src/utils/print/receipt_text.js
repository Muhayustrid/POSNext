/**
 * iMin text-mode receipts — the fast lane.
 *
 * The bitmap lane renders a whole receipt with html2canvas, PNG-encodes it,
 * uploads it to the device's print service and only then starts the head.
 * Text mode skips all of that: the receipt goes out as printer text commands
 * over the already-open WebSocket, and only the small logo travels as an
 * image. The printer draws the text with its own font, so the paper starts
 * moving almost immediately.
 *
 * Layout is done here, in fixed character columns, with the printer set to
 * its MONOSPACE typeface. Every glyph is then exactly MONO_CHAR_EM of the text
 * size wide, so `cols` characters fill the paper and columns line up without
 * relying on the SDK's printColumnsText.
 *
 * Content mirrors buildReceiptHTML / the "POS Next Receipt" print format and
 * buildCrewSlipHTML line for line — same rows, same guards, same order — so
 * switching lanes changes the typeface, never what the paper says.
 *
 * Pure data in, ops out: no DOM, no SDK, unit-testable. The iMin driver
 * resolves sizes and executes the ops.
 *
 * Op shapes:
 *   { op: "text", text, align: 0|1|2, size: "base"|"big"|"queue", bold }
 *   { op: "logo" }
 *   { op: "feed", dots }
 */

import { isAllocationSnapshot } from "@/utils/packageQuote"
import { formatQueueNumber } from "@/utils/queue/queueNumber"
import { formatCurrencyNumber, getCurrencySymbol } from "@/utils/currency"
import { __ } from "@/utils/translation"

/** Same file both receipt templates point their <img> at. */
export const RECEIPT_LOGO_URL = "/files/ropi-logo.png"
/** Logo width on paper: the templates size it 14mm. */
export const RECEIPT_LOGO_MM = 14

/** Monospace advance width as a fraction of the text size (Droid Sans Mono). */
export const MONO_CHAR_EM = 0.6

/**
 * Base text size in printer px at fontScale 100. 20 gives 31 columns on a
 * 58mm head (47 on 80mm): an invoice id row still fits on one line.
 */
export const TEXT_BASE_SIZE = 20

export const ALIGN_LEFT = 0
export const ALIGN_CENTER = 1

/** Label column of the info rows: the longest label ("Customer") fits. */
const LABEL_WIDTH = 8

/**
 * Characters per line for a text size. One column of slack: a line that
 * fills the head exactly risks wrapping on a build with an inner margin.
 */
export function colsFor(dots, size) {
	const per = size * MONO_CHAR_EM
	if (!(per > 0) || !(dots > 0)) return 32
	return Math.max(16, Math.floor(dots / per) - 1)
}

/** Text sizes (printer px) for the three size keys at a font scale. */
export function textSizes(fontScale = 100) {
	const base = Math.max(12, Math.round((TEXT_BASE_SIZE * fontScale) / 100))
	return {
		base,
		big: Math.round(base * 1.3),
		queue: Math.round(base * 2.4),
	}
}

/**
 * Greedy word wrap to `width` columns; a word longer than the line is hard
 * broken so nothing ever spills past the paper.
 */
export function wrapText(text, width) {
	const w = Math.max(1, width)
	const raw = String(text ?? "")
	// A line that already fits keeps its spacing: padded labels and
	// indented sub-lines are laid out with spaces on purpose.
	// A newline collapses to a space, as HTML renders it.
	if (raw.includes("\n")) return wrapText(raw.replace(/\s+/g, " ").trim(), w)
	if (raw.length <= w) return [raw]
	const words = raw.replace(/\s+/g, " ").trim().split(" ").filter(Boolean)
	const lines = []
	let line = ""
	for (let word of words) {
		while (word.length > w) {
			if (line) {
				lines.push(line)
				line = ""
			}
			lines.push(word.slice(0, w))
			word = word.slice(w)
		}
		if (!word) continue
		if (!line) line = word
		else if (line.length + 1 + word.length <= w) line += ` ${word}`
		else {
			lines.push(line)
			line = word
		}
	}
	if (line) lines.push(line)
	return lines.length ? lines : [""]
}

/**
 * `left` flush left, `right` flush right on the FIRST line (where the HTML
 * table cell puts the price). A left side too long for that line wraps;
 * continuation lines are indented by `indent` so a wrapped item name stays
 * under itself, not under the qty, and never runs under the price column.
 */
export function leftRight(left, right, cols, indent = 0) {
	const r = String(right ?? "")
	const width = Math.max(1, r ? cols - r.length - 1 : cols)
	const first = wrapText(left, width)
	const out = [r ? first[0].padEnd(cols - r.length) + r : first[0]]
	const rest = first.slice(1).join(" ")
	if (rest) {
		for (const l of wrapText(rest, Math.max(1, width - indent))) {
			out.push(" ".repeat(indent) + l)
		}
	}
	return out
}

/** "Label   : value", value wrapping under itself. */
function infoRow(label, value, cols) {
	const head = `${String(label).padEnd(LABEL_WIDTH)}: `
	const lines = wrapText(value, cols - head.length)
	return lines.map((l, i) => (i === 0 ? head : " ".repeat(head.length)) + l)
}

function moneyFormatter(currency) {
	const symbol = currency ? getCurrencySymbol(currency) : ""
	return (amount) =>
		`${symbol}${formatCurrencyNumber(Number.parseFloat(amount || 0), undefined, currency || null)}`
}

/** Builder state: collects ops for one sheet. */
function sheet() {
	const ops = []
	const api = {
		ops,
		text(text, { align = ALIGN_LEFT, size = "base", bold = false } = {}) {
			ops.push({ op: "text", text: String(text), align, size, bold })
			return api
		},
		lines(lines, style) {
			for (const l of lines) api.text(l, style)
			return api
		},
		blank() {
			return api.text("")
		},
		rule(cols) {
			return api.text("-".repeat(cols))
		},
	}
	return api
}

/**
 * The customer receipt as text ops.
 *
 * @param {object} doc - invoice dict (local or get_invoice shaped)
 * @param {object} layout
 * @param {{base:number, big:number, queue:number}} layout.cols - columns per size key
 * @param {boolean} [layout.logo=true]
 * @param {number} [layout.queueGapDots]
 */
export function buildReceiptTextJob(
	doc,
	{ cols, logo = true, queueGapDots } = {},
) {
	const d = doc || {}
	const c = cols?.base || 32
	const money = moneyFormatter(d.currency)
	const s = sheet()
	const items = Array.isArray(d.items) ? d.items : []

	if (d.pos_queue_number) {
		s.text("NO. ANTRIAN", { align: ALIGN_CENTER })
		s.text(formatQueueNumber(d.pos_queue_number), {
			align: ALIGN_CENTER,
			size: "queue",
			bold: true,
		})
		if (queueGapDots != null) {
			if (queueGapDots > 0) s.ops.push({ op: "feed", dots: queueGapDots })
		} else {
			s.blank()
		}
	}
	if (logo) s.ops.push({ op: "logo" })
	s.lines(wrapText(d.company || "POS Next", c), {
		align: ALIGN_CENTER,
		bold: true,
	})
	if (d.company_address_line) {
		s.lines(wrapText(d.company_address_line, c), { align: ALIGN_CENTER })
	}
	if (d.header) {
		s.lines(wrapText(d.header, c), { align: ALIGN_CENTER, bold: true })
	}

	s.rule(c)
	const cashierName = String(d.cashier_name || d.cashier || "").trim()
	const buyerName = String(d.buyer_name || "").trim()
	const customerName = String(d.customer_name || d.customer || "").trim()
	const timestamp = `${d.posting_date || ""} ${
		String(d.posting_time || "").split(".")[0]
	}`.trim()
	s.lines(infoRow("Invoice", d.name || "", c))
	if (cashierName) s.lines(infoRow("Cashier", cashierName, c))
	s.lines(infoRow("Date", timestamp, c))
	if (buyerName) s.lines(infoRow("Buyer", buyerName, c))
	else if (customerName) s.lines(infoRow("Customer", customerName, c))
	const isPartial =
		d.status === "Partly Paid" ||
		(d.outstanding_amount > 0 && d.outstanding_amount < d.grand_total)
	if (isPartial) s.lines(infoRow("Status", "PARTIAL PAYMENT", c))
	s.rule(c)

	const packageTotals = {}
	for (const item of items) {
		if (!item.pos_package_instance) continue
		packageTotals[item.pos_package_instance] =
			(packageTotals[item.pos_package_instance] || 0) +
			(item.quantity || item.qty || 0) *
				(item.price_list_rate || item.rate || 0)
	}
	let totalQty = 0
	for (const item of items) {
		totalQty += Number(item.quantity ?? item.qty) || 0
		const qty = item.quantity || item.qty || 0
		const name = item.item_name || item.item_code || ""
		const instance = item.pos_package_instance
		if (instance && item.pos_package_role === "Package Item") {
			s.lines(leftRight(`  - ${qty}x ${name}`, "", c, 6))
			continue
		}
		if (instance && item.pos_package_role === "Package") {
			const lead = `${qty}x `
			s.lines(
				leftRight(
					`${lead}${name}`,
					money(packageTotals[instance]),
					c,
					lead.length,
				),
			)
			continue
		}
		const displayRate = item.price_list_rate || item.rate || 0
		const isZeroPackageLine =
			item.pos_package_role === "Package" &&
			!displayRate &&
			isAllocationSnapshot(item.pos_package_snapshot)
		if (isZeroPackageLine) {
			s.lines(wrapText(name, c), { bold: true })
			continue
		}
		const lead = `${qty}x `
		if (item.is_free_item) {
			s.lines(
				leftRight(`${lead}${name} (${__("FREE")})`, money(0), c, lead.length),
			)
		} else {
			s.lines(
				leftRight(`${lead}${name}`, money(qty * displayRate), c, lead.length),
			)
		}
		const hasDiscount =
			(item.discount_percentage &&
				Number.parseFloat(item.discount_percentage) > 0) ||
			(item.discount_amount && Number.parseFloat(item.discount_amount) > 0)
		if (hasDiscount) {
			const pct = item.discount_percentage
				? ` (${Number(item.discount_percentage).toFixed(1)}%)`
				: ""
			s.lines(
				leftRight(
					`  Discount${pct}`,
					`-${money(Math.abs(item.discount_amount || 0))}`,
					c,
					2,
				),
			)
		}
		if (item.serial_no) {
			s.lines(
				wrapText(
					`  Serial No: ${String(item.serial_no).replace(/\n/g, ", ")}`,
					c,
				),
			)
		}
	}

	s.blank()
	s.lines(infoRow("Total Qty", String(totalQty), c))
	if (d.total_taxes_and_charges && d.total_taxes_and_charges > 0) {
		s.lines(
			leftRight(
				"Subtotal:",
				money((d.grand_total || 0) - (d.total_taxes_and_charges || 0)),
				c,
			),
		)
		s.lines(leftRight("Tax:", money(d.total_taxes_and_charges), c))
	}
	if (d.discount_amount) {
		const pct = d.additional_discount_percentage
			? ` (${Number(d.additional_discount_percentage).toFixed(1)}%)`
			: ""
		s.lines(
			leftRight(
				`Additional Discount${pct}:`,
				`-${money(Math.abs(d.discount_amount))}`,
				c,
			),
		)
	}

	s.rule(c)
	const total = (label, amount, style) =>
		s.lines(
			leftRight(`${String(label).padEnd(LABEL_WIDTH)}:`, money(amount), c),
			style,
		)
	total("TOTAL", d.grand_total, { bold: true })
	for (const p of d.payments || []) total(p.mode_of_payment, p.amount)
	const paid =
		d.paid_amount != null
			? d.paid_amount
			: (d.payments || []).reduce(
					(sum, p) => sum + (Number.parseFloat(p.amount) || 0),
					0,
				)
	if (paid) total("Paid", paid)
	if (d.change_amount && d.change_amount > 0) total("Change", d.change_amount)
	if (d.outstanding_amount && d.outstanding_amount > 0) {
		total("Due", d.outstanding_amount, { bold: true })
	}

	s.rule(c)
	if (d.footer) s.lines(wrapText(d.footer, c), { align: ALIGN_CENTER })
	s.text("Thank you", { align: ALIGN_CENTER })
	s.text("Powered by POS Next", { align: ALIGN_CENTER })
	return s.ops
}

/**
 * The crew slip as text ops — what was ordered, by whom; nothing
 * money-shaped. Item lines bold and a size up, like buildCrewSlipHTML.
 */
export function buildCrewSlipTextJob(doc, { cols, queueGapDots } = {}) {
	const d = doc || {}
	const c = cols?.base || 32
	const cBig = cols?.big || c
	const s = sheet()
	const items = Array.isArray(d.items) ? d.items : []

	if (d.pos_queue_number != null && d.pos_queue_number !== "") {
		s.text(__("NO. ANTRIAN"), { align: ALIGN_CENTER })
		s.text(formatQueueNumber(d.pos_queue_number), {
			align: ALIGN_CENTER,
			size: "queue",
			bold: true,
		})
		if (queueGapDots != null) {
			if (queueGapDots > 0) s.ops.push({ op: "feed", dots: queueGapDots })
		} else {
			s.blank()
		}
	}
	s.text(__("ORDER"), { align: ALIGN_CENTER, bold: true })

	const buyerName = String(d.buyer_name || "").trim()
	const party = buyerName || d.customer_name || d.customer || ""
	const cashierName = String(d.cashier_name || d.cashier || "").trim()
	const date = d.posting_date == null ? "" : String(d.posting_date).trim()
	const time = String(d.posting_time ?? "")
		.split(".")[0]
		.trim()
	const stamp = [date, /\d/.test(time) ? time : ""].filter(Boolean).join(" ")
	const rows = []
	if (d.name) rows.push(...infoRow(__("Invoice"), d.name, c))
	if (cashierName) rows.push(...infoRow(__("Cashier"), cashierName, c))
	if (stamp) rows.push(...infoRow(__("Date"), stamp, c))
	if (party) rows.push(...infoRow(__("Customer"), party, c))
	if (rows.length) {
		s.rule(c)
		s.lines(rows)
	}

	const itemLines = []
	for (const item of items) {
		const label = item.item_name || item.item_code
		if (!label) continue
		const qty = item.qty ?? item.quantity
		const lead = qty == null ? "" : `${qty}x `
		// Package components indented under their package line, as on the receipt.
		const pkg =
			item.pos_package_instance && item.pos_package_role === "Package Item"
		const prefix = pkg ? `  - ${lead}` : lead
		itemLines.push(...leftRight(`${prefix}${label}`, "", cBig, prefix.length))
	}
	if (itemLines.length) {
		s.rule(c)
		s.lines(itemLines, { size: "big", bold: true })
	}
	return s.ops
}

/**
 * Format a number like Python's format_rupiah:
 * Rp12.000, -Rp1.000, half-up rounding to whole.
 */
export function formatRupiah(value) {
	const n = Number(value) || 0
	const whole = Math.round(Math.abs(n))
	const sign = n < 0 && whole !== 0 ? "-" : ""
	const formatted = whole.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ".")
	return `${sign}Rp${formatted}`
}

/** Format quantity like Jinja's %g */
export function formatQty(qty) {
	const n = Number(qty) || 0
	return String(Number(n.toFixed(6)))
}

/**
 * The EOD / Tutup Kasir report as text ops.
 * Mirrors pos_next_eod_report line for line.
 *
 * @param {object} data - output of printing.get_eod_print_data
 * @param {object} [layout]
 * @param {number|{base:number}} [layout.cols]
 */
export function buildEodTextJob(data, { cols } = {}) {
	const d = data || {}
	const r = d.recap || d
	const c = typeof cols === "number" ? cols : (cols?.base || 32)
	const s = sheet()

	s.text("SALES RECAP", { align: ALIGN_CENTER, bold: true })
	if (d.company) {
		s.lines(wrapText(d.company, c), { align: ALIGN_CENTER, bold: true })
	}
	s.rule(c)

	// leftRight never wraps the right side: a long cashier name would run
	// past the paper, so it drops under its label, flush right.
	const head = (label, value) => {
		const v = String(value || "-")
		if (label.length + 1 + v.length <= c) return leftRight(label, v, c)
		return [label, ...wrapText(v, c).map((l) => l.padStart(c))]
	}
	s.lines(head("Cashier", d.cashier))
	s.lines(head("Shift", d.shift))
	s.lines(head("Start", d.start))
	s.lines(head("Closing", d.closing))
	s.lines(head("Print Date", d.print || d.print_date))
	s.rule(c)

	s.text("SALES SUMMARY", { align: ALIGN_CENTER, bold: true })
	s.lines(leftRight("Total Sales", formatRupiah(r.total_sales || 0), c))
	s.lines(leftRight("Total Order", String(r.total_order || 0), c))
	s.lines(leftRight("Average / Order", formatRupiah(r.average_per_order || 0), c))
	s.rule(c)

	const cash = r.cash || {}
	s.text("CASH SUMMARY", { align: ALIGN_CENTER, bold: true })
	s.lines(leftRight("Modal Awal", formatRupiah(cash.opening_balance || 0), c))
	s.lines(leftRight("Cash Payment", formatRupiah(cash.cash_payment || 0), c))
	s.lines(leftRight("Total Expense", formatRupiah(cash.total_expense || 0), c))
	s.lines(leftRight("Cash in Hand", formatRupiah(cash.cash_in_hand || 0), c))
	s.rule(c)

	s.text("PAYMENT METHOD", { align: ALIGN_CENTER, bold: true })
	for (const pm of r.payment_methods || []) {
		s.lines(leftRight(pm.mode_of_payment, formatRupiah(pm.amount || 0), c))
	}
	s.rule(c)
	s.lines(leftRight("Total Cash", formatRupiah(r.total_cash || 0), c))
	s.lines(leftRight("Total Non Cash", formatRupiah(r.total_non_cash || 0), c))
	s.lines(
		leftRight("GRAND TOTAL", formatRupiah(r.methods_grand_total || 0), c),
		{ bold: true },
	)
	s.rule(c)

	s.text("CHARGE", { align: ALIGN_CENTER, bold: true })
	s.lines(leftRight("Service Charge", formatRupiah(r.service_charge || 0), c))
	s.lines(leftRight("Tax / PPN", formatRupiah(r.tax_total || 0), c))
	s.rule(c)

	s.text("DISCOUNT", { align: ALIGN_CENTER, bold: true })
	s.lines(leftRight("Product Discount", formatRupiah(r.product_discount || 0), c))
	s.lines(leftRight("Payment Discount", formatRupiah(r.payment_discount || 0), c))
	s.rule(c)

	s.text("REFUND", { align: ALIGN_CENTER, bold: true })
	s.lines(leftRight("Total Refund", formatRupiah(r.refund_total || 0), c))
	s.rule(c)

	s.text("SALES PER CATEGORY", { align: ALIGN_CENTER, bold: true })
	const categories = r.categories || []
	if (categories.length > 0) {
		for (const cat of categories) {
			s.lines(wrapText(cat.category || "", c), { bold: true })
			for (const item of cat.items || []) {
				const lead = `${formatQty(item.qty || 0)}x `
				const ret = item.is_return ? " (Return)" : ""
				const itemName = `${lead}${item.item_name || item.item_code || ""}${ret}`
				s.lines(
					leftRight(itemName, formatRupiah(item.amount || 0), c, lead.length),
				)
				for (const comp of item.components || []) {
					const compLead = `  ${formatQty(comp.qty || 0)}x `
					const compName = `${compLead}${comp.item_name || comp.item_code || ""}`
					s.lines(wrapText(compName, c))
				}
			}
		}
	} else {
		s.text("-", { align: ALIGN_CENTER })
	}
	s.rule(c)

	s.text("-- Akhir Laporan --", { align: ALIGN_CENTER })
	return s.ops
}
