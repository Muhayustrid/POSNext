import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/utils/logger", () => ({
	logger: {
		create: () => ({
			debug: vi.fn(),
			warn: vi.fn(),
			info: vi.fn(),
			error: vi.fn(),
		}),
	},
}))

import {
	RAWBT_PACKAGE,
	asciiBytes,
	buildEscPosJob,
	encodeTextOps,
	escposCols,
	bytesToBase64,
	createRawbtDriver,
	escFeed,
	escRaster,
	rawbtIntentURL,
} from "./rawbt_client"

/** RGBA image; `black(x, y)` decides each pixel. */
function image(width, height, black = () => false) {
	const data = new Uint8ClampedArray(width * height * 4)
	for (let y = 0; y < height; y++) {
		for (let x = 0; x < width; x++) {
			const v = black(x, y) ? 0 : 255
			const i = (y * width + x) * 4
			data[i] = data[i + 1] = data[i + 2] = v
			data[i + 3] = 255
		}
	}
	return { width, height, data }
}

beforeEach(() => {
	localStorage.clear()
})

describe("ESC/POS encoding", () => {
	it("packs pixels MSB-first into a GS v 0 raster", () => {
		// 10 px wide -> 2 bytes per row; pixels 0 and 9 black.
		const bytes = escRaster(image(10, 1, (x) => x === 0 || x === 9))
		expect(bytes.slice(0, 8)).toEqual([0x1d, 0x76, 0x30, 0x00, 2, 0, 1, 0])
		expect(bytes.slice(8)).toEqual([0b10000000, 0b01000000])
	})

	it("splits a tall image into bands", () => {
		const bytes = escRaster(image(8, 300))
		// 128 + 128 + 44 rows -> three headers.
		const headers = []
		for (let i = 0; i < bytes.length; ) {
			expect(bytes.slice(i, i + 4)).toEqual([0x1d, 0x76, 0x30, 0x00])
			const rows = bytes[i + 6] | (bytes[i + 7] << 8)
			headers.push(rows)
			i += 8 + rows * 1
		}
		expect(headers).toEqual([128, 128, 44])
	})

	it("treats transparent pixels as paper", () => {
		const img = image(8, 1, () => true)
		img.data[3] = 0 // first pixel transparent
		expect(escRaster(img).slice(8)).toEqual([0b01111111])
	})

	it("splits feeds above 255 dots", () => {
		expect(escFeed(300)).toEqual([0x1b, 0x4a, 255, 0x1b, 0x4a, 45])
		expect(escFeed(0)).toEqual([])
	})

	it("builds one job: init, then raster + feed (+ cut) per sheet", () => {
		const img = image(8, 1)
		const job = Array.from(
			buildEscPosJob([img, img], { feedDots: 10, cut: true }),
		)
		const sheet = [
			0x1d, 0x76, 0x30, 0x00, 1, 0, 1, 0, 0, 0x1b, 0x4a, 10, 0x1d, 0x56, 0x01,
		]
		expect(job).toEqual([0x1b, 0x40, ...sheet, ...sheet])
	})

	it("encodes the RawBT intent URL with a base64 payload", () => {
		const bytes = Uint8Array.from([0x1b, 0x40])
		expect(bytesToBase64(bytes)).toBe("G0A=")
		expect(rawbtIntentURL(bytes)).toBe(
			`intent:base64,G0A=#Intent;scheme=rawbt;package=${RAWBT_PACKAGE};end;`,
		)
	})
})

