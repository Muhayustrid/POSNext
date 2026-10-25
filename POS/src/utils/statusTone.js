export const STATUS_PILL_BASE =
	"inline-flex shrink-0 items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset"

export const STATUS_TONES = {
	orange: "bg-orange-50 text-orange-700 ring-orange-600/20",
	red: "bg-red-50 text-red-700 ring-red-600/20",
	blue: "bg-blue-50 text-blue-700 ring-blue-600/20",
	amber: "bg-amber-50 text-amber-700 ring-amber-600/20",
	green: "bg-green-50 text-green-700 ring-green-600/20",
	gray: "bg-gray-50 text-gray-600 ring-gray-500/20",
}

export function getStatusTone(state, flags = {}) {
	let raw = state
	let f = flags
	if (state && typeof state === "object") {
		f = state
		raw = state.status || state.workflow_state || state.state || ""
	}

	if (f.docstatus === 2 || f.cancelled) return "gray"
	if (f.docstatus === 1 || f.approved || f.completed) return "green"
	if (f.issue || f.return_with_issue) return "red"
	if (f.returned) return "orange"
	if (f.processing) return "blue"

	const s = String(raw || "")
		.trim()
		.toLowerCase()

	if (STATUS_TONES[s]) return s

	if (s === "draft" || s === "returned" || s === "open" || s === "revise")
		return "orange"

	if (
		s === "rejected" ||
		s.includes("issue") ||
		s === "credit" ||
		s === "failed"
	)
		return "red"

	if (
		s === "pending approval" ||
		s === "pending" ||
		s === "pending review" ||
		s === "to receive" ||
		s === "to receive and bill"
	)
		return "blue"

	if (
		s.includes("awaiting") ||
		s.includes("progress") ||
		s.includes("submitted for check") ||
		s === "destruction submitted" ||
		s === "to bill"
	)
		return "amber"

	if (
		s === "completed" ||
		s === "approved" ||
		s === "submitted" ||
		s === "done" ||
		s === "paid" ||
		s === "active"
	)
		return "green"

	if (s === "cancelled" || s === "canceled") return "gray"

	if (f.docstatus === 0 || f.editable) return "orange"

	return "gray"
}

export function statusTone(state, flags = {}) {
	const tone = getStatusTone(state, flags)
	return `${STATUS_PILL_BASE} ${STATUS_TONES[tone] || STATUS_TONES.gray}`
}
