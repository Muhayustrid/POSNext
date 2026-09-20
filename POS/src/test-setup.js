// Global test setup, loaded by every vitest environment (see vite.config.js
// `test.setupFiles`).

// Frappe injects the global __() translator when the app boots inside the
// desk. Unit tests boot no Frappe, so app modules that use the bare global
// (e.g. stockValidator's __() wrapping) would throw ReferenceError. Provide a
// stand-in with Frappe's contract: dictionary lookup is a no-op here, but
// positional {0}/{1} placeholders are still formatted like frappe's __ does.
if (typeof globalThis.__ !== "function") {
	globalThis.__ = (text, replace) => {
		if (!replace) return text
		return text.replace(/{(\d+)}/g, (match, n) => replace[n] ?? match)
	}
}
