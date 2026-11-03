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
	buildEscPosJob,
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
