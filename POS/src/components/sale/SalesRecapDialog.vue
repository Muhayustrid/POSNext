<template>
	<Dialog v-model="show" :options="{ title: __('Sales Recap'), size: '4xl' }">
		<template #body>
			<div class="flex flex-col max-h-[calc(100dvh-6rem)] text-start">
				<div class="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-5" data-test="dialog-body">
					<!-- One dataset, two lenses: the open shift or a posting-date
					     window. SessionSummary owns the period picker, the recap
					     queries (union of POS Invoice + legacy Sales Invoice) and
					     its own print, so this dialog stays a frame around it. -->
					<SessionSummary :opening-shift="openingShift" :pos-profile="posProfile" />
				</div>
				<div
					class="flex shrink-0 justify-end border-t border-gray-200 px-4 py-2.5 sm:px-5"
					data-test="dialog-footer"
				>
					<Button variant="subtle" @click="show = false">
						{{ __("Close") }}
					</Button>
				</div>
			</div>
		</template>
	</Dialog>
</template>

<script setup>
import { Button, Dialog } from "frappe-ui"
import { ref, watch } from "vue"
import SessionSummary from "./SessionSummary.vue"

const props = defineProps({
	modelValue: Boolean,
	posProfile: { type: String, default: null },
	openingShift: { type: String, default: null },
})

const emit = defineEmits(["update:modelValue"])

const show = ref(props.modelValue)

watch(
	() => props.modelValue,
	(val) => {
		show.value = val
	},
)

watch(show, (val) => {
	emit("update:modelValue", val)
})
</script>
