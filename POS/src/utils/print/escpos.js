/**
 * ESC/POS encoder shared by the drivers that hand raw bytes to a printer
 * (RawBT, iMin Native shell).
 *
 * Two print modes, from the same POS Settings knob as iMin (imin_print_mode /
 * imin_eod_print_mode):
 *   - "bitmap": the same bitmap the iMin bitmap lane prints (one renderer,
 *     so the paper looks the same), encoded as ESC/POS raster (GS v 0).
 *   - "text": the receipt_text layout sent as plain ESC/POS text in the
 *     printer's own font A (12x24 dots); only the small logo is a raster.
 * Text mode needs the invoice / closing doc; a print without one (test page,
 * server-HTML fallback) stays bitmap, the same rule as the iMin driver.
 */
import { logger } from "@/utils/logger"
import { __ } from "@/utils/translation"

import { DOTS_PER_MM } from "./paper"
import { resolvePrintConfig } from "./receipt_layout"
import { binarize, renderHTMLToBitmap } from "./receipt_renderer"
import {
	RECEIPT_LOGO_MM,
	RECEIPT_LOGO_URL,
	buildCrewSlipTextJob,
	buildEodTextJob,
	buildReceiptTextJob,
} from "./receipt_text"

const log = logger.create("EscPos")

// Rows per GS v 0 command. Many cheap heads cap one raster command; small
// bands keep every printer happy at no cost in speed.
const RASTER_BAND_ROWS = 128

const ESC = 0x1b
const GS = 0x1d

/** ESC @: reset the printer to its defaults. */
export function escInit() {
	return [ESC, 0x40]
}

/** ESC J n, split so any length fits the 0..255 parameter. */
export function escFeed(dots) {
	const out = []
	let left = Math.max(0, Math.round(Number(dots) || 0))
	while (left > 0) {
		const n = Math.min(255, left)
		out.push(ESC, 0x4a, n)
		left -= n
	}
	return out
}

/** GS V 1: partial cut (ignored by printers without a cutter). */
export function escCut() {
	return [GS, 0x56, 0x01]
}

/**
 * ESC/POS raster bytes (GS v 0, normal density) for a binarized RGBA image.
 * A pixel is black when its red channel is below 128 — binarize() already
 * forced every pixel to pure black or white, and the renderer paints on
 * white, so transparent areas never occur.
 *
 * @param {{width:number, height:number, data:Uint8ClampedArray}} image
 * @returns {number[]}
 */
export function escRaster(image) {
	const { width, height, data } = image || {}
	if (!(width > 0) || !(height > 0) || !data) return []
	const bytesPerRow = Math.ceil(width / 8)
	const out = []
	for (let top = 0; top < height; top += RASTER_BAND_ROWS) {
		const rows = Math.min(RASTER_BAND_ROWS, height - top)
		out.push(
			GS,
			0x76,
			0x30,
			0x00,
			bytesPerRow & 0xff,
			(bytesPerRow >> 8) & 0xff,
			rows & 0xff,
			(rows >> 8) & 0xff,
		)
		for (let y = top; y < top + rows; y++) {
			const rowStart = y * width * 4
			for (let bx = 0; bx < bytesPerRow; bx++) {
				let byte = 0
				for (let bit = 0; bit < 8; bit++) {
					const x = bx * 8 + bit
					if (x >= width) continue
					const i = rowStart + x * 4
					// Alpha 0 is paper, whatever the colour channels hold.
					if (data[i + 3] !== 0 && data[i] < 128) byte |= 0x80 >> bit
				}
				out.push(byte)
			}
		}
	}
	return out
}

/**
 * One ESC/POS job for every sheet: init, then per sheet its bytes + feed
 * (+ cut). The feed is what pushes the receipt past the tear bar, exactly as
 * on the iMin lane.
 *
 * @param {number[][]} sheets - encoded sheet bodies
 */
export function buildEscPosSheets(sheets, { feedDots = 0, cut = false } = {}) {
	const out = escInit()
	for (const body of sheets) {
		for (const b of body) out.push(b)
		out.push(...escFeed(feedDots))
		if (cut) out.push(...escCut())
	}
	return Uint8Array.from(out)
}

