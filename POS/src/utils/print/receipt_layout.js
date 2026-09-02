/**
 * Pure helpers for receipt bitmap layout.
 *
 * No DOM, no canvas, no html2canvas — safe to unit-test in any environment.
 * The bitmap width is always `dots` (from paper), and the CSS leak that used
 * to hit the live POS DOM is gone: the bitmap frame is the sole width
 * authority; body rules are only for the browser-popup path
 * (printInvoice.js) via receiptStylesFor().
 */

import { dotsForPaper } from "./paper"

export const DEFAULT_TAIL_DOTS = 24
export const DEFAULT_FEED_DOTS = 160
const MAX_TAIL_DOTS = 200

const DEVICE_CONFIG_KEY = "pos_imin_device_config"

/**
 * Per-device print overrides, kept in localStorage on the till itself.
 * Lives here (rather than in imin_client) because the receipt builder and the
 * Direct Print preview need the same values the driver uses — three readers,
 * one source of truth. imin_client re-exports these for its existing callers.
 */
export function loadDeviceConfig() {
	try {
		return JSON.parse(localStorage.getItem(DEVICE_CONFIG_KEY) || "{}")
	} catch {
		return {}
	}
}

export function saveDeviceConfig(patch) {
	const next = { ...loadDeviceConfig(), ...patch }
	localStorage.setItem(DEVICE_CONFIG_KEY, JSON.stringify(next))
	return next
}

/**
 * CSS for the off-screen bitmap frame. Padding here replaces the dead
 * `body { padding: 10px }` that was stripped by the fragment parser
 * (frame is a <div>, not a <body>).
 */
export function receiptFrameStyle(dots) {
	return `width:${dots}px;background:#fff;color:#000;padding:8px;box-sizing:border-box;`
}

/**
 * White block appended under the receipt so the last text line is not
 * exactly at the raster edge. Height-defined (not margin) so it survives
 * margin-collapse and is counted in getBoundingClientRect().
 */
export function tailSpacerHTML(tailDots) {
	const n = Number(tailDots) || 0
	if (n <= 0) return ""
	const h = Math.max(0, Math.min(Math.floor(n), MAX_TAIL_DOTS))
	if (h <= 0) return ""
	return `<div class="pn-receipt-tail" style="height:${h}px;min-height:${h}px;width:100%;background:#fff;"></div>`
}

export function clampTailDots(v) {
	if (v == null || v === "") return DEFAULT_TAIL_DOTS
	const n = Number(v)
	if (!Number.isFinite(n)) return DEFAULT_TAIL_DOTS
	return Math.max(0, Math.min(Math.floor(n), MAX_TAIL_DOTS))
}

export function resolveReceiptLayout({
	paper = "58mm",
	customDots,
	tailDots,
} = {}) {
	const dots = dotsForPaper(paper, customDots)
	const tail = clampTailDots(tailDots)
	return { dots, tailDots: tail }
}

/**
 * Dot-aware stylesheet for the *browser popup* path only
 * (window.open + document.write, where <body> is real).
 * The bitmap path does not use this — the frame width is dots px.
 */
export function receiptStylesFor(dots) {
	const mm = dots / 8
	return `
	* { margin: 0; padding: 0; box-sizing: border-box; }
	body {
		font-family: 'Courier New', monospace;
		padding: 10px; width: ${mm}mm; margin: 0; max-width: ${mm}mm;
		font-weight: bold; color: black;
	}
	.receipt { width: 100%; }
	.header { text-align: center; margin-bottom: 20px; border-bottom: 2px dashed #000; padding-bottom: 10px; }
	.company-name { font-size: 18px; font-weight: bold; margin-bottom: 5px; }
	.invoice-info { margin-bottom: 15px; font-size: 12px; }
	.invoice-info div { display: flex; justify-content: space-between; margin-bottom: 3px; }
	.partial-status { color: #000; font-weight: bold; margin-bottom: 5px; }
	.items-table { width: 100%; margin-bottom: 15px; border-top: 1px dashed #000; border-bottom: 1px dashed #000; padding: 10px 0; }
	.item-row { margin-bottom: 10px; font-size: 12px; }
	.item-name { font-weight: bold; margin-bottom: 3px; }
	.item-details { display: flex; justify-content: space-between; font-size: 11px; }
	.item-discount { display: flex; justify-content: space-between; font-size: 10px; margin-top: 2px; }
	.item-serials { font-size: 9px; margin-top: 3px; padding: 3px 5px; border: 1px dashed #000; border-radius: 2px; }
	.item-serials-label { font-weight: bold; margin-bottom: 2px; }
	.item-serials-list { word-break: break-all; }
	.totals { margin-top: 15px; border-top: 1px dashed #000; padding-top: 10px; }
	.total-row { display: flex; justify-content: space-between; margin-bottom: 5px; font-size: 12px; }
	.grand-total { font-size: 16px; font-weight: bold; border-top: 2px solid #000; padding-top: 10px; margin-top: 10px; }
	.payments { margin-top: 15px; border-top: 1px dashed #000; padding-top: 10px; }
	.payment-row { display: flex; justify-content: space-between; margin-bottom: 3px; font-size: 11px; }
	.total-paid { font-weight: bold; border-top: 1px solid #000; padding-top: 5px; margin-top: 5px; }
	.outstanding-row {
		display: flex; justify-content: space-between; font-size: 13px; font-weight: bold;
		border: 1px solid #000; padding: 8px; margin-top: 8px; border-radius: 4px;
	}
	.offline-badge {
		text-align: center; font-size: 11px; font-weight: bold;
		border: 1px dashed #000; padding: 4px; margin-bottom: 10px;
	}
	.footer { text-align: center; margin-top: 20px; padding-top: 10px; border-top: 2px dashed #000; font-size: 11px; }
	.pn-receipt-tail { background: #fff; }
	@media print {
		@page { size: ${mm}mm auto; margin: 0; }
		body { width: ${mm}mm; padding: 5mm; margin: 0; }
		.no-print { display: none; }
	}
`
}

