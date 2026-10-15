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
			:class="{ 'animate-spin': spinning }"
		/>
	</button>
</template>

<script setup>
import { FeatherIcon } from "frappe-ui"
import { onBeforeUnmount, ref, watch } from "vue"

// One quiet refresh control for every management page toolbar: icon spins
// while its page reloads, disabled until done, sized to match text inputs.
const props = defineProps({
	loading: { type: Boolean, default: false },
})

defineEmits(["click"])

// A reload that resolves within a frame would flip loading back off before the
// spin is visible; hold it for a short floor so the tap always reads as "it ran".
// shortcut: fixed floor, independent of how long the real request takes
const MIN_SPIN_MS = 600
const spinning = ref(props.loading)
let floorTimer = null

watch(
	() => props.loading,
	(val) => {
		if (val) {
			clearTimeout(floorTimer)
			spinning.value = true
		} else if (spinning.value) {
			floorTimer = setTimeout(() => {
				spinning.value = false
			}, MIN_SPIN_MS)
		}
	},
)

onBeforeUnmount(() => clearTimeout(floorTimer))
</script>
