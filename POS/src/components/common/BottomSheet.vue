<template>
	<!-- Mobile action sheet: slides up from the bottom, closes on backdrop
	     tap or Escape. z-[450] sits above the management shell (z-300) and
	     the invoice drawer (z-400). -->
	<Teleport to="body">
		<Transition name="sheet">
			<div
				v-if="open"
				class="fixed inset-0 z-[450] flex items-end bg-black/40"
				data-test="bottom-sheet"
				@click.self="close"
			>
				<div
					role="dialog"
					aria-modal="true"
					:aria-label="title"
					class="sheet-panel w-full max-h-[85vh] overflow-y-auto rounded-t-2xl bg-white pb-[env(safe-area-inset-bottom)] shadow-xl"
				>
					<div class="flex justify-center pt-2.5 pb-1" aria-hidden="true">
						<div class="h-1 w-10 rounded-full bg-gray-300"></div>
					</div>
					<div v-if="title" class="px-5 pt-1 pb-2 text-sm font-semibold text-gray-900">
						{{ title }}
					</div>
					<div class="px-2 pb-4">
						<slot :close="close" />
					</div>
				</div>
			</div>
		</Transition>
	</Teleport>
</template>

<script setup>
import { onBeforeUnmount, watch } from "vue"

const props = defineProps({
	open: { type: Boolean, default: false },
	title: { type: String, default: "" },
})
const emit = defineEmits(["update:open"])

function close() {
	emit("update:open", false)
}

function onKeydown(e) {
	if (e.key === "Escape") close()
}

watch(
	() => props.open,
	(open) => {
		if (open) window.addEventListener("keydown", onKeydown)
		else window.removeEventListener("keydown", onKeydown)
	},
	{ immediate: true },
)
onBeforeUnmount(() => window.removeEventListener("keydown", onKeydown))
</script>

<style scoped>
.sheet-enter-active,
.sheet-leave-active {
	transition: background-color 0.2s ease;
}
.sheet-enter-active .sheet-panel,
.sheet-leave-active .sheet-panel {
	transition: transform 0.25s ease;
}
.sheet-enter-from,
.sheet-leave-to {
	background-color: transparent;
}
.sheet-enter-from .sheet-panel,
.sheet-leave-to .sheet-panel {
	transform: translateY(100%);
}
</style>