// Preview helper: how many bitmaps to render and at what delay.
export function buildPreviewPlan(copies, copyDelayMs) {
	const n = Math.max(1, Math.min(Number(copies) || 1, 5))
	const d = Math.max(0, Math.min(Number(copyDelayMs) || 0, 10000))
	return { copies: n, copyDelayMs: d }
}

/**
 * Copy labels: the customer copy and the crew copy are otherwise identical
 * bitmaps, so nobody can tell them apart at the counter. Only used when more
 * than one copy is requested — a single receipt needs no label.
 */
export const COPY_LABELS = { 0: "CUSTOMER COPY", 1: "CREW COPY" }

export function copyLabelFor(index, copies) {
	if (!copies || copies < 2) return ""
	return COPY_LABELS[index] || `COPY ${index + 1}`
}

/**
 * Insert a copy label into print HTML as the first printed line.
 *
 * This is prepended as a sibling rather than injected into the markup, so it
 * works for every HTML the transport can be handed: the locally built receipt,
 * a Frappe print format, or the EOD report. The banner uses inline styles
 * because nothing in the receipt stylesheet knows about it.
 */
export function withCopyLabel(html, label) {
	if (!label) return html
	const banner = `<div class="pn-copy-label" style="text-align:center;font-weight:bold;font-size:14px;letter-spacing:1px;padding:4px 0 8px;">${label}</div>`
	return `${banner}${html}`
}

const MAX_COPIES = 5
const MAX_COPY_DELAY_MS = 10000
const DEFAULT_COPIES = 1
const DEFAULT_COPY_DELAY_MS = 800

function clampInt(v, lo, hi, dflt) {
	if (v == null || v === "") return dflt
	const n = Number(v)
	if (!Number.isFinite(n)) return dflt
	return Math.max(lo, Math.min(Math.floor(n), hi))
}

/**
 * Single source of truth for how device localStorage overrides the server
 * (POS Settings) print config. Both the iMin driver and the Direct Print
 * preview call this, so what you preview is exactly what prints.
 *
 * Precedence per value: device key if present (including false / "58mm")
 * wins; only an ABSENT device key falls through to the server value; then the
 * module default. `??` (not `||`) keeps that distinction.
 */
export function resolvePrintConfig(device = {}, server = {}) {
	const paper = device.paper ?? server.paper ?? "58mm"
	const customDots = device.customDots ?? server.customDots ?? undefined
	const cut = device.cut ?? server.cut ?? false
	const copies = clampInt(
		device.copies ?? server.copies ?? DEFAULT_COPIES,
		1,
		MAX_COPIES,
		DEFAULT_COPIES,
	)
	const copyDelayMs = clampInt(
		device.copyDelayMs ?? server.copyDelayMs ?? DEFAULT_COPY_DELAY_MS,
		0,
		MAX_COPY_DELAY_MS,
		DEFAULT_COPY_DELAY_MS,
	)
	const feedDots = clampInt(
		device.feedDots ?? server.feedDots ?? DEFAULT_FEED_DOTS,
		8,
		500,
		DEFAULT_FEED_DOTS,
	)
	const tailDots = clampInt(
		device.tailDots ?? server.tailDots ?? DEFAULT_TAIL_DOTS,
		0,
		MAX_TAIL_DOTS,
		DEFAULT_TAIL_DOTS,
	)
	const rawLabels = device.copyLabels ?? server.copyLabels
	const copyLabels = rawLabels == null ? true : Boolean(rawLabels)

	const dots = dotsForPaper(paper, customDots)
	// Labels only change the bitmap when there is more than one copy; a single
	// receipt is the customer's and needs no banner.
	const useLabels = copyLabels && copies > 1
	const labels = useLabels
		? Array.from({ length: copies }, (_, i) => copyLabelFor(i, copies))
		: []

	return {
		paper,
		customDots,
		cut,
		copies,
		copyDelayMs,
		feedDots,
		tailDots,
		copyLabels,
		dots,
		useLabels,
		labels,
	}
}
