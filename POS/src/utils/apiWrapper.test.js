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
