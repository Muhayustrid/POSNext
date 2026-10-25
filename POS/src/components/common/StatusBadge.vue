<template>
	<span
		class="inline-flex shrink-0 items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset"
		:class="[badgeClasses, sizeClasses]"
	>
		<svg
			v-if="icon"
			class="w-3.5 h-3.5 shrink-0 me-1 -ms-0.5"
			:class="iconClasses"
			:fill="iconFill"
			stroke="currentColor"
			viewBox="0 0 24 24"
		>
			<path
				:stroke-linecap="strokeLinecap"
				:stroke-linejoin="strokeLinejoin"
				:stroke-width="strokeWidth"
				:d="icon"
			/>
		</svg>
		<template v-if="label || value">
			<span v-if="label" class="opacity-80">{{ label }}</span>
			<span
				v-if="value"
				class="font-semibold tabular-nums"
				:class="{ 'ms-1': label }"
			>{{ value }}</span>
		</template>
		<span v-else>{{ text }}</span>
	</span>
</template>

<script setup>
import { computed } from "vue"
import { STATUS_TONES } from "@/utils/statusTone"

const props = defineProps({
	variant: {
		type: String,
		default: "blue", // blue, green, orange, red, gray, amber
		validator: (value) =>
			["blue", "green", "orange", "red", "gray", "amber"].includes(value),
	},
	icon: {
		type: String,
		default: null,
	},
	iconFill: {
		type: String,
		default: "none",
	},
	strokeLinecap: {
		type: String,
		default: "round",
	},
	strokeLinejoin: {
		type: String,
		default: "round",
	},
	strokeWidth: {
		type: String,
		default: "2",
	},
	text: {
		type: String,
		default: "",
	},
	label: {
		type: String,
		default: null,
	},
	value: {
		type: String,
		default: null,
	},
	size: {
		type: String,
		default: "sm", // xs, sm, md
		validator: (value) => ["xs", "sm", "md"].includes(value),
	},
})

const badgeClasses = computed(() => {
	return STATUS_TONES[props.variant] || STATUS_TONES.blue
})

const iconClasses = computed(() => {
	const variants = {
		blue: "text-blue-600",
		green: "text-green-600",
		orange: "text-orange-600",
		red: "text-red-600",
		gray: "text-gray-600",
		amber: "text-amber-600",
	}
	return variants[props.variant] || variants.blue
})

const sizeClasses = computed(() => {
	if (props.size === "md") return "text-sm px-2.5 py-1"
	return "text-xs px-2 py-0.5"
})

const textSize = computed(() => {
	const sizes = {
		xs: "text-xs",
		sm: "text-xs",
		md: "text-sm",
	}
	return sizes[props.size] || sizes.sm
})
</script>
