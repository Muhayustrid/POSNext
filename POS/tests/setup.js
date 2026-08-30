// Vitest global setup: the Frappe client global used for translations across the
// app, plus a console fallback for older jsdom builds.
globalThis.__ = (message, args) => {
	if (Array.isArray(args)) {
		return String(message).replace(/\{(\d+)\}/g, (match, index) =>
			args[Number(index)] !== undefined ? String(args[Number(index)]) : match
		);
	}
	return String(message);
};

if (!globalThis.console.debug) {
	globalThis.console.debug = () => {};
}
