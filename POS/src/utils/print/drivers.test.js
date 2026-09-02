import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/utils/qzTray", () => ({
	connect: vi.fn(),
	printHTML: vi.fn(),
}))

import * as qzTray from "@/utils/qzTray"
import { createBrowserDriver } from "./browser_client"
import { createQzDriver } from "./qz_client"

beforeEach(() => {
	vi.clearAllMocks()
})

describe("createQzDriver", () => {
	it("is available only when connect succeeds", async () => {
		qzTray.connect.mockResolvedValue(true)
		await expect(createQzDriver().isAvailable()).resolves.toBe(true)
		qzTray.connect.mockResolvedValue(false)
		await expect(createQzDriver().isAvailable()).resolves.toBe(false)
	})

	it("delegates printHTML to qzTray", async () => {
		qzTray.printHTML.mockResolvedValue(true)
		await expect(createQzDriver().printHTML("<html/>")).resolves.toBe(true)
		// Transport contract: every driver receives (html, opts). The adapter
		// pulls printerName out of opts and forwards the rest as options.
		expect(qzTray.printHTML).toHaveBeenCalledWith("<html/>", undefined, {})
	})
})

describe("createBrowserDriver", () => {
	it("is always available", async () => {
		await expect(createBrowserDriver().isAvailable()).resolves.toBe(true)
	})

	it("reports describe metadata", () => {
		expect(createBrowserDriver().describe().id).toBe("browser")
	})
})
