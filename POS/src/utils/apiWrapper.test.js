import { describe, expect, it, vi } from "vitest"

// apiWrapper imports the frappe-ui barrel, which does not resolve under vitest;
// only `call` is used and is irrelevant to the helper under test.
vi.mock("frappe-ui", () => ({ call: vi.fn() }))

import { serverErrorMessage } from "./apiWrapper"

// Shape of the error thrown by frappe-ui's call() (see frappe-ui utils/call.js):
// message is the useless "method exc_type" string; the real, server-translated
// text lives on `messages`.
const frappeError = (messages, message = "Error: /api/method/x ValidationError") => ({
	message,
	exc_type: "ValidationError",
	messages,
})

describe("serverErrorMessage", () => {
	it("returns the server-translated message instead of the generic error string", () => {
		// frappe-ui parses _server_messages JSON before we see it: entries are plain text
		const error = frappeError([
			"QA-SCHEDULE is outside the scheduled shift hours (09:00:00 – 22:00:00). A shift cannot be opened now.",
		])
		const shown = serverErrorMessage(error)
		expect(shown).toContain("outside the scheduled shift hours")
		expect(shown).not.toContain("Error:")
		expect(shown).not.toContain("ValidationError")
	})

	it("joins multiple server messages", () => {
		const error = frappeError(["Pertama", "Kedua"])
		expect(serverErrorMessage(error)).toBe("Pertama Kedua")
	})

	it("drops empty message entries", () => {
		expect(serverErrorMessage(frappeError(["", "Nyata", null]))).toBe("Nyata")
	})

	it("falls back when there are no server messages (network failure)", () => {
		expect(serverErrorMessage(new TypeError("Failed to fetch"))).toBe(
			"Something went wrong. Please try again.",
		)
		expect(serverErrorMessage(undefined)).toBe("Something went wrong. Please try again.")
		expect(serverErrorMessage(frappeError([]))).toBe("Something went wrong. Please try again.")
	})

	it("never leaks the raw endpoint/exception string", () => {
		const error = frappeError([])
		expect(serverErrorMessage(error)).not.toMatch(/api\/method|ValidationError/)
	})
})

describe("humanizeError", async () => {
	const { humanizeError } = await import("./errorHandler")

	it("swaps the 'method ExcType' string for the server's message, keeping lines", () => {
		const error = new Error("pos_next.api.invoices.submit_invoice InsufficientStockError")
		error.messages = ["Roti Tawar: requested 3, available 1 in Toko<br>Susu: requested 2, available 0 in Toko"]
		humanizeError(error)
		expect(error.message).toBe(
			"Roti Tawar: requested 3, available 1 in Toko\nSusu: requested 2, available 0 in Toko"
		)
		expect(error.technicalMessage).toBe("pos_next.api.invoices.submit_invoice InsufficientStockError")
	})

	it("strips markup without executing it", () => {
		const error = new Error("x ValidationError")
		error.messages = ['<img src=x onerror="window.__xss=1"><b>Shift</b> sudah ditutup']
		humanizeError(error)
		expect(error.message).toBe("Shift sudah ditutup")
		expect(window.__xss).toBeUndefined()
	})

	it("leaves network errors and frappe's generic fallback alone", () => {
		const net = new TypeError("Failed to fetch")
		expect(humanizeError(net).message).toBe("Failed to fetch")
		const generic = new Error("x")
		generic.messages = ["Internal Server Error"]
		expect(humanizeError(generic).message).toBe("x")
	})
})
