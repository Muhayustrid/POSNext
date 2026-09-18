import { describe, expect, it, vi } from "vitest"

// Importing the SFC pulls in the frappe-ui/reka-ui barrels, which do not
// resolve under vitest (same reason apiWrapper.test.js mocks them). Nothing
// under test touches them; only recapPeriodRange is exercised.
vi.mock("frappe-ui", () => ({
	Button: { name: "Button", render: () => null },
	Dialog: { name: "Dialog", render: () => null },
	createResource: vi.fn(() => ({ reload: vi.fn() })),
	call: vi.fn(),
}))
vi.mock("reka-ui", () => ({
	DialogTitle: { name: "DialogTitle", render: () => null },
}))
vi.mock("@/composables/useToast", () => ({
	useToast: () => ({ showSuccess: vi.fn(), showError: vi.fn() }),
}))
vi.mock("@/utils/currency", () => ({
	DEFAULT_CURRENCY: "IDR",
	formatCurrency: vi.fn(),
}))
vi.mock("@/utils/print/transport", () => ({
	printHTML: vi.fn(),
}))

import { recapPeriodRange } from "./SalesRecapDialog.vue"

// new Date(2026, 8, 18) → September 18, 2026 (month is 0-based)
const NOW = new Date(2026, 8, 18)

describe("recapPeriodRange", () => {
	it("returns today for both bounds on 'today'", () => {
		expect(recapPeriodRange("today", NOW)).toEqual({
			from: "2026-09-18",
			to: "2026-09-18",
		})
	})

	it("returns yesterday's date for 'yesterday'", () => {
		expect(recapPeriodRange("yesterday", NOW)).toEqual({
			from: "2026-09-17",
			to: "2026-09-17",
		})
	})

	it("spans the first of the month through today for 'this_month'", () => {
		expect(recapPeriodRange("this_month", NOW)).toEqual({
			from: "2026-09-01",
			to: "2026-09-18",
		})
	})

	it("spans the full previous month for 'last_month'", () => {
		expect(recapPeriodRange("last_month", NOW)).toEqual({
			from: "2026-08-01",
			to: "2026-08-31",
		})
	})

	it("clamps to February on a month boundary (non-leap year)", () => {
		expect(recapPeriodRange("last_month", new Date(2026, 2, 31))).toEqual({
			from: "2026-02-01",
			to: "2026-02-28",
		})
	})

	it("keeps February 29 on a leap year boundary", () => {
		expect(recapPeriodRange("last_month", new Date(2024, 2, 31))).toEqual({
			from: "2024-02-01",
			to: "2024-02-29",
		})
	})

	it("returns null for shift and custom (no fixed range)", () => {
		expect(recapPeriodRange("shift", NOW)).toBeNull()
		expect(recapPeriodRange("custom", NOW)).toBeNull()
		expect(recapPeriodRange("anything", NOW)).toBeNull()
	})
})
