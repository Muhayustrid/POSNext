/**
 * @vitest-environment jsdom
 */
import { describe, expect, it } from "vitest"
import { escapeHtml, escapeRegExp, highlightSafe } from "./escapeHtml"

describe("escapeHtml", () => {
	it("escapes every HTML metacharacter", () => {
		expect(escapeHtml(`<img src=x onerror="alert('1')">&`)).toBe(
			"&lt;img src=x onerror=&quot;alert(&#39;1&#39;)&quot;&gt;&amp;",
		)
	})

	it("leaves text without metacharacters byte-identical", () => {
		const normal = "Kopi Susu Gula Aren (2x) — Rp 18.000"
		expect(escapeHtml(normal)).toBe(normal)
	})

	it("coerces numbers and tolerates null/undefined", () => {
		expect(escapeHtml(48)).toBe("48")
		expect(escapeHtml(null)).toBe("")
		expect(escapeHtml(undefined)).toBe("")
	})
})

describe("escapeRegExp", () => {
	it("escapes regex metacharacters", () => {
		expect(escapeRegExp("a.b*c(d)e[f]g$h|i?{j}k\\l^m")).toBe(
			"a\\.b\\*c\\(d\\)e\\[f\\]g\\$h\\|i\\?\\{j\\}k\\\\l\\^m",
		)
	})
})

describe("highlightSafe (SEC-12 escape-before-mark)", () => {
	const label = "<img src=x onerror=alert(1)>Kopi"
	const render = (html) => {
		const el = document.createElement("div")
		el.innerHTML = html
		return el
	}

	it("renders injected markup as text and marks only the query", () => {
		const out = highlightSafe(label, "kopi")
		expect(out).not.toContain("<img")
		const el = render(out)
		expect(el.querySelector("mark").textContent).toBe("Kopi")
		// the raw string survives as TEXT content
		expect(el.textContent).toContain("<img src=x onerror=alert(1)>")
	})

	it("escapes the text even when there is no query (empty search box)", () => {
		expect(highlightSafe(label, "")).not.toContain("<img")
	})

	it("does not throw on a regex-metacharacter query like '('", () => {
		const out = highlightSafe("Item (besar)", "(")
		expect(out).toContain("<mark>(</mark>")
	})

	it("matches escaped entities consistently on both sides", () => {
		expect(highlightSafe("Roti & Kue", "&")).toBe("Roti <mark>&amp;</mark> Kue")
	})

	it("uses the caller's mark template", () => {
		expect(highlightSafe("Kopi", "kopi", '<mark class="hl">$1</mark>')).toBe(
			'<mark class="hl">Kopi</mark>',
		)
	})
})
