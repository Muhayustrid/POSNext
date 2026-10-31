<template>
	<div
		v-if="options.length > 1"
		class="flex flex-wrap items-center gap-2 text-sm text-gray-600"
		data-test="company-picker"
	>
		<span class="font-medium">{{ label || __("Company") }}</span>
		<div class="min-w-[220px] flex-1 sm:flex-none sm:w-80">
			<AutocompleteSelect
				:model-value="modelValue || ''"
				:options="companyOptions"
				:placeholder="__('Search company...')"
				required
				:class="{ 'pointer-events-none opacity-60': disabled }"
				@update:model-value="(v) => v && $emit('update:modelValue', v)"
			/>
		</div>
	</div>
</template>

<script setup>
import { computed } from "vue"
import AutocompleteSelect from "@/components/common/AutocompleteSelect.vue"
import { __ } from "@/utils/translation"

// Shown only when the user may act for more than one company (managers);
// a cashier's shift company needs no choice.
const props = defineProps({
	modelValue: { type: String, default: null },
	options: { type: Array, default: () => [] },
	label: { type: String, default: "" },
	disabled: { type: Boolean, default: false },
})
defineEmits(["update:modelValue"])

const companyOptions = computed(() =>
	props.options.map((o) => ({ value: o.company, label: o.company })),
)
</script>
