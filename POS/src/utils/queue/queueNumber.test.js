/**
 * @vitest-environment jsdom
 */
import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/utils/apiWrapper", () => ({ call: vi.fn() }))
vi.mock("@/utils/logger", () => ({
	logger: { create: () => ({ warn: vi.fn(), info: vi.fn(), error: vi.fn() }) },
}))

import { call } from "@/utils/apiWrapper"
import { acquireQueueNumber, formatQueueNumber, readQueueCache } from "./queueNumber"

// Same idiom printInvoice.test.js uses: the module-level `call` mock, wrapped
// in the tiny helpers the brief's skeletons refer to.
const mockCall = {
	resolveWith: (value) => call.mockResolvedValue(value),
	rejectWith: (err) => call.mockRejectedValue(err),
}
const today = () => new Date().toISOString().slice(0, 10)

beforeEach(() => {
	localStorage.clear()
	vi.clearAllMocks()
})

describe("queueNumber", () => {
	it("formats with 3-digit zero pad and beyond", () => {
		expect(formatQueueNumber(7)).toBe("007")
		expect(formatQueueNumber(1000)).toBe("1000")
		// Edge cases: non-positive or non-numeric input prints nothing.
		expect(formatQueueNumber(0)).toBe("")
		expect(formatQueueNumber(-1)).toBe("")
		expect(formatQueueNumber(NaN)).toBe("")
		expect(formatQueueNumber("7")).toBe("007")
	})

	it("uses the server number online and caches it", async () => {
		mockCall.resolveWith({ enabled: true, queue_number: 12, date: "2026-09-06" })
		const out = await acquireQueueNumber({ company: "C", posProfile: "P" })
		expect(out.queue_number).toBe(12)
		expect(readQueueCache("C")).toEqual({ date: "2026-09-06", number: 12 })
		expect(call).toHaveBeenCalledWith("pos_next.api.queue.get_next_queue_number", {
			pos_profile: "P",
		})
	})

	it("returns null when the company disabled the queue", async () => {
		mockCall.resolveWith({ enabled: false })
		expect(await acquireQueueNumber({ company: "C", posProfile: "P" })).toBeNull()
		expect(readQueueCache("C")).toBeNull()
	})

	it("continues locally offline from today's cache", async () => {
		localStorage.setItem(
			"pos_queue_last::C",
			JSON.stringify({ date: today(), number: 41 }),
		)
		const out = await acquireQueueNumber({ company: "C", posProfile: "P", offline: true })
		expect(out.queue_number).toBe(42)
		// Every allocation rewrites the cache so the next sale continues after it.
		expect(readQueueCache("C")).toEqual({ date: today(), number: 42 })
	})

	it("restarts at 1 when the cached date is stale or absent", async () => {
		localStorage.setItem(
			"pos_queue_last::C",
			JSON.stringify({ date: "2000-01-01", number: 41 }),
		)
		const out = await acquireQueueNumber({ company: "C", posProfile: "P", offline: true })
		expect(out.queue_number).toBe(1)
		// Absent cache starts at 1 too.
		localStorage.clear()
		const fresh = await acquireQueueNumber({ company: "C", posProfile: "P", offline: true })
		expect(fresh.queue_number).toBe(1)
	})

	it("falls back to local continuation when the API call fails online", async () => {
		mockCall.rejectWith(new Error("net"))
		localStorage.setItem(
			"pos_queue_last::C",
			JSON.stringify({ date: today(), number: 5 }),
		)
		const out = await acquireQueueNumber({ company: "C", posProfile: "P" })
		expect(out.queue_number).toBe(6)
		expect(readQueueCache("C")).toEqual({ date: today(), number: 6 })
	})
})
