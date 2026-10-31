import { describe, expect, it } from "vitest"

import { createCSRFAwareRequest } from "./csrf"

describe("createCSRFAwareRequest", () => {
	it("rejects resource errors with the server's message, not 'method ExcType'", async () => {
		const request = createCSRFAwareRequest(async () => {
			const e = new Error("/api/method/pos_next.api.backdate_invoices.x ValidationError")
			e.exc_type = "ValidationError"
			e.messages = ["Invoice No. 3: Shift TRNG sudah ditutup"]
			throw e
		})
		await expect(request()).rejects.toMatchObject({
			message: "Invoice No. 3: Shift TRNG sudah ditutup",
			exc_type: "ValidationError",
		})
	})
})