/** Bitmap job: one raster sheet per image. */
export function buildEscPosJob(images, opts = {}) {
	return buildEscPosSheets(images.map(escRaster), opts)
}

// ---------------------------------------------------------------------------
// Text mode
// ---------------------------------------------------------------------------

/** Font A cell width in dots: 32 columns on 58mm, 48 on 80mm. */
export const FONT_A_DOTS = 12
/** Default ESC/POS line pitch (ESC 2) for font A, in dots. */
const DEFAULT_LINE_DOTS = 30

// GS ! n per size key: base normal; big double height (same columns, so the
// crew slip's item lines keep the receipt's width); queue double both ways.
const SIZE_MODE = { base: 0x00, big: 0x01, queue: 0x11 }

const ASCII_FOLD = {
	"‘": "'",
	"’": "'",
	"“": '"',
	"”": '"',
	"–": "-",
	"—": "-",
	"…": "...",
	" ": " ",
}
const FOLD_RE = /[‘’“”–—… ]/g
// Combining diacritical marks left behind by NFD ("É" -> "E" + U+0301).
const COMBINING_RE = /\p{M}/gu

/**
 * Text as single-byte ASCII: the job selects code page 437 (ESC t 0), so
 * accents are folded and anything else becomes "?" instead of mojibake.
 */
export function asciiBytes(text) {
	const folded = String(text ?? "")
		.replace(FOLD_RE, (c) => ASCII_FOLD[c])
		.normalize("NFD")
		.replace(COMBINING_RE, "")
	const out = []
	for (const ch of folded) {
		const code = ch.codePointAt(0)
		out.push(code >= 0x20 && code < 0x7f ? code : 0x3f)
	}
	return out
}

/** Columns per size key for the usable width (paper minus both margins). */
export function escposCols(dots, sideMarginDots = 0) {
	const side = Math.max(0, sideMarginDots || 0)
	const usable = Math.max(120, dots - 2 * side)
	const base = Math.max(16, Math.floor(usable / FONT_A_DOTS))
	return {
		cols: { base, big: base, queue: Math.max(8, Math.floor(base / 2)) },
		padSpaces: Math.round(side / FONT_A_DOTS),
	}
}

/**
 * Encode receipt_text ops as ESC/POS. Style commands are only sent when they
 * change; every setting is reset at the end of the sheet.
 *
 * @param {object[]} ops - from buildReceiptTextJob / buildCrewSlipTextJob / buildEodTextJob
 * @param {object} [o]
 * @param {number} [o.padSpaces] - left padding of left-aligned lines (side margin)
 * @param {number} [o.lineSpacing] - percent of the default line pitch
 * @param {{width:number,height:number,data:Uint8ClampedArray}|null} [o.logo]
 * @returns {number[]}
 */
export function encodeTextOps(
	ops,
	{ padSpaces = 0, lineSpacing = 100, logo = null } = {},
) {
	const pitch = Math.max(
		1,
		Math.min(
			255,
			Math.round((DEFAULT_LINE_DOTS * (Number(lineSpacing) || 100)) / 100),
		),
	)
	// ESC t 0: code page 437; ESC 3 n: line pitch.
	const out = [ESC, 0x74, 0x00, ESC, 0x33, pitch]
	const pad = padSpaces > 0 ? " ".repeat(padSpaces) : ""
	let align = 0
	let size = 0
	let bold = 0
	for (const o of ops) {
		if (o.op === "text") {
			const a = Number(o.align) || 0
			if (a !== align) {
				align = a
				out.push(ESC, 0x61, a)
			}
			const sz = SIZE_MODE[o.size] ?? 0
			if (sz !== size) {
				size = sz
				out.push(GS, 0x21, sz)
			}
			const b = o.bold ? 1 : 0
			if (b !== bold) {
				bold = b
				out.push(ESC, 0x45, b)
			}
			const line = a === 0 && pad && o.text ? pad + o.text : o.text
			for (const byte of asciiBytes(line)) out.push(byte)
			out.push(0x0a)
		} else if (o.op === "feed") {
			out.push(...escFeed(o.dots))
		} else if (o.op === "logo" && logo) {
			// Already centred on a full-width canvas: GS v 0 ignores ESC a on
			// many heads, so alignment is baked into the raster instead.
			for (const byte of escRaster(logo)) out.push(byte)
		}
	}
	// ESC 2 default pitch, left align, normal size, bold off.
	out.push(ESC, 0x32, ESC, 0x61, 0, GS, 0x21, 0, ESC, 0x45, 0)
	return out
}

