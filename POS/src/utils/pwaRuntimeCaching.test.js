import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

// SEC-17: the generated service worker must never cache /api/ responses.
// Asserted against the vite.config.js source — importing it would execute
// every plugin factory just to read one array. Workbox matches runtimeCaching
// routes in registration order, so the NetworkOnly /api/ rule must precede any
// pattern that could still catch an /api/ URL (notably /files/, whose regex
// also matches query strings ending in an image extension).
// Tests always run from POS/ (npm --prefix POS), so cwd resolves the config.
const config = readFileSync(path.resolve(process.cwd(), "vite.config.js"), "utf8");

describe("workbox runtimeCaching in vite.config.js (SEC-17)", () => {
	it("handles /api/ with NetworkOnly, registered before caching rules", () => {
		const apiPattern = config.indexOf("urlPattern: /\\/api\\/.*/i");
		expect(apiPattern).toBeGreaterThan(-1);

		const apiBlock = config.slice(apiPattern, apiPattern + 120);
		expect(apiBlock).toContain('handler: "NetworkOnly"');

		const filesPattern = config.indexOf("urlPattern: /\\/files\\/");
		expect(filesPattern).toBeGreaterThan(apiPattern);
	});

	it("no longer configures the 24h api-cache", () => {
		expect(config).not.toContain("api-cache");
	});
});
