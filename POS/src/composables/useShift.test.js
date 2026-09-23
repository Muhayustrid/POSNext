/**
 * @vitest-environment jsdom
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

// Controllable createResource double: the handler opts are kept so tests
// fire onSuccess/onError directly (same pattern as ShiftDashboard.test.js).
const resources = vi.hoisted(() => ({ instances: [] }))

vi.mock("frappe-ui", async () => {
	const { reactive } = await import("vue")
	return {
		createResource: (opts) => {
			const r = reactive({ loading: false, data: null, error: null, reload: vi.fn() })
			resources.instances.push({ url: opts.url, opts, resource: r })
			return r
		},
	}
})

import { shiftState, useShift } from "./useShift"

const CHECK_URL = "pos_next.api.shifts.check_opening_shift"

const setOnline = (online) => {
	Object.defineProperty(navigator, "onLine", { configurable: true, value: online })
}

describe("useShift checkOpeningShift error fallback", () => {
	beforeEach(() => {
		localStorage.clear()
		resources.instances.length = 0
		vi.spyOn(console, "error").mockImplementation(() => {})
		shiftState.value = {
			pos_opening_shift: null,
			pos_profile: null,
			company: null,
			isOpen: false,
			_initialElapsedMs: 0,
			_receivedAt: 0,
			_serverNowMs: 0,
		}
	})

	afterEach(() => {
		vi.restoreAllMocks()
		setOnline(true)
	})

	it("online error clears the cache and leaves the shift closed", () => {
		localStorage.setItem(
			"pos_shift_data",
			JSON.stringify({ pos_opening_shift: { name: "STALE-SHIFT" } })
		)
		setOnline(true)

		const { checkOpeningShift } = useShift()
		const check = resources.instances.find((r) => r.url === CHECK_URL)
		check.opts.onError(new Error("500"))

		expect(localStorage.getItem("pos_shift_data")).toBe(null)
		expect(shiftState.value.isOpen).toBe(false)
		expect(shiftState.value.pos_opening_shift).toBe(null)
	})

	it("offline error falls back to the cached shift", () => {
		localStorage.setItem(
			"pos_shift_data",
			JSON.stringify({
				pos_opening_shift: { name: "CACHED-SHIFT" },
				pos_profile: { name: "P1" },
				company: { name: "C1" },
			})
		)
		setOnline(false)

		const { checkOpeningShift } = useShift()
		const check = resources.instances.find((r) => r.url === CHECK_URL)
		check.opts.onError(new Error("network down"))

		expect(shiftState.value.isOpen).toBe(true)
		expect(shiftState.value.pos_opening_shift.name).toBe("CACHED-SHIFT")
		expect(localStorage.getItem("pos_shift_data")).not.toBe(null)
	})
})