/**
 * The receipt logo as a binarized raster, centred on a full-width canvas.
 * Cached per size for the session; null when the image cannot be loaded (the
 * receipt then prints without it, as on the iMin text lane).
 */
const _logoCache = new Map()
export function loadLogoRaster(widthDots, paperDots) {
	const key = `${widthDots}:${paperDots}`
	if (_logoCache.has(key)) return _logoCache.get(key)
	const job = (async () => {
		const img = new Image()
		img.src = RECEIPT_LOGO_URL
		await img.decode()
		const height = Math.max(
			1,
			Math.round((img.naturalHeight / img.naturalWidth) * widthDots),
		)
		const canvas = document.createElement("canvas")
		canvas.width = Math.max(widthDots, paperDots)
		canvas.height = height
		const ctx = canvas.getContext("2d")
		ctx.fillStyle = "#ffffff"
		ctx.fillRect(0, 0, canvas.width, height)
		ctx.drawImage(
			img,
			Math.floor((canvas.width - widthDots) / 2),
			0,
			widthDots,
			height,
		)
		const data = ctx.getImageData(0, 0, canvas.width, height)
		binarize(data)
		return data
	})().catch((err) => {
		log.warn("receipt logo unavailable, printing without it:", err?.message)
		_logoCache.delete(key)
		return null
	})
	_logoCache.set(key, job)
	return job
}

/** Base64 of raw bytes, chunked so large receipts do not blow the stack. */
export function bytesToBase64(bytes) {
	let bin = ""
	const CHUNK = 0x8000
	for (let i = 0; i < bytes.length; i += CHUNK) {
		bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK))
	}
	return btoa(bin)
}

// ---------------------------------------------------------------------------
// One print -> one ESC/POS job
// ---------------------------------------------------------------------------

async function bitmapSheets(html, opts, r, isEod, render) {
	if (!html) throw new Error(__("Nothing to print"))
	const renderOpts = {
		paper: r.paper,
		customDots: r.customDots,
		tailDots: r.tailDots,
		fontScale: r.fontScale,
		lineSpacing: r.lineSpacing,
		sideMarginDots: r.sideMarginDots,
		topMarginDots: r.topMarginDots,
		queueGapDots: r.queueGapDots,
	}
	const crewApplies = !isEod && r.crewSlipEnabled && Boolean(opts.crewHTML)
	const [receipt, crew] = await Promise.all([
		render(html, renderOpts),
		crewApplies
			? render(opts.crewHTML, { ...renderOpts, fontScale: r.crewFontScale })
			: null,
	])
	const body = escRaster(receipt.imageData)
	const h = receipt.imageData?.height || 0
	const sheets = Array.from({ length: r.copies }, () => ({
		body,
		kind: "receipt",
		heightDots: h,
	}))
	if (crew) {
		sheets.push({
			body: escRaster(crew.imageData),
			kind: "crew",
			heightDots: crew.imageData?.height || 0,
		})
	}
	return sheets
}

/** Paper a text sheet uses, for the inter-sheet pause estimate only. */
function textHeightDots(ops, lineSpacing, logo) {
	const pitch = (DEFAULT_LINE_DOTS * (Number(lineSpacing) || 100)) / 100
	let h = 0
	for (const o of ops) {
		if (o.op === "text") h += o.size === "base" ? pitch : pitch * 2
		else if (o.op === "feed") h += Number(o.dots) || 0
		else if (o.op === "logo" && logo) h += logo.height || 0
	}
	return Math.round(h)
}

