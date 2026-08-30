import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vitest/config";
import path from "node:path";

// Minimal Vitest config mirroring vite.config.js aliases. The app relies on the
// Frappe global `__` for i18n; tests/setup.js defines it for the jsdom env.
export default defineConfig({
	plugins: [vue()],
	resolve: {
		alias: {
			"@": path.resolve(__dirname, "src"),
		},
	},
	test: {
		environment: "jsdom",
		setupFiles: ["./tests/setup.js"],
	},
});
