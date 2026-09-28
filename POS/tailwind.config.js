import frappeUIPreset from "frappe-ui/tailwind";

export default {
	presets: [frappeUIPreset],
	content: [
		"./index.html",
		"./src/**/*.{vue,js,ts,jsx,tsx}",
		"./node_modules/frappe-ui/src/components/**/*.{vue,js,ts,jsx,tsx}",
	],
	theme: {
		// The frappe-ui preset's theme plugin defines every text utility in static
		// px (text-xs=12px, text-sm=13px, …), so the per-device text scale (root
		// font-size %) never reached the text itself — only spacing scaled. These
		// rem twins are byte-for-byte the preset's scale converted to rem: at the
		// default 16px root the rendered size is identical, and text now follows
		// the root like every other rem utility.
		fontSize: {
			"2xs": ["0.6875rem", { lineHeight: "1.15", letterSpacing: "0.01em", fontWeight: "420" }],
			xs: ["0.75rem", { lineHeight: "1.15", letterSpacing: "0.02em", fontWeight: "420" }],
			sm: ["0.8125rem", { lineHeight: "1.15", letterSpacing: "0.02em", fontWeight: "420" }],
			base: ["0.875rem", { lineHeight: "1.15", letterSpacing: "0.02em", fontWeight: "420" }],
			lg: ["1rem", { lineHeight: "1.15", letterSpacing: "0.02em", fontWeight: "400" }],
			xl: ["1.125rem", { lineHeight: "1.15", letterSpacing: "0.01em", fontWeight: "400" }],
			"2xl": ["1.25rem", { lineHeight: "1.15", letterSpacing: "0.01em", fontWeight: "400" }],
			"3xl": ["1.5rem", { lineHeight: "1.15", fontWeight: 400, letterSpacing: "0.005em" }],
			"p-2xs": ["0.6875rem", { lineHeight: "1.6", letterSpacing: "0.01em", fontWeight: "420" }],
			"p-xs": ["0.75rem", { lineHeight: "1.6", letterSpacing: "0.02em", fontWeight: "420" }],
			"p-sm": ["0.8125rem", { lineHeight: "1.5", letterSpacing: "0.02em", fontWeight: "420" }],
			"p-base": ["0.875rem", { lineHeight: "1.5", letterSpacing: "0.02em", fontWeight: "420" }],
			"p-lg": ["1rem", { lineHeight: "1.5", letterSpacing: "0.02em", fontWeight: "400" }],
			"p-xl": ["1.125rem", { lineHeight: "1.42", letterSpacing: "0.01em", fontWeight: "400" }],
			"p-2xl": ["1.25rem", { lineHeight: "1.38", letterSpacing: "0.01em", fontWeight: "400" }],
			"p-3xl": ["1.5rem", { lineHeight: "1.2", fontWeight: 400, letterSpacing: "0.005em" }],
		},
		extend: {
			// Extra-small phones and up (header clock, pagination labels).
			// `xs:` classes were already used in templates but never defined,
			// so they silently never applied.
			screens: {
				xs: "400px",
			},
		},
	},
	plugins: [],
};
