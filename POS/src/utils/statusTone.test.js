import { describe, expect, it } from "vitest"
import { STATUS_PILL_BASE, STATUS_TONES, statusTone } from "./statusTone"

describe("statusTone", () => {
	it("includes the design contract base classes", () => {
		const pill = statusTone("Draft")
		expect(pill).toContain(STATUS_PILL_BASE)
	})

	it("maps draft/returned to orange tone", () => {
		const draftPill = statusTone("Draft")
		expect(draftPill).toContain(STATUS_TONES.orange)
		expect(draftPill).toContain(
			"bg-orange-50 text-orange-700 ring-orange-600/20",
		)

		const returnedPill = statusTone("returned")
		expect(returnedPill).toContain(STATUS_TONES.orange)
	})

	it("maps return-with-issue or rejected to red tone", () => {
		const rejectedPill = statusTone("Rejected")
		expect(rejectedPill).toContain(STATUS_TONES.red)
		expect(rejectedPill).toContain("bg-red-50 text-red-700 ring-red-600/20")

		const issuePill = statusTone("return with issue")
		expect(issuePill).toContain(STATUS_TONES.red)
	})

	it("maps pending approval to blue tone", () => {
		const pendingPill = statusTone("Pending Approval")
		expect(pendingPill).toContain(STATUS_TONES.blue)
		expect(pendingPill).toContain("bg-blue-50 text-blue-700 ring-blue-600/20")
	})

	it("maps awaiting / in-progress to amber tone", () => {
		const awaitingPill = statusTone("Awaiting Destruction")
		expect(awaitingPill).toContain(STATUS_TONES.amber)
		expect(awaitingPill).toContain(
			"bg-amber-50 text-amber-700 ring-amber-600/20",
		)

		const progressPill = statusTone("In Progress")
		expect(progressPill).toContain(STATUS_TONES.amber)
	})

	it("maps completed/approved/submitted to green tone", () => {
		const completedPill = statusTone("Completed")
		expect(completedPill).toContain(STATUS_TONES.green)
		expect(completedPill).toContain(
			"bg-green-50 text-green-700 ring-green-600/20",
		)
	})

	it("maps cancelled to gray tone", () => {
		const cancelledPill = statusTone("Cancelled")
		expect(cancelledPill).toContain(STATUS_TONES.gray)
		expect(cancelledPill).toContain("bg-gray-50 text-gray-600 ring-gray-500/20")
	})

	it("derives tones from payload flags", () => {
		// docstatus: 1 -> green
		expect(statusTone("", { docstatus: 1 })).toContain(STATUS_TONES.green)
		// docstatus: 2 -> gray
		expect(statusTone("", { docstatus: 2 })).toContain(STATUS_TONES.gray)
		// returned: true -> orange
		expect(statusTone("", { returned: true })).toContain(STATUS_TONES.orange)
		// issue / return_with_issue -> red
		expect(statusTone("", { issue: true })).toContain(STATUS_TONES.red)
		// object input with docstatus: 1
		expect(statusTone({ docstatus: 1 })).toContain(STATUS_TONES.green)
	})
})
