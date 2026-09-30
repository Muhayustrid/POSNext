import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { createOptimizedClickHandler } from "./lowEndOptimizations"

const touchEvent = () => ({
	touches: [{ clientX: 0, clientY: 0 }],
	currentTarget: null,
	preventDefault: vi.fn(),
	stopPropagation: vi.fn(),
})

const clickEvent = () => ({
	preventDefault: vi.fn(),
	stopPropagation: vi.fn(),
})

beforeEach(() => {
	vi.useFakeTimers()
})

afterEach(() => {
	vi.unstubAllGlobals()
	vi.useRealTimers()
})

describe("createOptimizedClickHandler rAF/timeout race", () => {
	it("runs the handler exactly once when rAF is healthy (no setTimeout double-fire)", () => {
		const handler = vi.fn()
		// Healthy rAF: invoke the callback synchronously.
		vi.stubGlobal("requestAnimationFrame", vi.fn((cb) => cb(0)))
		vi.stubGlobal("cancelAnimationFrame", vi.fn())

		const handlers = createOptimizedClickHandler(handler)
		handlers.click(clickEvent())
		vi.advanceTimersByTime(1000)

		expect(handler).toHaveBeenCalledTimes(1)
	})

	it("falls back to setTimeout exactly once when rAF is stalled (never fires)", () => {
		const handler = vi.fn()
		// Stalled compositor: rAF never invokes its callback.
		vi.stubGlobal("requestAnimationFrame", vi.fn(() => 1))
		vi.stubGlobal("cancelAnimationFrame", vi.fn())

		const handlers = createOptimizedClickHandler(handler)
		handlers.click(clickEvent())

		vi.advanceTimersByTime(31)
		expect(handler).not.toHaveBeenCalled()

		vi.advanceTimersByTime(1)
		expect(handler).toHaveBeenCalledTimes(1)

		vi.advanceTimersByTime(1000)
		expect(handler).toHaveBeenCalledTimes(1)
	})

	it("runs the touch path exactly once when rAF is stalled", () => {
		const handler = vi.fn()
		vi.stubGlobal("requestAnimationFrame", vi.fn(() => 1))
		vi.stubGlobal("cancelAnimationFrame", vi.fn())

		const handlers = createOptimizedClickHandler(handler)
		handlers.touchstart(touchEvent())
		handlers.touchend(touchEvent())

		vi.advanceTimersByTime(32)
		expect(handler).toHaveBeenCalledTimes(1)
	})
})

describe("createOptimizedClickHandler ghost-click guard", () => {
	it("ignores a click within 500ms of touchend, accepts one after", () => {
		const handler = vi.fn()
		// Sync rAF keeps the race executor deterministic under fake timers.
		vi.stubGlobal("requestAnimationFrame", vi.fn((cb) => cb(0)))
		vi.stubGlobal("cancelAnimationFrame", vi.fn())

		const handlers = createOptimizedClickHandler(handler)

		handlers.touchstart(touchEvent())
		handlers.touchend(touchEvent())
		expect(handler).toHaveBeenCalledTimes(1)

		// Ghost click right after touch: swallowed.
		handlers.click(clickEvent())
		expect(handler).toHaveBeenCalledTimes(1)

		// After the 500ms ghost window: real click goes through.
		vi.advanceTimersByTime(500)
		handlers.click(clickEvent())
		expect(handler).toHaveBeenCalledTimes(2)
	})
})

describe("createOptimizedClickHandler timer hygiene", () => {
	it("does not create a setInterval (or any timer) per handler instance", () => {
		const setIntervalSpy = vi.spyOn(globalThis, "setInterval")
		vi.stubGlobal("requestAnimationFrame", vi.fn(() => 1))
		vi.stubGlobal("cancelAnimationFrame", vi.fn())

		for (let i = 0; i < 50; i++) {
			createOptimizedClickHandler(vi.fn())
		}

		expect(setIntervalSpy).not.toHaveBeenCalled()
		// Creating a handler must not schedule anything; only invoking
		// touch/click schedules the one-shot fallback timer.
		expect(vi.getTimerCount()).toBe(0)
	})
})
