<template>
	<Teleport to="body">
		<!-- Left overlay drawer for management menu — mobile/tablet only (< lg). -->
		<div class="fixed inset-0 z-[200] lg:hidden">
			<!-- Backdrop -->
			<div
				class="absolute inset-0 bg-black/50"
				data-testid="drawer-backdrop"
				@click="emit('close')"
			></div>

			<!-- Drawer panel -->
			<nav
				id="management-drawer"
				ref="panelRef"
				role="dialog"
				aria-modal="true"
				:aria-label="__('Management menu')"
				class="absolute inset-y-0 start-0 w-64 max-w-[80vw] bg-white shadow-xl flex flex-col"
			>
				<div class="flex items-center justify-between px-4 py-3 border-b border-gray-200">
					<span class="text-sm font-semibold text-gray-900">{{ __("Menu") }}</span>
					<button
						ref="closeBtnRef"
						type="button"
						class="p-1.5 rounded-lg text-gray-600 hover:bg-gray-100 hover:text-gray-900 active:bg-gray-200 transition-colors"
						:aria-label="__('Close menu')"
						@click="emit('close')"
					>
						<FeatherIcon name="x" class="w-5 h-5" />
					</button>
				</div>

				<div class="flex flex-col py-2 overflow-y-auto">
					<template v-for="item in visibleItems" :key="item.id">
						<div v-if="item.divider" class="w-full border-t border-gray-200 my-2"></div>
						<button
							type="button"
							class="flex items-center gap-3 px-4 py-2.5 text-sm text-gray-700 hover:bg-gray-100 active:bg-gray-200 transition-colors text-start"
							@click="selectItem(item.id)"
						>
							<FeatherIcon :name="item.icon" class="w-5 h-5 flex-shrink-0" />
							<span>{{ __(item.label) }}</span>
						</button>
					</template>
				</div>
			</nav>
		</div>
	</Teleport>
</template>

<script setup>
import { FeatherIcon } from "frappe-ui"
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue"
import { MANAGEMENT_MENU } from "./managementMenu"

const props = defineProps({
	showProduction: { type: Boolean, default: false },
})

const emit = defineEmits(["navigate", "close"])

const panelRef = ref(null)
const closeBtnRef = ref(null)

const visibleItems = computed(() =>
	MANAGEMENT_MENU.filter(
		(item) => !item.requiresProduction || props.showProduction,
	),
)

function selectItem(itemId) {
	emit("navigate", itemId)
	emit("close")
}

function getFocusable() {
	if (!panelRef.value) return []
	return Array.from(panelRef.value.querySelectorAll("button")).filter(
		(el) => !el.disabled,
	)
}

function onDocumentKeydown(event) {
	if (event.key === "Escape") {
		// Keep Escape from also reaching handlers behind the drawer.
		event.stopPropagation()
		emit("close")
		return
	}
	if (event.key !== "Tab") return

	const focusable = getFocusable()
	if (!focusable.length) return
	const first = focusable[0]
	const last = focusable[focusable.length - 1]
	const current = document.activeElement

	if (event.shiftKey && (current === first || !focusable.includes(current))) {
		event.preventDefault()
		last.focus()
	} else if (
		!event.shiftKey &&
		(current === last || !focusable.includes(current))
	) {
		event.preventDefault()
		first.focus()
	}
}

// Lock background scroll while the drawer is open.
const previousBodyOverflow = document.body.style.overflow
document.body.style.overflow = "hidden"

// Closing when the viewport reaches the desktop breakpoint (lg = 1024px),
// where the permanent icon sidebar takes over again.
const desktopQuery = window.matchMedia("(min-width: 1024px)")
function onDesktopChange(event) {
	if (event.matches) emit("close")
}

onMounted(() => {
	document.addEventListener("keydown", onDocumentKeydown, true)
	desktopQuery.addEventListener("change", onDesktopChange)
	nextTick(() => closeBtnRef.value?.focus())
})

onBeforeUnmount(() => {
	document.removeEventListener("keydown", onDocumentKeydown, true)
	desktopQuery.removeEventListener("change", onDesktopChange)
	document.body.style.overflow = previousBodyOverflow
})
</script>
