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

describe("parseError server-message extraction", () => {
	it("accepts _server_messages already parsed as an array of objects", () => {
		// some frappe-ui paths surface the pre-parsed form; JSON.parse on an
		// array used to throw and fall back to the generic message
		const ctx = parseError({
			_server_messages: [{ message: "Diskon melebihi batas", title: "Validasi" }],
		})

		expect(ctx.message).toBe("Diskon melebihi batas")
		expect(ctx.title).toBe("Validasi")
	})

	it("accepts _server_messages already parsed as an array of JSON strings", () => {
		const ctx = parseError({
			_server_messages: [JSON.stringify({ message: "Stok tidak cukup" })],
		})

		expect(ctx.message).toBe("Stok tidak cukup")
	})

	it("still parses the plain JSON-string form", () => {
		const ctx = parseError({
			_server_messages: JSON.stringify([JSON.stringify({ message: "Kode HQ wajib" })]),
		})

		expect(ctx.message).toBe("Kode HQ wajib")
	})
})

describe("parseError Insufficient Stock structured errors payload", () => {
	it("turns raw errors JSON in error.messages[0] into a human-readable message", () => {
		const error = {
			exc_type: "ValidationError",
			messages: [
				JSON.stringify({
					errors: [
						{
							item_code: "PJO2605",
							item_name: "Roti Coklat",
							warehouse: "Store - RR",
							requested_qty: 3,
							available_qty: 1,
						},
					],
				}),
			],
		}
		const ctx = parseError(error)

		expect(ctx.title).toBe("Insufficient Stock")
		expect(ctx.type).toBe("warning")
		expect(ctx.retryable).toBe(true)
		expect(ctx.message).not.toContain('{"errors"')
		expect(ctx.message).toContain("Roti Coklat")
		expect(ctx.message).toContain("3")
		expect(ctx.message).toContain("1")
		expect(ctx.message).toContain("Store - RR")
	})

	it("falls back to item_code when item_name is missing", () => {
		const error = {
			exc_type: "ValidationError",
			messages: [
				JSON.stringify({
					errors: [
						{
							item_code: "PJO2605",
							warehouse: "Store - RR",
							requested_qty: 2,
							available_qty: 0,
						},
					],
				}),
			],
		}
		const ctx = parseError(error)

		expect(ctx.title).toBe("Insufficient Stock")
		expect(ctx.message).toContain("PJO2605")
		expect(ctx.message).not.toContain('{"errors"')
	})

	it("formats multiple items with one line per item", () => {
		const error = {
			exc_type: "ValidationError",
			messages: [
				JSON.stringify({
					errors: [
						{
							item_code: "ITEM-A",
							item_name: "Item A",
							warehouse: "Store A",
							requested_qty: 5,
							available_qty: 2,
						},
						{
							item_code: "ITEM-B",
							warehouse: "Store B",
							requested_qty: 4,
							available_qty: 1,
						},
					],
				}),
			],
		}
		const ctx = parseError(error)
		const lines = ctx.message.split("\n")

		expect(lines).toHaveLength(2)
		expect(lines[0]).toContain("Item A")
		expect(lines[0]).toContain("Store A")
		expect(lines[1]).toContain("ITEM-B")
		expect(lines[1]).toContain("Store B")
	})

	it("detects errors payload when arriving via error.message", () => {
		const error = {
			message: JSON.stringify({
				errors: [
					{
						item_code: "PJO2605",
						warehouse: "Store",
						requested_qty: 2,
						available_qty: 1,
					},
				],
			}),
		}
		const ctx = parseError(error)

		expect(ctx.title).toBe("Insufficient Stock")
		expect(ctx.retryable).toBe(true)
		expect(ctx.message).not.toContain('{"errors"')
		expect(ctx.message).toContain("PJO2605")
	})

	it("detects errors payload when arriving via _server_messages", () => {
		const error = {
			_server_messages: JSON.stringify([
				JSON.stringify({
					message: JSON.stringify({
						errors: [
							{
								item_code: "PJO2605",
								item_name: "Roti Keju",
								warehouse: "Store",
								requested_qty: 2,
								available_qty: 0,
							},
						],
					}),
				}),
			]),
		}
		const ctx = parseError(error)

		expect(ctx.title).toBe("Insufficient Stock")
		expect(ctx.retryable).toBe(true)
		expect(ctx.message).toContain("Roti Keju")
		expect(ctx.message).not.toContain('{"errors"')
	})
})
