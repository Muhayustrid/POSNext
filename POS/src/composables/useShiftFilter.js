import { computed, ref, toValue } from "vue"

export function getShiftBadge(row) {
	if (!row) return ""
	return row.shift_slot || row.pos_profile || ""
}

export function useShiftFilter(rowsRef) {
	const selected = ref("")

	const options = computed(() => {
		const rows = toValue(rowsRef)
		if (!Array.isArray(rows)) return []
		const set = new Set()
		for (const row of rows) {
			const badge = getShiftBadge(row)
			if (badge) {
				set.add(badge)
			}
		}
		return Array.from(set).sort((a, b) => a.localeCompare(b))
	})

	const filtered = computed(() => {
		const rows = toValue(rowsRef)
		if (!Array.isArray(rows)) return []
		const sel = selected.value
		if (!sel) return rows
		return rows.filter((row) => getShiftBadge(row) === sel)
	})

	return {
		options,
		selected,
		filtered,
	}
}
