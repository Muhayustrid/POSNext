/**
 * @vitest-environment jsdom
 */
import { describe, expect, it } from "vitest"
import { ref } from "vue"
import { getShiftBadge, useShiftFilter } from "./useShiftFilter"

describe("useShiftFilter", () => {
	describe("getShiftBadge", () => {
		it("prefers shift_slot over pos_profile", () => {
			expect(
				getShiftBadge({ shift_slot: "Pagi", pos_profile: "RS PKU - Main" }),
			).toBe("Pagi")
		})

		it("falls back to pos_profile when shift_slot is absent or empty", () => {
			expect(getShiftBadge({ pos_profile: "RS PKU - Main" })).toBe(
				"RS PKU - Main",
			)
			expect(
				getShiftBadge({ shift_slot: "", pos_profile: "RS PKU - Main" }),
			).toBe("RS PKU - Main")
			expect(
				getShiftBadge({ shift_slot: null, pos_profile: "RS PKU - Main" }),
			).toBe("RS PKU - Main")
		})

		it("returns empty string when both are missing", () => {
			expect(getShiftBadge({})).toBe("")
			expect(getShiftBadge(null)).toBe("")
			expect(getShiftBadge(undefined)).toBe("")
		})
	})

	describe("filter options and filtering", () => {
		it("derives sorted unique options and filters rows by selected shift", () => {
			const rows = ref([
				{ name: "INV-1", shift_slot: "Siang" },
				{ name: "INV-2", shift_slot: "Pagi" },
				{ name: "INV-3", pos_profile: "Outlet 2" },
				{ name: "INV-4", shift_slot: "Pagi" },
				{ name: "INV-5" }, // no badge
			])

			const { options, selected, filtered } = useShiftFilter(rows)

			expect(options.value).toEqual(["Outlet 2", "Pagi", "Siang"])
			expect(selected.value).toBe("")
			expect(filtered.value.length).toBe(5)

			selected.value = "Pagi"
			expect(filtered.value.map((r) => r.name)).toEqual(["INV-2", "INV-4"])

			selected.value = "Siang"
			expect(filtered.value.map((r) => r.name)).toEqual(["INV-1"])

			selected.value = "Outlet 2"
			expect(filtered.value.map((r) => r.name)).toEqual(["INV-3"])

			selected.value = ""
			expect(filtered.value.length).toBe(5)
		})

		it("handles empty or null rows gracefully", () => {
			const rows = ref([])
			const { options, selected, filtered } = useShiftFilter(rows)
			expect(options.value).toEqual([])
			expect(filtered.value).toEqual([])

			rows.value = null
			expect(options.value).toEqual([])
			expect(filtered.value).toEqual([])
		})
	})
})
