import { beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

vi.mock("frappe-ui", () => ({
	createResource: vi.fn(() => ({
		submit: vi.fn(),
		data: null,
		loading: false,
		error: null,
	})),
}))

vi.mock("./bootstrap", () => ({
	useBootstrapStore: () => ({
		getPreloadedPOSSettings: () => null,
	}),
}))

import { usePOSSettingsStore } from "./posSettings"

describe("posSettings store auto_balance_closing", () => {
	beforeEach(() => {
		setActivePinia(createPinia())
	})

	it("defaults auto_balance_closing to 0 and autoBalanceClosing to false", () => {
		const store = usePOSSettingsStore()
		expect(store.settings.auto_balance_closing).toBe(0)
		expect(store.autoBalanceClosing).toBe(false)
	})

	it("evaluates autoBalanceClosing to true when auto_balance_closing is 1", () => {
		const store = usePOSSettingsStore()
		store.settings.auto_balance_closing = 1
		expect(store.autoBalanceClosing).toBe(true)
	})

	it("resets auto_balance_closing on resetSettings", () => {
		const store = usePOSSettingsStore()
		store.settings.auto_balance_closing = 1
		expect(store.autoBalanceClosing).toBe(true)
		store.resetSettings()
		expect(store.settings.auto_balance_closing).toBe(0)
		expect(store.autoBalanceClosing).toBe(false)
	})
})
