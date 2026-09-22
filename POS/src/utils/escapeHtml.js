/**
 * HTML escaping + safe search-highlight — the single implementation every
 * HTML sink in the POS routes through (SEC-11 receipt HTML, SEC-12 v-html
 * autocomplete highlights). No dependencies.
 */

const HTML_ESCAPES = {
	"&": "&amp;",
	"<": "&lt;",
	">": "&gt;",
	'"': "&quot;",
	"'": "&#39;",
}

/**
 * Escape HTML metacharacters so data interpolated into a template can never
 * break out into markup. Safe for text content and double/single-quoted
 * attribute values alike.
 */
export function escapeHtml(value) {
	return String(value ?? "").replace(/[&<>"']/g, (ch) => HTML_ESCAPES[ch])
}

/** Escape RegExp metacharacters before a user query goes into new RegExp. */
export function escapeRegExp(str) {
	return String(str ?? "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}

/**
 * Escape-then-highlight for v-html autocomplete rows: `text` AND `query` are
 * HTML-escaped BEFORE the mark replacement, so item names containing markup
 * render as text and the only tags in the result are the <mark> wrapper. The
 * query is also RegExp-escaped, so a "(" search no longer throws. Escaping
 * both sides the same way keeps match positions aligned.
 */
export function highlightSafe(text, query, markTemplate = "<mark>$1</mark>") {
	if (!text) return text
	const safeText = escapeHtml(text)
	const pattern = escapeRegExp(escapeHtml(query))
	if (!query || !pattern) return safeText
	return safeText.replace(new RegExp(`(${pattern})`, "gi"), markTemplate)
}