async function textSheets(r, { isEod, eodDoc, receiptDoc }, loadLogo) {
	const { cols, padSpaces } = escposCols(r.dots, r.sideMarginDots)
	const wrap = (ops) => {
		let out = ops
		if (r.topMarginDots > 0) {
			out = [{ op: "feed", dots: r.topMarginDots }, ...out]
		}
		// The tail is white space inside a bitmap; text has no raster, so
		// it becomes a feed at the end of the sheet (as on iMin).
		if (r.tailDots > 0) out = [...out, { op: "feed", dots: r.tailDots }]
		return out
	}
	const enc = { padSpaces, lineSpacing: r.lineSpacing }
	const sheet = (ops, kind, logo = null) => ({
		body: encodeTextOps(ops, { ...enc, logo }),
		kind,
		heightDots: textHeightDots(ops, r.lineSpacing, logo),
	})
	if (isEod) {
		const eod = sheet(wrap(buildEodTextJob(eodDoc, { cols: cols.base })), "eod")
		return Array.from({ length: r.copies }, () => eod)
	}
	const logoWidth = Math.min(r.dots, RECEIPT_LOGO_MM * DOTS_PER_MM)
	const logo = await loadLogo(logoWidth, r.dots)
	const receipt = sheet(
		wrap(
			buildReceiptTextJob(receiptDoc, {
				cols,
				queueGapDots: r.queueGapDots,
			}),
		),
		"receipt",
		logo,
	)
	const sheets = Array.from({ length: r.copies }, () => receipt)
	if (r.crewSlipEnabled) {
		sheets.push(
			sheet(
				wrap(
					buildCrewSlipTextJob(receiptDoc, {
						cols,
						queueGapDots: r.queueGapDots,
					}),
				),
				"crew",
			),
		)
	}
	return sheets
}

/**
 * Build the whole ESC/POS job for one driver.printHTML call: every copy and
 * the crew slip, each followed by the feed (+ cut).
 *
 * @param {string} html
 * @param {object} opts - driver.printHTML opts (config, kind, doc, receiptDoc, eodDoc, crewHTML)
 * @param {object} [deps]
 * @param {(html, opts) => Promise<{imageData:ImageData}>} [deps.render]
 * @param {(widthDots:number, paperDots:number) => Promise<ImageData|null>} [deps.loadLogo]
 * @returns {Promise<{bytes:Uint8Array, r:object, textMode:boolean, sheets:number,
 *   parts:{bytes:Uint8Array, kind:string, heightDots:number}[]}>}
 *   `bytes` is the whole job; `parts` is the same job split per sheet (each
 *   a standalone job) for drivers that pause between sheets.
 */
export async function buildEscPosPrint(html, opts = {}, deps = {}) {
	const render = deps.render || ((h, o) => renderHTMLToBitmap(h, o))
	const loadLogo = deps.loadLogo || loadLogoRaster
	const r = resolvePrintConfig({}, opts.config || {}, { kind: opts.kind })
	const isEod = opts.kind === "eod"
	const eodDoc = opts.eodDoc || (isEod ? opts.doc : null)
	const receiptDoc = opts.receiptDoc || (!isEod ? opts.doc : null)
	const textMode =
		r.printMode === "text" && Boolean(isEod ? eodDoc : receiptDoc)

	const sheets = textMode
		? await textSheets(r, { isEod, eodDoc, receiptDoc }, loadLogo)
		: await bitmapSheets(html, opts, r, isEod, render)
	const tail = { feedDots: r.feedDots, cut: r.cut }
	const bytes = buildEscPosSheets(
		sheets.map((s) => s.body),
		tail,
	)
	const parts = sheets.map((s) => ({
		bytes: buildEscPosSheets([s.body], tail),
		kind: s.kind,
		heightDots: s.heightDots,
	}))
	return { bytes, r, textMode, sheets: sheets.length, parts }
}
