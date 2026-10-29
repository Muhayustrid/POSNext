<template>
	<label
		v-if="options.length > 1"
		class="flex flex-wrap items-center gap-2 text-sm text-gray-600"
		data-test="company-picker"
	>
		<span class="font-medium">{{ label || __("Company") }}</span>
		<select
			:value="modelValue"
			:disabled="disabled"
			class="h-11 md:h-10 min-w-[220px] flex-1 sm:flex-none rounded-lg border border-gray-300 bg-white px-3 text-sm text-gray-900 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 disabled:bg-gray-50 disabled:text-gray-500"
			@change="$emit('update:modelValue', $event.target.value)"
		>
			<option v-for="o in options" :key="o.company" :value="o.company">{{ o.company }}</option>
		</select>
	</label>
</template>

<script setup>
import { __ } from "@/utils/translation"

// Shown only when the user may act for more than one company (managers);
// a cashier's shift company needs no choice.
defineProps({
	modelValue: { type: String, default: null },
	options: { type: Array, default: () => [] },
	label: { type: String, default: "" },
	disabled: { type: Boolean, default: false },
})
defineEmits(["update:modelValue"])
</script>
