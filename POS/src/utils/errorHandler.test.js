// SEC-NEW-06: cleanErrorMessage must not write server-supplied text into a
// live element's innerHTML (assignment executes payload handlers such as
// <img src=x onerror=...>). Tag stripping now goes through DOMParser, which
// never touches the live DOM.
import { afterEach, describe, expect, it, vi } from "vitest"

import { parseError } from "./errorHandler"

const PAYLOAD = '<img src=x onerror="window.__xss=1">Insufficient stock'

afterEach(() => {
	vi.restoreAllMocks()
	delete window.__xss
})

describe("cleanErrorMessage XSS hardening (SEC-NEW-06)", () => {
	it("never writes the payload into a live element's innerHTML", () => {
		const setSpy = vi.spyOn(Element.prototype, "innerHTML", "set")

		parseError({ message: PAYLOAD })

		expect(setSpy).not.toHaveBeenCalled()
	})

	it("strips the tags, keeps the text, and executes nothing", () => {
		const ctx = parseError({ message: PAYLOAD })

		expect(ctx.message).toContain("Insufficient stock")
		expect(ctx.message).not.toContain("<img")
		expect(window.__xss).toBeUndefined()
	})

	it("still strips tags from server _server_messages payloads", () => {
		const ctx = parseError({
			_server_messages: JSON.stringify([
				JSON.stringify({ message: `${PAYLOAD}`, title: "Msg" }),
			]),
		})

		expect(ctx.message).toContain("Insufficient stock")
		expect(ctx.message).not.toContain("<img")
		expect(window.__xss).toBeUndefined()
	})
})
