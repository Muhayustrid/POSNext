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

import { escFeed } from "./escpos"
import { createNativeDriver } from "./native_client"
import { resolvePrintConfig } from "./receipt_layout"

/**
 * Fake PosNextNative: `handlers[cmd](msg)` returns the reply body (or
 * undefined for silence). Replies are async, like the real bridge.
 */
function fakeBridge(handlers = {}) {
	const listeners = []
	const sent = []
	return {
		sent,
		addEventListener(type, fn) {
			if (type === "message") listeners.push(fn)
		},
		postMessage(raw) {
			const msg = JSON.parse(raw)
			sent.push(msg)
			const h = handlers[msg.cmd]
			const body = h ? h(msg) : undefined
			if (body === undefined) return
			setTimeout(() => {
				for (const fn of listeners)
					fn({ data: JSON.stringify({ id: msg.id, ...body }) })
			}, 0)
		},
	}
}

const HELLO = {
	ok: true,
	app: "posnext-shell",
	version: "1.0.0",
	sdk: "V2.0.0.19",
	model: "Swan 1",
	android: "13",
}
const okHandlers = (over = {}) => ({
	hello: () => HELLO,
	status: () => ({ ok: true, code: 0 }),
	printRaw: () => ({ ok: true }),
	...over,
})

const image = (w, h) => ({
	width: w,
	height: h,
	data: new Uint8ClampedArray(w * h * 4).fill(255),
})
const render = vi.fn(async (html) => ({
	imageData: image(8, html === "crew" ? 2 : 1),
}))
const fast = { hello: 50, status: 50, print: 50 }

function driver(bridge, extra = {}) {
	return createNativeDriver({
		bridge: () => bridge,
		render,
		loadLogo: async () => null,
		timeouts: fast,
		...extra,
	})
}

/** Decoded bytes of the last printRaw. */
function lastJob(bridge) {
	const job = bridge.sent.filter((m) => m.cmd === "printRaw").pop()
	return atob(job.data)
}

const doc = {
	company: "Toko",
	name: "SINV-1",
	currency: "IDR",
	grand_total: 15000,
	items: [{ item_name: "Kopi", qty: 1, rate: 15000, amount: 15000 }],
	payments: [{ mode_of_payment: "Cash", amount: 15000 }],
}

beforeEach(() => render.mockClear())

describe("createNativeDriver: bridge", () => {
	it("is unavailable without the bridge", async () => {
		const d = createNativeDriver({ bridge: () => null })
		await expect(d.isAvailable()).resolves.toBe(false)
		expect((await d.getStatus()).ok).toBe(false)
	})

	it("is available once hello answers, and caches it", async () => {
		const b = fakeBridge(okHandlers())
		const d = driver(b)
		await expect(d.isAvailable()).resolves.toBe(true)
		await expect(d.isAvailable()).resolves.toBe(true)
		expect(b.sent.filter((m) => m.cmd === "hello")).toHaveLength(1)
		expect(d.describe()).toMatchObject({ id: "imin2" })
		expect(d.describe().detail).toContain("V2.0.0.19")
		expect(d.info().model).toBe("Swan 1")
	})

	it("is unavailable when hello times out", async () => {
		const d = driver(fakeBridge(okHandlers({ hello: () => undefined })))
		await expect(d.isAvailable()).resolves.toBe(false)
	})

	it("reports printer faults from status", async () => {
		const d = driver(
			fakeBridge(okHandlers({ status: () => ({ ok: false, code: 7 }) })),
		)
		const s = await d.getStatus()
		expect(s).toMatchObject({ ok: false, code: 7 })
		expect(s.message).toBe("Printer out of paper")
	})
})

describe("createNativeDriver: printing", () => {
	it("sends one printRaw with every copy and the crew slip (bitmap)", async () => {
		const b = fakeBridge(okHandlers())
		const out = await driver(b).printHTML("receipt", {
			crewHTML: "crew",
			config: { copies: 2, crewSlipEnabled: true, feedDots: 0 },
		})
		expect(out.copies).toBe(3)
		expect(render).toHaveBeenCalledTimes(2)
		// init + 2 x (8 header + 1 row + feed) + (8 header + 2 rows + feed)
		const feed = escFeed(resolvePrintConfig({}, { feedDots: 0 }).feedDots)
		expect(lastJob(b).length).toBe(2 + 2 * (9 + feed.length) + 10 + feed.length)
	})

	it("prints the receipt doc as text without rendering", async () => {
		const b = fakeBridge(okHandlers())
		await driver(b).printHTML("<html/>", {
			receiptDoc: doc,
			config: { printMode: "text", crewSlipEnabled: true },
		})
		expect(render).not.toHaveBeenCalled()
		expect(lastJob(b)).toContain("Kopi")
	})

	it("prints the closing report as text on the EOD lane", async () => {
		const b = fakeBridge(okHandlers())
		const out = await driver(b).printHTML("", {
			kind: "eod",
			eodDoc: { name: "CLS-1", company: "Toko" },
			config: { eodPrintMode: "text", eodCopies: 2 },
		})
		expect(render).not.toHaveBeenCalled()
		expect(out.copies).toBe(2)
		expect(lastJob(b)).toContain("Toko")
	})

	it("refuses before sending when the cover is open (plain error)", async () => {
		const b = fakeBridge(okHandlers({ status: () => ({ ok: false, code: 3 }) }))
		const err = await driver(b)
			.printHTML("receipt", { config: {} })
			.catch((e) => e)
		expect(err.message).toBe("Printer cover is open")
		expect(err.postPrint).toBeUndefined()
		expect(b.sent.some((m) => m.cmd === "printRaw")).toBe(false)
	})

	it("rejects plainly when the app refused the job", async () => {
		const b = fakeBridge(
			okHandlers({ printRaw: () => ({ ok: false, message: "bad data" }) }),
		)
		const err = await driver(b)
			.printHTML("receipt", { config: {} })
			.catch((e) => e)
		expect(err.message).toBe("bad data")
		expect(err.postPrint).toBeUndefined()
	})

	it("marks postPrint when the job failed after paper came out", async () => {
		const b = fakeBridge(
			okHandlers({
				printRaw: () => ({ ok: false, code: 7, printed: true }),
			}),
		)
		const err = await driver(b)
			.printHTML("receipt", { config: {} })
			.catch((e) => e)
		expect(err.message).toBe("Printer out of paper")
		expect(err.postPrint).toBe(true)
	})

	it("marks postPrint when printRaw never answers", async () => {
		const b = fakeBridge(okHandlers({ printRaw: () => undefined }))
		const err = await driver(b)
			.printHTML("receipt", { config: {} })
			.catch((e) => e)
		expect(err.postPrint).toBe(true)
	})
})
