/**
 * Responsive Payment Dialog Composable
 * Handles viewport tracking and dynamic sizing for payment dialog
 */

import { ref, computed, onMounted, onUnmounted } from "vue";

export function useResponsivePayment() {
	// Viewport dimension tracking
	const viewportWidth = ref(typeof window !== "undefined" ? window.innerWidth : 1200);
	const viewportHeight = ref(typeof window !== "undefined" ? window.innerHeight : 800);

	function updateViewportDimensions() {
		viewportWidth.value = window.innerWidth;
		viewportHeight.value = window.innerHeight;
	}

	onMounted(() => {
		updateViewportDimensions();
		window.addEventListener("resize", updateViewportDimensions);
	});

	onUnmounted(() => {
		window.removeEventListener("resize", updateViewportDimensions);
	});

	// Dynamic dialog size based on viewport
	const dynamicDialogSize = computed(() => {
		const width = viewportWidth.value;
		if (width < 640) return "full"; // Mobile: full screen
		if (width < 768) return "full"; // Small tablet: full screen for better usability
		if (width < 1024) return "4xl"; // Tablet
		if (width < 1280) return "5xl"; // Small desktop
		return "6xl"; // Large desktop
	});

	// Check if we're on a mobile device (for mobile-specific behavior)
	const isMobileView = computed(() => viewportWidth.value < 1024);

	// Dynamic content max height based on viewport
	const dialogContentMaxHeight = computed(() => {
		const height = viewportHeight.value;
		const width = viewportWidth.value;

		// On mobile, don't set max-height - let content determine size
		if (width < 1024) {
			return "none";
		}
		// Desktop/tablet: the dialog chrome (overlay py-4 + DialogContent my-8
		// + header row) measures ~190px. The old `height - 100` left the
		// dialog taller than the viewport, so the overlay itself scrolled and
		// the pinned action buttons sat below the fold.
		return `${Math.max(360, height - 190)}px`;
	});

	// Check if we're in compact mode (small screens)
	const isCompactMode = computed(() => viewportHeight.value < 700 || viewportWidth.value < 1024);

	// Check if we're on a very small mobile screen
	const isSmallMobile = computed(() => viewportWidth.value < 360 || viewportHeight.value < 600);

	// Dynamic gap and padding based on screen size
	const dynamicGap = computed(() => {
		if (viewportWidth.value < 360) return "gap-1"; // Very small phones
		if (viewportWidth.value < 640) return "gap-1.5";
		if (viewportWidth.value < 1024) return "gap-2";
		return "gap-3";
	});

	// Dynamic text sizes
	const dynamicTextSize = computed(() => {
		const width = viewportWidth.value;
		const height = viewportHeight.value;

		// Very small phones
		if (width < 360 || height < 550) {
			return {
				header: "text-[10px]",
				body: "text-[10px]",
				amount: "text-base",
				grandTotal: "text-base",
			};
		}
		// Small phones
		if (width < 640) {
			return {
				header: "text-xs",
				body: "text-xs",
				amount: "text-lg",
				grandTotal: "text-lg",
			};
		}
		// Tablet and small height screens
		if (height < 700) {
			return {
				header: "text-sm",
				body: "text-sm",
				amount: "text-lg",
				grandTotal: "text-xl",
			};
		}
		// Default desktop
		return {
			header: "text-sm",
			body: "text-sm",
			amount: "text-xl",
			grandTotal: "text-2xl",
		};
	});

	// Dynamic button heights
	const dynamicButtonHeight = computed(() => {
		const width = viewportWidth.value;
		const height = viewportHeight.value;

		// Very small phones - smaller buttons
		if (width < 360 || height < 550) return "h-10";
		// Small phones
		if (width < 640) return "h-11";
		// Short screens
		if (height < 700) return "h-12";
		return "h-14";
	});

	// Mobile action button sizing
	const mobileButtonSize = computed(() => {
		const width = viewportWidth.value;
		const height = viewportHeight.value;

		if (width < 360 || height < 550) {
			return {
				height: "h-11",
				text: "text-sm",
				icon: "w-4 h-4",
				gap: "gap-1.5",
			};
		}
		if (width < 640) {
			return {
				height: "h-12",
				text: "text-sm",
				icon: "w-5 h-5",
				gap: "gap-1.5",
			};
		}
		return {
			height: "h-14",
			text: "text-base",
			icon: "w-5 h-5",
			gap: "gap-2",
		};
	});

	// Dynamic numpad key size — cashiers stab at these all day, keep every
	// key a comfortable touch target (48px floor, 64px on normal screens).
	// Heights are rem-based, so the in-app text scale inflates them; the
	// <800 threshold keeps keys shorter on 720–800px viewports where the
	// full dialog otherwise needs inner scrolling.
	const dynamicNumpadSize = computed(() => {
		if (viewportHeight.value < 600) return { key: "h-12", addBtn: "h-[6.5rem]" };
		if (viewportHeight.value < 800) return { key: "h-14", addBtn: "h-[7.5rem]" };
		return { key: "h-16", addBtn: "h-[8.5rem]" };
	});

	return {
		// State
		viewportWidth,
		viewportHeight,

		// Computed
		dynamicDialogSize,
		isMobileView,
		dialogContentMaxHeight,
		isCompactMode,
		isSmallMobile,
		dynamicGap,
		dynamicTextSize,
		dynamicButtonHeight,
		mobileButtonSize,
		dynamicNumpadSize,
	};
}