describe("createRawbtDriver", () => {
	const render = vi.fn(async (html) => ({
		imageData: image(8, html === "crew" ? 2 : 1),
		height: 1,
	}))

	beforeEach(() => render.mockClear())

	it("intent mode is available only on Android", async () => {
		const d = createRawbtDriver({
			mode: () => "intent",
			isAndroid: () => false,
		})
		await expect(d.isAvailable()).resolves.toBe(false)
		expect((await d.getStatus()).ok).toBe(false)
		const a = createRawbtDriver({ mode: () => "intent", isAndroid: () => true })
		await expect(a.isAvailable()).resolves.toBe(true)
	})

	it("websocket mode is available on any OS", async () => {
		const d = createRawbtDriver({
			mode: () => "websocket",
			isAndroid: () => false,
		})
		await expect(d.isAvailable()).resolves.toBe(true)
	})

	it("launches one intent carrying every copy and the crew slip", async () => {
		const launch = vi.fn()
		const d = createRawbtDriver({ mode: () => "intent", launch, render })
		const out = await d.printHTML("receipt", {
			crewHTML: "crew",
			config: { copies: 2, crewSlipEnabled: true, feedDots: 10, cut: false },
		})
		expect(out.copies).toBe(3)
		expect(render).toHaveBeenCalledTimes(2)
		expect(launch).toHaveBeenCalledTimes(1)
		const url = launch.mock.calls[0][0]
		expect(url.startsWith("intent:base64,")).toBe(true)
		const b64 = url.slice("intent:base64,".length, url.indexOf("#Intent"))
		const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))
		// init + 2 receipts (1 row) + crew (2 rows), each followed by a feed.
		expect(bytes.length).toBe(2 + 2 * (8 + 1 + 3) + (8 + 2 + 3))
	})

	it("skips the crew slip on the EOD lane", async () => {
		const launch = vi.fn()
		const d = createRawbtDriver({ mode: () => "intent", launch, render })
		const out = await d.printHTML("eod", {
			kind: "eod",
			crewHTML: "crew",
			config: { crewSlipEnabled: true },
		})
		expect(out.copies).toBe(1)
		expect(render).toHaveBeenCalledTimes(1)
	})

	it("sends the bytes over the websocket in websocket mode", async () => {
		const sendWs = vi.fn().mockResolvedValue(true)
		const launch = vi.fn()
		const d = createRawbtDriver({
			mode: () => "websocket",
			sendWs,
			launch,
			render,
		})
		await d.printHTML("receipt", { config: {} })
		expect(launch).not.toHaveBeenCalled()
		expect(sendWs).toHaveBeenCalledTimes(1)
		expect(sendWs.mock.calls[0][0]).toBeInstanceOf(Uint8Array)
	})

	it("websocket: one send per sheet with head time + copy/crew delay between", async () => {
		const sendWs = vi.fn().mockResolvedValue(true)
		const sleep = vi.fn().mockResolvedValue()
		const d = createRawbtDriver({
			mode: () => "websocket",
			sendWs,
			sleep,
			render,
		})
		const out = await d.printHTML("receipt", {
			crewHTML: "crew",
			config: {
				copies: 2,
				crewSlipEnabled: true,
				copyDelayMs: 800,
				crewDelayMs: 1500,
				printSpeed: 400,
			},
		})
		expect(out.copies).toBe(3)
		expect(sendWs).toHaveBeenCalledTimes(3)
		const h = (await render.mock.results[0].value).imageData.height
		const headMs = Math.round((h / 400) * 1000)
		expect(sleep.mock.calls.map((c) => c[0])).toEqual([
			headMs + 800,
			headMs + 1500,
		])
	})

	it("websocket: a failure after the first sheet is postPrint (no fallback)", async () => {
		const sendWs = vi
			.fn()
			.mockResolvedValueOnce(true)
			.mockRejectedValueOnce(new Error("down"))
		const d = createRawbtDriver({
			mode: () => "websocket",
			sendWs,
			sleep: async () => {},
			render,
		})
		await expect(
			d.printHTML("receipt", { config: { copies: 2 } }),
		).rejects.toMatchObject({ postPrint: true })
	})

	it("rejects (so the transport can fall back) when the websocket fails", async () => {
		const sendWs = vi.fn().mockRejectedValue(new Error("down"))
		const d = createRawbtDriver({ mode: () => "websocket", sendWs, render })
		await expect(d.printHTML("receipt", { config: {} })).rejects.toThrow("down")
	})

	it("reads the hand-over mode from the device config", () => {
		localStorage.setItem(
			"pos_imin_device_config",
			JSON.stringify({ rawbtMode: "websocket", _v: 2 }),
		)
		expect(createRawbtDriver().describe().detail).toContain("40213")
		localStorage.clear()
		expect(createRawbtDriver().describe().detail).not.toContain("40213")
	})
})

