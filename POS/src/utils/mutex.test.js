// COR-FE-07: a mutex timeout is a signal to the CALLER only. The lock must
// stay held until the running fn settles; releasing it at timeout let a
// second sync loop start in parallel with the one still running.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { CoalescingMutex, QueuedMutex } from "./mutex"

beforeEach(() => {
	vi.useFakeTimers()
})

afterEach(() => {
	vi.useRealTimers()
})

describe("CoalescingMutex timeout keeps the lock (COR-FE-07)", () => {
	it("does not start the next fn while the timed-out one is still running", async () => {
		const mutex = new CoalescingMutex({ timeout: 50, name: "Sync" })
		let releaseFirst
		const firstFn = vi.fn(
			() =>
				new Promise((resolve) => {
					releaseFirst = resolve
				}),
		)
		const secondFn = vi.fn(async () => "second")

		const firstCall = mutex.withLock(firstFn)
		// Attach the rejection handler before the timer fires so the
		// rejection is never unhandled.
		const firstFailure = expect(firstCall).rejects.toThrow("timed out")
		await vi.advanceTimersByTimeAsync(50)
		await firstFailure

		// Immediately re-acquiring must NOT start the second fn: the lock is
		// still held by the first fn that never stopped running.
		const secondCall = mutex.withLock(secondFn)
		await vi.advanceTimersByTimeAsync(1000)
		expect(secondFn).not.toHaveBeenCalled()
		expect(mutex.isLocked).toBe(true)

		// Once the first fn settles, the second one runs.
		releaseFirst("done")
		await vi.advanceTimersByTimeAsync(0)
		expect(secondFn).toHaveBeenCalledTimes(1)
		await expect(secondCall).resolves.toBe("second")
		expect(mutex.isLocked).toBe(false)
	})

	it("still resolves normally when the fn finishes before the timeout", async () => {
		const mutex = new CoalescingMutex({ timeout: 1000, name: "Sync" })
		const fn = vi.fn(async () => "ok")

		await expect(mutex.withLock(fn)).resolves.toBe("ok")
		expect(mutex.isLocked).toBe(false)
	})

	it("releases the lock after the fn rejects", async () => {
		const mutex = new CoalescingMutex({ timeout: 1000, name: "Sync" })
		const fn = vi.fn(async () => {
			throw new Error("boom")
		})

		await expect(mutex.withLock(fn)).rejects.toThrow("boom")

		const next = vi.fn(async () => "next")
		await expect(mutex.withLock(next)).resolves.toBe("next")
	})
})

describe("QueuedMutex timeout keeps the queue blocked (COR-FE-07)", () => {
	it("does not run the queued fn until the timed-out one settles", async () => {
		const mutex = new QueuedMutex({ timeout: 50, name: "Queue" })
		let releaseFirst
		const firstFn = vi.fn(
			() =>
				new Promise((resolve) => {
					releaseFirst = resolve
				}),
		)
		const secondFn = vi.fn(async () => "second")

		const firstCall = mutex.withLock(firstFn)
		const secondCall = mutex.withLock(secondFn)
		const firstFailure = expect(firstCall).rejects.toThrow("timed out")

		await vi.advanceTimersByTimeAsync(50)
		await firstFailure

		// The timed-out first job is still running: the queued second job must
		// wait for it instead of overlapping (old code started it here).
		await vi.advanceTimersByTimeAsync(1000)
		expect(secondFn).not.toHaveBeenCalled()
		expect(mutex.isLocked).toBe(true)

		releaseFirst("done")
		await vi.advanceTimersByTimeAsync(0)
		expect(secondFn).toHaveBeenCalledTimes(1)
		await expect(secondCall).resolves.toBe("second")
		expect(mutex.isLocked).toBe(false)
	})

	it("keeps FIFO order and results for jobs that finish in time", async () => {
		const mutex = new QueuedMutex({ timeout: 1000, name: "Queue" })
		const order = []
		const a = mutex.withLock(async () => {
			order.push("a")
			return "A"
		})
		const b = mutex.withLock(async () => {
			order.push("b")
			return "B"
		})

		await vi.advanceTimersByTimeAsync(0)
		await expect(a).resolves.toBe("A")
		await expect(b).resolves.toBe("B")
		expect(order).toEqual(["a", "b"])
	})

	it("keeps the queue alive when fn throws synchronously (review MINOR)", async () => {
		const mutex = new QueuedMutex({ timeout: 1000, name: "Queue" })

		await expect(
			mutex.withLock(() => {
				throw new Error("sync boom")
			}),
		).rejects.toThrow("sync boom")

		// Old code left the queue promise forever pending here: workDone was
		// never wired because the callback escaped before reaching it.
		const after = vi.fn(async () => "still alive")
		await expect(mutex.withLock(after)).resolves.toBe("still alive")
		expect(after).toHaveBeenCalledTimes(1)
	})
})

describe("CoalescingMutex joiner waits in bounded slices (review MINOR)", () => {
	it("keeps waiting without running the joiner fn until the stuck work settles", async () => {
		const mutex = new CoalescingMutex({ timeout: 50, name: "Sync" })
		let releaseWork
		const work = new Promise((resolve) => {
			releaseWork = resolve
		})

		const first = mutex.withLock(() => work)
		// The owner gets its timeout signal at 50ms; attach the expectation
		// before advancing the timers so the rejection is never unhandled.
		const firstFailure = expect(first).rejects.toThrow("timed out")
		const joinerFn = vi.fn(async () => "joined")
		const second = mutex.withLock(joinerFn)

		// Several wait slices expire: the joiner neither runs its fn (the
		// lock is still held by the stuck work) nor dies on the slice
		// timeouts - it keeps waiting, one bounded slice at a time.
		await vi.advanceTimersByTimeAsync(300)
		await firstFailure
		expect(joinerFn).not.toHaveBeenCalled()
		expect(mutex.isLocked).toBe(true)

		releaseWork("done")
		await vi.advanceTimersByTimeAsync(0)
		expect(joinerFn).toHaveBeenCalledTimes(1)
		await expect(second).resolves.toBe("joined")
		expect(mutex.isLocked).toBe(false)
	})
})
