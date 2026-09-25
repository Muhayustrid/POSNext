import path from "node:path";
import { promises as fs } from "node:fs";
import vue from "@vitejs/plugin-vue";
import frappeui from "frappe-ui/vite";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";
import { viteStaticCopy } from "vite-plugin-static-copy";

// Get build version from environment or use timestamp
const buildVersion = process.env.POS_NEXT_BUILD_VERSION || Date.now().toString();
const enableSourceMap = process.env.POS_NEXT_ENABLE_SOURCEMAP === "true";

/**
 * Vite plugin to write build version to version.json file
 * This enables cache busting and version tracking
 */
function posNextBuildVersionPlugin(version) {
	return {
		name: "pos-next-build-version",
		apply: "build",
		async writeBundle() {
			const versionFile = path.resolve(__dirname, "../pos_next/public/pos/version.json");
			await fs.mkdir(path.dirname(versionFile), { recursive: true });
			await fs.writeFile(
				versionFile,
				JSON.stringify(
					{
						version,
						timestamp: new Date().toISOString(),
						buildDate: new Date().toLocaleDateString("en-US", {
							year: "numeric",
							month: "long",
							day: "numeric",
						}),
					},
					null,
					2
				),
				"utf8"
			);
			console.log(`\n✓ Build version written: ${version}`);
		},
	};
}

/**
 * Vite plugin to make the generated service worker servable from /sw.js
 * (COR-FE-05). The SW is registered from /sw.js (see POS/src/main.js and the
 * pos_next/www/sw.py controller) so it can claim scope "/" over the cashier
 * page at /pos, but workbox generates precache URLs relative to the SW script
 * URL: served from /sw.js, "index.html" would resolve to "/index.html"
 * (404) and the SW install would fail. After VitePWA writes sw.js, rewrite
 * every relative precache URL (and the workbox runtime import, if not
 * inlined) to the absolute /assets/pos_next/pos/ base.
 */
function posNextSwPathFixPlugin() {
	const swFile = path.resolve(__dirname, "../pos_next/public/pos/sw.js");
	const assetBase = "/assets/pos_next/pos/";
	return {
		name: "pos-next-sw-path-fix",
		apply: "build",
		closeBundle: {
			sequential: true,
			order: "post", // run after VitePWA's closeBundle, which writes sw.js
			async handler() {
				let source = await fs.readFile(swFile, "utf8");
				// Workbox runtime chunk import (no-op when the runtime is inlined).
				source = source.replaceAll('define(["./workbox-', `define(["${assetBase}workbox-`);
				// The precache manifest is the array literal handed to workbox's
				// precache call (precacheAndRoute([...]) or an inlined IIFE).
				const listStart = source.indexOf('[{url:"');
				if (listStart === -1 || !source.slice(listStart, listStart + 256).includes('revision:"')) {
					throw new Error("pos-next-sw-path-fix: precache manifest not found in generated sw.js");
				}
				const listEnd = source.indexOf("])", listStart);
				const manifest = source.slice(listStart, listEnd + 1);
				const fixedManifest = manifest.replace(
					/(url:")([^"/][^"]*)(")/g,
					(_match, prefix, url, suffix) => `${prefix}${assetBase}${url}${suffix}`
				);
				source = source.slice(0, listStart) + fixedManifest + source.slice(listEnd + 1);
				await fs.writeFile(swFile, source);
				console.log("\n✓ SW precache URLs rewritten to absolute /assets/pos_next/pos/ base");
			},
		},
	};
}