describe("text mode", () => {
	const doc = {
		company: "Toko Énak",
		name: "SINV-1",
		currency: "IDR",
		grand_total: 15000,
		items: [{ item_name: "Kopi", qty: 1, rate: 15000, amount: 15000 }],
		payments: [{ mode_of_payment: "Cash", amount: 15000 }],
	}

	it("folds non-ASCII text to single bytes", () => {
		expect(asciiBytes("Énak – ok")).toEqual(
			Array.from("Enak - ok", (c) => c.charCodeAt(0)),
		)
		expect(asciiBytes("日")).toEqual([0x3f])
	})

	it("sizes columns from the font A cell and the side margin", () => {
		expect(escposCols(384, 0).cols).toEqual({ base: 32, big: 32, queue: 16 })
		expect(escposCols(576, 0).cols.base).toBe(48)
		const m = escposCols(384, 24)
		expect(m.cols.base).toBe(28)
		expect(m.padSpaces).toBe(2)
	})

	it("encodes styles only when they change and resets them at the end", () => {
		const bytes = encodeTextOps(
			[
				{ op: "text", text: "A", align: 1, size: "queue", bold: true },
				{ op: "text", text: "B", align: 1, size: "queue", bold: true },
				{ op: "text", text: "C", align: 0, size: "base", bold: false },
			],
			{ padSpaces: 1 },
		)
		expect(bytes).toEqual([
			0x1b, 0x74, 0x00, 0x1b, 0x33, 30, 0x1b, 0x61, 1, 0x1d, 0x21, 0x11, 0x1b,
			0x45, 1, 0x41, 0x0a, 0x42, 0x0a, 0x1b, 0x61, 0, 0x1d, 0x21, 0x00, 0x1b,
			0x45, 0, 0x20, 0x43, 0x0a, 0x1b, 0x32, 0x1b, 0x61, 0, 0x1d, 0x21, 0, 0x1b,
			0x45, 0,
		])
	})

	it("prints the doc as text, without rendering a bitmap", async () => {
		const launch = vi.fn()
		const render = vi.fn()
		const loadLogo = vi.fn().mockResolvedValue(null)
		const d = createRawbtDriver({
			mode: () => "intent",
			launch,
			render,
			loadLogo,
		})
		const out = await d.printHTML("<html/>", {
			receiptDoc: doc,
			config: { printMode: "text", copies: 1, crewSlipEnabled: true },
		})
		expect(render).not.toHaveBeenCalled()
		expect(out.copies).toBe(2)
		const url = launch.mock.calls[0][0]
		const b64 = url.slice("intent:base64,".length, url.indexOf("#Intent"))
		const text = atob(b64)
		expect(text).toContain("Toko Enak")
		expect(text).toContain("Kopi")
		// Far lighter than a bitmap sheet (tens of KB).
		expect(text.length).toBeLessThan(4000)
	})

	it("falls back to the bitmap when no doc came with the print", async () => {
		const launch = vi.fn()
		const render = vi.fn(async () => ({ imageData: image(8, 1), height: 1 }))
		const d = createRawbtDriver({ mode: () => "intent", launch, render })
		await d.printHTML("<html/>", { config: { printMode: "text" } })
		expect(render).toHaveBeenCalledTimes(1)
	})

	it("prints the closing report as text on the EOD lane", async () => {
		const launch = vi.fn()
		const render = vi.fn()
		const d = createRawbtDriver({ mode: () => "intent", launch, render })
		const out = await d.printHTML("", {
			kind: "eod",
			eodDoc: { name: "CLS-1", company: "Toko" },
			config: { eodPrintMode: "text", eodCopies: 2 },
		})
		expect(render).not.toHaveBeenCalled()
		expect(out.copies).toBe(2)
	})
})
