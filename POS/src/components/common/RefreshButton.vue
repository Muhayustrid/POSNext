<template>
	<button
		type="button"
		data-test="refresh-button"
		:disabled="loading"
		:aria-label="__('Refresh')"
		:title="__('Refresh')"
		class="shrink-0 px-3 py-2 text-gray-600 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 hover:text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-1 transition-colors disabled:opacity-60 disabled:cursor-wait"
		@click="$emit('click')"
	>
		<FeatherIcon
			name="refresh-cw"
			class="w-4 h-4"
			:class="{ 'animate-spin': loading }"
		/>
	</button>
</template>

<script setup>
import { FeatherIcon } from "frappe-ui"

// One quiet refresh control for every management page toolbar: icon spins
// while its page reloads, disabled until done, sized to match text inputs.
defineProps({
	loading: { type: Boolean, default: false },
})

defineEmits(["click"])
</script>
