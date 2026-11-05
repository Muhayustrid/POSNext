/**
 * @vitest-environment jsdom
 */
import { beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

const callMock = vi.hoisted(() => vi.fn(() => Promise.resolve(null)))

vi.mock("@/utils/apiWrapper", () => ({
	call: (...args) => callMock(...args),
}))

import { useOutletMonitoringStore } from "./outletMonitoring"

describe("useOutletMonitoringStore", () => {
	beforeEach(() => {
		setActivePinia(createPinia())
		vi.clearAllMocks()
	})

	it("store count equals number of tasks, not checkpoints", async () => {
		callMock.mockResolvedValueOnce({
			enabled: true,
			config: { show_menu_badge: 1 },
			count: 2,
			tasks: [
				{
					name: "REP-001",
					template: "Daily Opening",
					required_total: 5,
					required_done: 2,
				},
				{
					name: "REP-002",
					template: "Cleanliness Check",
					required_total: 4,
					required_done: 4,
				},
			],
		})

		const store = useOutletMonitoringStore()
		await store.refresh("POS Profile 1")

		expect(store.enabled).toBe(true)
		expect(store.tasks.length).toBe(2)
		// Count is 2 (tasks count), not 9 (sum of checkpoints)
		expect(store.count).toBe(2)
	})

	it("store disabled keeps menu hidden", async () => {
		callMock.mockResolvedValueOnce({
			enabled: false,
			config: {},
			count: 0,
			tasks: [],
		})

		const store = useOutletMonitoringStore()
		await store.refresh("POS Profile 1")

		expect(store.enabled).toBe(false)
		expect(store.count).toBe(0)
		expect(store.tasks).toEqual([])
	})
})
