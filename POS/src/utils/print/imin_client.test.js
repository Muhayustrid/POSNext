import { beforeEach, describe, expect, it, vi } from "vitest"

import { createIminDriver } from "./imin_client"

function makeFakePrinter(overrides = {}) {
	return {
		connect: vi.fn().mockResolvedValue(true),
		initPrinter: vi.fn(),
		getPrinterStatus: vi.fn().mockResolvedValue({ value: 0 }),
		setPageFormat: vi.fn(),
		printSingleBitmap: vi.fn().mockResolvedValue(1),
		partialCut: vi.fn(),
		printAndFeedPaper: vi.fn(),
		...overrides,
	}
}

let printer
let driver

beforeEach(() => {
	printer = makeFakePrinter()
	driver = createIminDriver({
		factory: () => printer,
		loadConfig: () => ({ host: "127.0.0.1", paper: "58mm", cut: true }),
	})
})

describe("createIminDriver", () => {
	it("renders to a dot-exact bitmap and prints it", async () => {
		await driver.printHTML("<div>receipt</div>", {
			render: async () => ({
				dataURL: "data:image/png;base64,AAA",
				width: 384,
			}),
		})
		expect(printer.printSingleBitmap).toHaveBeenCalledWith(
			"data:image/png;base64,AAA",
			expect.any(Number),
		)
	})

	it("never feeds paper after the bitmap", async () => {
		await driver.printHTML("<div/>", {
			render: async () => ({ dataURL: "data:,", width: 384 }),
		})
		expect(printer.printAndFeedPaper).not.toHaveBeenCalled()
	})

	it("cuts only when the device config enables it", async () => {
		await driver.printHTML("<div/>", {
			render: async () => ({ dataURL: "x", width: 384 }),
		})
		expect(printer.partialCut).toHaveBeenCalledTimes(1)

		const noCut = createIminDriver({
			factory: () => printer,
			loadConfig: () => ({ paper: "58mm", cut: false }),
		})
		await noCut.printHTML("<div/>", {
			render: async () => ({ dataURL: "x", width: 384 }),
		})
		expect(printer.partialCut).toHaveBeenCalledTimes(1)
	})

	it("waits for status to reach 0 before resolving", async () => {
		printer.getPrinterStatus
			.mockResolvedValueOnce({ value: -1 })
			.mockResolvedValue({ value: 0 })
		await driver.printHTML("<div/>", {
			render: async () => ({ dataURL: "x", width: 384 }),
		})
		expect(printer.getPrinterStatus.mock.calls.length).toBeGreaterThan(1)
		expect(printer.printAndFeedPaper).not.toHaveBeenCalled()
	})

	it("applies the page format for the chosen paper", async () => {
		await driver.printHTML("<div/>", {
			render: async () => ({ dataURL: "x", width: 384 }),
		})
		expect(printer.setPageFormat).toHaveBeenCalledWith(1) // 58mm

		const w80 = createIminDriver({
			factory: () => printer,
			loadConfig: () => ({ paper: "80mm", cut: false }),
		})
		await w80.printHTML("<div/>", {
			render: async () => ({ dataURL: "x", width: 576 }),
		})
		expect(printer.setPageFormat).toHaveBeenCalledWith(0) // 80mm
	})

	it("reports a not-connected error when status never recovers", async () => {
		printer.getPrinterStatus.mockResolvedValue({ value: -1 })
		const stuck = createIminDriver({
			factory: () => printer,
			loadConfig: () => ({ paper: "58mm", cut: false }),
			statusTimeoutMs: 200,
			statusPollMs: 50,
		})
		await expect(
			stuck.printHTML("<div/>", {
				render: async () => ({ dataURL: "x", width: 384 }),
			}),
		).rejects.toThrow(/not connected/i)
	})
})
