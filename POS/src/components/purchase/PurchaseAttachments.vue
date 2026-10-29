<template>
	<div class="space-y-1.5" data-test="purchase-attachments">
		<div v-if="editable" class="flex items-center gap-2">
			<button
				type="button"
				class="inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap px-2.5 py-1 text-xs rounded-full border border-gray-300 text-gray-600 hover:bg-gray-50 transition-colors"
				data-test="attach-button"
				@click="picker?.click()"
			>
				<FeatherIcon name="paperclip" class="w-3.5 h-3.5" />
				{{ label || __("Attachments") }}
			</button>
			<span class="text-xs text-gray-400">{{ hint || __("PDF, image, Excel or CSV · max 10 MB") }}</span>
			<input
				ref="picker"
				type="file"
				multiple
				:accept="accept"
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
				<button
					v-if="isImage(row) && srcOf(row)"
					type="button"
					class="flex min-w-0 items-center gap-2 text-start text-sm text-gray-700 hover:text-gray-900"
					data-test="attachment-open"
					:aria-label="__('Preview {0}', [nameOf(row)])"
					@click="previewRow = row"
				>
					<img
						:src="srcOf(row)"
						:alt="nameOf(row)"
						class="h-10 w-10 shrink-0 rounded border border-gray-200 object-cover"
						data-test="attachment-thumb"
					/>
					<span class="truncate">{{ nameOf(row) }}</span>
				</button>
				<a
					v-else-if="row.file_url"
					:href="row.file_url"
					target="_blank"
					rel="noopener"
					class="flex min-w-0 items-center gap-2 text-sm text-gray-700 hover:text-gray-900"
					data-test="attachment-open"
				>
					<FeatherIcon name="file-text" class="w-3.5 h-3.5 text-gray-400 shrink-0" />
					<span class="truncate">{{ nameOf(row) }}</span>
				</a>
				<span v-else class="flex min-w-0 items-center gap-2 text-sm text-gray-700">
					<FeatherIcon name="file-text" class="w-3.5 h-3.5 text-gray-400 shrink-0" />
					<span class="truncate">{{ nameOf(row) }}</span>
				</span>
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
		<Teleport to="body">
			<div
				v-if="previewRow"
				class="fixed inset-0 z-[600] flex flex-col bg-black/80"
				role="dialog"
				aria-modal="true"
				:aria-label="nameOf(previewRow)"
				data-test="attachment-preview"
				@click.self="previewRow = null"
			>
				<div class="flex items-center gap-3 bg-black px-4 py-3 text-white">
					<span class="min-w-0 flex-1 truncate text-sm">{{ nameOf(previewRow) }}</span>
					<a
						v-if="previewRow.file_url"
						:href="previewRow.file_url"
						target="_blank"
						rel="noopener"
						class="inline-flex h-11 items-center gap-1.5 rounded-lg px-3 text-sm hover:bg-white/10"
					>
						<FeatherIcon name="external-link" class="w-4 h-4" />
						{{ __("Open") }}
					</a>
					<button
						type="button"
						class="inline-flex h-11 w-11 items-center justify-center rounded-lg hover:bg-white/10"
						:aria-label="__('Close')"
						@click="previewRow = null"
					>
						<FeatherIcon name="x" class="w-5 h-5" />
					</button>
				</div>
				<div class="flex min-h-0 flex-1 items-center justify-center p-4" @click.self="previewRow = null">
					<img :src="srcOf(previewRow)" :alt="nameOf(previewRow)" class="max-h-full max-w-full object-contain" />
				</div>
			</div>
		</Teleport>
	</div>
</template>

<script setup>
import { FeatherIcon } from "frappe-ui"
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue"

import { ATTACHMENT_ACCEPT, formatBytes } from "@/utils/attachments"

const props = defineProps({
	// summary rows from the API ({file_name, file_url, file_size}) — read-only mode
	attached: { type: Array, default: () => [] },
	// picked File objects — edit mode; the parent owns the array and uploads after save
	pending: { type: Array, default: () => [] },
	editable: { type: Boolean, default: false },
	label: { type: String, default: "" },
	accept: { type: String, default: ATTACHMENT_ACCEPT },
	hint: { type: String, default: "" },
})

const emit = defineEmits(["add", "remove"])

const picker = ref(null)
// summary rows carry `name`; picked File objects carry `name` too — keep both shapes here
const rows = computed(() => (props.editable ? props.pending : props.attached))

const IMAGE_EXT = /\.(png|jpe?g|gif|webp|bmp|heic|heif|svg)$/i
const previewRow = ref(null)

// picked File objects have no URL yet: one object URL each, revoked when dropped
const blobUrls = new Map()
watch(
	() => props.pending,
	(files) => {
		for (const [file, url] of blobUrls) {
			if (!files.includes(file)) {
				URL.revokeObjectURL(url)
				blobUrls.delete(file)
			}
		}
	},
	{ deep: false },
)

function nameOf(row) {
	return row.file_name || row.name || ""
}

function isImage(row) {
	if (row instanceof File) return row.type.startsWith("image/")
	return IMAGE_EXT.test(row.file_url || row.file_name || "")
}

function srcOf(row) {
	if (row.file_url) return row.file_url
	if (!(row instanceof File) || !URL.createObjectURL) return null
	if (!blobUrls.has(row)) blobUrls.set(row, URL.createObjectURL(row))
	return blobUrls.get(row)
}

function onKeydown(event) {
	if (event.key === "Escape" && previewRow.value) previewRow.value = null
}
onMounted(() => document.addEventListener("keydown", onKeydown))
onBeforeUnmount(() => {
	document.removeEventListener("keydown", onKeydown)
	for (const url of blobUrls.values()) URL.revokeObjectURL(url)
})

function onPick(event) {
	emit("add", Array.from(event.target.files || []))
	event.target.value = ""
}
</script>