// https://vitejs.dev/config/
export default defineConfig({
	plugins: [
		posNextBuildVersionPlugin(buildVersion),
		frappeui({
			frappeProxy: true,
			jinjaBootData: true,
			lucideIcons: true,
			buildConfig: {
				indexHtmlPath: "../pos_next/www/pos.html",
				outDir: "../pos_next/public/pos",
				emptyOutDir: true,
				sourcemap: enableSourceMap,
			},
		}),
		vue(),
		viteStaticCopy({
			targets: [
				{
					src: "src/workers",
					dest: ".",
				},
			],
		}),
		VitePWA({
			registerType: "autoUpdate",
			// Registration is handled in src/main.js (/sw.js, scope "/"); keep the
			// plugin from injecting its own register script for the static SW URL.
			injectRegister: false,
			includeAssets: ["favicon.png", "icon.svg", "icon-maskable.svg"],
			manifest: {
				name: "POSNext",
				short_name: "POSNext",
				description:
					"Point of Sale system with real-time billing, stock management, and offline support",
				theme_color: "#4F46E5",
				background_color: "#ffffff",
				display: "standalone",
				// Manifest scope must contain start_url or Chrome rejects installability.
				// Canonical app URL is /pos (www/pos.html); the previous scope was the
				// build-output path (/assets/pos_next/pos/), which Chrome rejects for install.
				// The service worker is served from /sw.js with scope "/" (see src/main.js),
				// which now contains this manifest scope.
				scope: "/pos",
				start_url: "/pos",
				icons: [
					{
						src: "/assets/pos_next/pos/icon-192.png",
						sizes: "192x192",
						type: "image/png",
						purpose: "any",
					},
					{
						src: "/assets/pos_next/pos/icon-512.png",
						sizes: "512x512",
						type: "image/png",
						purpose: "any",
					},
					{
						src: "/assets/pos_next/pos/icon-maskable-192.png",
						sizes: "192x192",
						type: "image/png",
						purpose: "maskable",
					},
					{
						src: "/assets/pos_next/pos/icon-maskable-512.png",
						sizes: "512x512",
						type: "image/png",
						purpose: "maskable",
					},
				],
				// Chrome's "Richer PWA Install UI" needs at least one wide and one
				// non-wide screenshot; captured from the live login page at the exact
				// viewport sizes declared here.
				screenshots: [
					{
						src: "/assets/pos_next/pos/pwa-screenshot-wide.png",
						sizes: "1280x800",
						type: "image/png",
						form_factor: "wide",
					},
					{
						src: "/assets/pos_next/pos/pwa-screenshot-narrow.png",
						sizes: "390x844",
						type: "image/png",
						form_factor: "narrow",
					},
				],
			},
			workbox: {
				globPatterns: ["**/*.{js,css,html,ico,png,svg,woff,woff2}"],
				maximumFileSizeToCacheInBytes: 4 * 1024 * 1024, // 3 MB
				// The SW is served from /sw.js (pos_next/www/sw.py); inline the workbox
				// runtime so sw.js carries no relative ./workbox-*.js import (which
				// would resolve against / instead of /assets/pos_next/pos/).
				inlineWorkboxRuntime: true,
				navigateFallback: null,
				navigateFallbackDenylist: [/^\/api/, /^\/app/],
				runtimeCaching: [
					// SEC-17: never cache /api/ responses — they carry cashier-scoped
					// data (invoices, customers, PII) that must not leak to the next
					// cashier on a shared device. Registered FIRST: workbox matches
					// routes in registration order, so no later pattern (e.g. /files/)
					// can capture an /api/ URL.
					{
						urlPattern: /\/api\/.*/i,
						handler: "NetworkOnly",
					},
					{
						urlPattern: /^https:\/\/fonts\.googleapis\.com\/.*/i,
						handler: "CacheFirst",
						options: {
							cacheName: "google-fonts-cache",
							expiration: {
								maxEntries: 10,
								maxAgeSeconds: 60 * 60 * 24 * 365, // 1 year
							},
							cacheableResponse: {
								statuses: [0, 200],
							},
						},
					},
					{
						urlPattern: /^https:\/\/fonts\.gstatic\.com\/.*/i,
						handler: "CacheFirst",
						options: {
							cacheName: "gstatic-fonts-cache",
							expiration: {
								maxEntries: 10,
								maxAgeSeconds: 60 * 60 * 24 * 365, // 1 year
							},
							cacheableResponse: {
								statuses: [0, 200],
							},
						},
					},
					// PERF-20: no runtime cache for /assets/pos_next/pos/ — every
					// file there is content-hashed and already in the workbox
					// precache manifest (globPatterns). A second CacheFirst route
					// over the same URLs would double-store each asset and could
					// pin a stale version the precache has already replaced.
					// Cache product images with StaleWhileRevalidate for better UX
					{
						urlPattern: /\/files\/.*\.(jpg|jpeg|png|gif|webp|svg)$/i,
						handler: "StaleWhileRevalidate",
						options: {
							cacheName: "product-images-cache",
							expiration: {
								maxEntries: 200, // Cache up to 200 product images
								maxAgeSeconds: 60 * 60 * 24 * 7, // 7 days
							},
							cacheableResponse: {
								statuses: [0, 200],
							},
						},
					},
					{
						urlPattern: ({ request, url }) =>
							request.mode === "navigate" && url.pathname.startsWith("/pos"),
						handler: "NetworkFirst",
						options: {
							cacheName: "pos-page-cache",
							networkTimeoutSeconds: 3,
							expiration: {
								maxEntries: 1,
								maxAgeSeconds: 60 * 60 * 24, // 24 hours
							},
						},
					},
				],
				cleanupOutdatedCaches: true,
				skipWaiting: true,
				clientsClaim: true,
			},
			devOptions: {
				enabled: true,
				type: "module",
			},
		}),
		posNextSwPathFixPlugin(),
	],
	build: {
		chunkSizeWarningLimit: 1500,
		outDir: "../pos_next/public/pos",
		emptyOutDir: true,
		target: "es2015",
		sourcemap: enableSourceMap,
		rollupOptions: {
			output: {
				// PERF-13: split the heavyweight vendors out of the entry/app
				// chunks so the initial chunk stays cacheable and parseable.
				// Only the four biggest deps are split; everything else keeps
				// Rollup's default chunking.
				manualChunks(id) {
					if (!id.includes("node_modules")) return undefined;
					if (id.includes("node_modules/qz-tray")) return "vendor-qz-tray";
					if (id.includes("node_modules/html2canvas")) return "vendor-html2canvas";
					if (id.includes("node_modules/dexie")) return "vendor-dexie";
					if (id.includes("node_modules/frappe-ui")) return "vendor-frappe-ui";
					return undefined;
				},
			},
		},
	},
	worker: {
		format: "es",
		rollupOptions: {
			output: {
				format: "es",
			},
		},
	},
	resolve: {
		alias: {
			"@": path.resolve(__dirname, "src"),
			"tailwind.config.js": path.resolve(__dirname, "tailwind.config.js"),
		},
	},
	define: {
		__BUILD_VERSION__: JSON.stringify(buildVersion),
	},
	// Vitest reads this block. frappe-ui ships uncompiled TS with extensionless
	// relative imports, so it must be transformed (inlined) instead of being
	// loaded by Node's native ESM resolver, which rejects "./resources" etc.
	test: {
		environment: "jsdom",
		setupFiles: ["./src/test-setup.js"],
		server: {
			deps: {
				inline: ["frappe-ui"],
			},
		},
	},
	optimizeDeps: {
		include: ["feather-icons", "showdown", "highlight.js/lib/core", "interactjs", "qz-tray"],
	},
	server: {
		allowedHosts: true,
		port: 8080,
		proxy: {
			"^/(app|api|assets|files|printview)": {
				target: "http://127.0.0.1:8000",
				ws: true,
				changeOrigin: true,
				secure: false,
				cookieDomainRewrite: "localhost",
				router: (req) => {
					const site_name = req.headers.host.split(":")[0];
					// Support both localhost and 127.0.0.1
					const isLocalhost = site_name === "localhost" || site_name === "127.0.0.1";
					const targetHost = isLocalhost ? "127.0.0.1" : site_name;
					return `http://${targetHost}:8000`;
				},
			},
		},
	},
});
