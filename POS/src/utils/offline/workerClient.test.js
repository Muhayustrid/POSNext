// COR-FE-09: when the worker is unavailable, the graceful fallback for
// PING_SERVER must report the server as NOT reachable (false) and
// CHECK_OFFLINE must report offline (true). The old code returned true for
// both, telling the UI the server was reachable exactly when it could not be
// checked.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/utils/translation", () => ({ __: (t) => t }))

import { offlineWorker } from "./workerClient"

beforeEach(() => {
	vi.useFakeTimers()
})

afterEach(() => {
	vi.useRealTimers()
})

describe("gracefulFallback connectivity semantics (COR-FE-09)", () => {
	it("reports the server as unreachable when the worker is gone", async () => {
		offlineWorker.workerCrashed = true
		offlineWorker.initAttempts = offlineWorker.maxInitAttempts

		await expect(offlineWorker.sendMessage("PING_SERVER")).resolves.toBe(false)
	})

	it("reports offline when the worker is gone", async () => {
		offlineWorker.workerCrashed = true
		offlineWorker.initAttempts = offlineWorker.maxInitAttempts

		await expect(offlineWorker.sendMessage("CHECK_OFFLINE")).resolves.toBe(true)
	})
})
