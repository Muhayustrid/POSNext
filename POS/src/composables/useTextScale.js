import { computed, ref } from "vue";

/**
 * Per-device text size preference for the POS screen.
 *
 * Scales every rem-based size (Tailwind text/spacing utilities) by setting the
 * root font-size percentage. Bounds come from an empirical sweep at 360-1050px
 * viewports: the layout holds from 80% to 135%, but above 125% the header
 * title collapses and below 85% body text gets uncomfortable to read.
 * 100% = the browser's own base size, so a user who raised their browser
 * font size keeps a proportional boost.
 */
export const TEXT_SCALE_MIN = 85;
export const TEXT_SCALE_MAX = 125;
export const TEXT_SCALE_STEP = 5;
export const TEXT_SCALE_DEFAULT = 100;

const STORAGE_KEY = "pos_text_scale";

// Module-level singleton: every caller shares one scale.
const scale = ref(TEXT_SCALE_DEFAULT);

function clamp(value) {
	if (!Number.isFinite(value)) return TEXT_SCALE_DEFAULT;
	return Math.min(TEXT_SCALE_MAX, Math.max(TEXT_SCALE_MIN, value));
}

function apply() {
	if (typeof document === "undefined") return;
	document.documentElement.style.fontSize = `${scale.value}%`;
}

/**
 * Load the saved preference and apply it. Call once during app bootstrap,
 * before mount, so the first paint already uses the saved size.
 */
export function initTextScale() {
	if (typeof window === "undefined") return;
	let stored = null;
	try {
		stored = localStorage.getItem(STORAGE_KEY);
	} catch {
		stored = null;
	}
	scale.value = clamp(Number.parseInt(stored ?? "", 10));
	apply();
}

export function useTextScale() {
	function setScale(value) {
		scale.value = clamp(value);
		if (typeof localStorage !== "undefined") {
			try {
				localStorage.setItem(STORAGE_KEY, String(scale.value));
			} catch {
				// Storage unavailable (private mode): keep the session-only scale.
			}
		}
		apply();
	}

	return {
		scale: computed(() => scale.value),
		isDefault: computed(() => scale.value === TEXT_SCALE_DEFAULT),
		increase: () => setScale(scale.value + TEXT_SCALE_STEP),
		decrease: () => setScale(scale.value - TEXT_SCALE_STEP),
		reset: () => setScale(TEXT_SCALE_DEFAULT),
	};
}
