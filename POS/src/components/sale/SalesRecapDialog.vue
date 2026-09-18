<template>
	<Dialog v-model="show" :options="{ title: __('Sales Recap'), size: '4xl' }">
		<template #body>
			<div class="flex flex-col max-h-[calc(100dvh-6rem)] text-start">
				<div class="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-5" data-test="dialog-body">
					<!-- One dataset, two lenses: the open shift or a posting-date
					     window. SessionSummary owns the period picker, the recap
					     queries (union of POS Invoice + legacy Sales Invoice) and
					     the print call, so this dialog stays a frame around it. -->
					<SessionSummary
						ref="summaryEl"
						:opening-shift="openingShift"
						:pos-profile="posProfile"
					/>
				</div>
				<!-- Print lives in the fixed footer: inside the scroll region it
				     was an unlabelled icon that scrolled out of sight, which read
				     as "there is no print button". -->
				<div
					class="flex shrink-0 items-center justify-between border-t border-gray-200 px-4 py-2.5 sm:px-5"
					data-test="dialog-footer"
				>
					<Button
						variant="subtle"
						theme="blue"
						:loading="printing"
						:disabled="!canPrint"
						@click="print"
					>
						{{ __("Print") }}
					</Button>
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
const summaryEl = ref(null)
// Mirrored from the child: the footer button must stay disabled until a recap
// is actually loaded, and show progress while the child prints. The child
// owns the fetch and the success/error toasts.
const printing = ref(false)
const canPrint = ref(false)

async function print() {
	if (!canPrint.value || printing.value) return
	printing.value = true
	try {
		await summaryEl.value?.print()
	} finally {
		printing.value = false
	}
}

watch(
	() => props.modelValue,
	(val) => {
		show.value = val
	},
)

watch(show, (val) => {
	emit("update:modelValue", val)
})

// Re-read on mount and whenever the child finishes loading a recap.
watch(summaryEl, (el) => {
	canPrint.value = Boolean(el?.printable)
})
watch(
	() => summaryEl.value?.printable,
	(val) => {
		canPrint.value = Boolean(val)
	},
)
</script>
