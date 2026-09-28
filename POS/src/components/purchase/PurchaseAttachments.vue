<template>
	<div class="space-y-1.5" data-test="purchase-attachments">
		<div v-if="editable" class="flex items-center gap-2">
			<button
				type="button"
				class="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-full border border-gray-300 text-gray-600 hover:bg-gray-50 transition-colors"
				data-test="attach-button"
				@click="picker?.click()"
			>
				<FeatherIcon name="paperclip" class="w-3.5 h-3.5" />
				{{ label || __("Attachments") }}
			</button>
			<span class="text-xs text-gray-400">{{ __("PDF or image · max 10 MB") }}</span>
			<input
				ref="picker"
				type="file"
				multiple
				:accept="ATTACHMENT_ACCEPT"
				class="hidden"
				data-test="attach-input"
				@change="onPick"
			/>
		</div>
		<div v-else-if="label && attached.length" class="text-xs font-medium text-gray-600">{{ label }}</div>
		<ul
			v-if="rows.length"
			class="divide-y divide-gray-100 rounded-lg border border-gray-100 bg-white"
			data-test="attachment-list"
		>
			<li
				v-for="(row, i) in rows"
				:key="row.file_url || row.__key || `${row.name}-${i}`"
				class="flex items-center gap-2 px-2.5 py-1.5"
			>
				<FeatherIcon name="file-text" class="w-3.5 h-3.5 text-gray-400 shrink-0" />
				<a
					v-if="row.file_url"
					:href="row.file_url"
					target="_blank"
					rel="noopener"
					class="text-sm text-gray-700 hover:text-gray-900 truncate min-w-0"
				>
					{{ row.file_name }}
				</a>
				<span v-else class="text-sm text-gray-700 truncate min-w-0">{{ row.file_name || row.name }}</span>
				<span class="ml-auto text-xs text-gray-400 shrink-0 tabular-nums">
					{{ formatBytes(row.file_size ?? row.size) }}
				</span>
				<button
					v-if="editable"
					type="button"
					class="text-gray-400 hover:text-red-500 shrink-0"
					:aria-label="__('Remove')"
					@click="emit('remove', row)"
				>
					×
				</button>
			</li>
		</ul>
	</div>
</template>

<script setup>
import { FeatherIcon } from "frappe-ui"
import { computed, ref } from "vue"

import { ATTACHMENT_ACCEPT, formatBytes } from "@/utils/attachments"

const props = defineProps({
	// summary rows from the API ({file_name, file_url, file_size}) — read-only mode
	attached: { type: Array, default: () => [] },
	// picked File objects — edit mode; the parent owns the array and uploads after save
	pending: { type: Array, default: () => [] },
	editable: { type: Boolean, default: false },
	label: { type: String, default: "" },
})

const emit = defineEmits(["add", "remove"])

const picker = ref(null)
// summary rows carry `name`; picked File objects carry `name` too — keep both shapes here
const rows = computed(() => (props.editable ? props.pending : props.attached))

function onPick(event) {
	emit("add", Array.from(event.target.files || []))
	event.target.value = ""
}
</script>
