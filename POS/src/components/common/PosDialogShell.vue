<template>
	<Dialog v-model="open" :options="{ title, size: '5xl' }">
		<template #body>
			<!-- One size for every cashier list dialog: fixed header/toolbar/footer, one scroll region -->
			<div
				class="flex flex-col max-h-[calc(100dvh-6rem)] md:h-[min(46rem,calc(100dvh-6rem))] text-start"
			>
				<header
					class="shrink-0 flex items-center justify-between gap-3 border-b border-gray-200 px-4 py-3 sm:px-5"
					data-test="dialog-header"
				>
					<div class="flex items-center gap-3 min-w-0">
						<div
							v-if="icon"
							class="w-9 h-9 shrink-0 rounded-lg bg-blue-50 text-blue-600 flex items-center justify-center"
						>
							<FeatherIcon :name="icon" class="w-5 h-5" />
						</div>
						<div class="min-w-0">
							<DialogTitle class="text-lg font-semibold leading-6 text-gray-900 truncate">
								{{ title }}
							</DialogTitle>
							<p v-if="subtitle" class="text-xs text-gray-500 truncate">{{ subtitle }}</p>
						</div>
					</div>
					<button
						type="button"
						class="w-9 h-9 shrink-0 rounded-lg flex items-center justify-center text-gray-500 hover:bg-gray-100 hover:text-gray-900"
						:aria-label="__('Close')"
						:title="__('Close')"
						@click="open = false"
					>
						<FeatherIcon name="x" class="w-5 h-5" />
					</button>
				</header>

				<div v-if="$slots.toolbar" class="shrink-0 px-4 pt-3 sm:px-5">
					<slot name="toolbar" />
				</div>

				<div class="min-h-0 flex-1 overflow-y-auto px-4 py-3 sm:px-5" data-test="dialog-body">
					<slot />
				</div>

				<footer
					class="shrink-0 flex items-center gap-2 border-t border-gray-200 px-4 py-2.5 sm:px-5"
					data-test="dialog-footer"
				>
					<slot name="footer" />
					<Button class="ms-auto" variant="subtle" @click="open = false">
						{{ __("Close") }}
					</Button>
				</footer>
			</div>
		</template>
	</Dialog>
</template>

<script setup>
import { Button, Dialog, FeatherIcon } from "frappe-ui"
import { DialogTitle } from "reka-ui"

defineProps({
	title: { type: String, required: true },
	subtitle: String,
	icon: String,
})

const open = defineModel({ type: Boolean, default: false })
</script>
