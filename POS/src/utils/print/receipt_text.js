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

const ALIGN_LEFT = 0
const ALIGN_CENTER = 1

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
	if (raw.length <= w && !raw.includes("\n")) return [raw]
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
 */
export function buildReceiptTextJob(doc, { cols, logo = true } = {}) {
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
		s.blank()
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
					`-${money(item.discount_amount || 0)}`,
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
export function buildCrewSlipTextJob(doc, { cols } = {}) {
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
		s.blank()
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
		itemLines.push(...leftRight(`${lead}${label}`, "", cBig, lead.length))
	}
	if (itemLines.length) {
		s.rule(c)
		s.lines(itemLines, { size: "big", bold: true })
	}
	return s.ops
}
