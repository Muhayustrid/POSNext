import frappeUIPreset from "frappe-ui/tailwind";

export default {
	presets: [frappeUIPreset],
	content: [
		"./index.html",
		"./src/**/*.{vue,js,ts,jsx,tsx}",
		"./node_modules/frappe-ui/src/components/**/*.{vue,js,ts,jsx,tsx}",
	],
	theme: {
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
