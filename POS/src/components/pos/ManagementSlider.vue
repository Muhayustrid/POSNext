<template>
	<!-- Icon-Only Sidebar - Hidden on Mobile, Visible on Desktop -->
	<div
		class="hidden lg:flex w-16 flex-shrink-0 bg-white border-e border-gray-200 flex-col items-center py-4 flex flex-col gap-2"
	>
		<template v-for="item in visibleItems" :key="item.id">
			<!-- Spacer + divider push settings to the bottom (same layout as before) -->
			<template v-if="item.divider">
				<div class="flex-1"></div>
				<div class="w-8 border-t border-gray-200 my-2"></div>
			</template>

			<button
				@click="handleMenuClick(item.id)"
				:class="[
					'w-12 h-12 rounded-lg flex items-center justify-center transition-all relative group',
					activeMenu === item.id
						? item.activeClass
						: 'text-gray-600 hover:bg-gray-100 hover:text-gray-900',
				]"
				:title="__(item.label)"
			>
				<FeatherIcon :name="item.icon" class="w-5 h-5" />
				<div
					class="absolute start-full ms-2 px-2 py-1 bg-gray-900 text-white text-xs rounded opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none whitespace-nowrap z-50"
				>
					{{ __(item.label) }}
				</div>
			</button>
		</template>
	</div>
</template>

<script setup>
import { FeatherIcon } from "frappe-ui"
import { computed, ref } from "vue"
import { MANAGEMENT_MENU } from "./managementMenu"

const props = defineProps({
	showProduction: { type: Boolean, default: false },
})

const emit = defineEmits(["menu-clicked"])

const activeMenu = ref("")

const visibleItems = computed(() =>
	MANAGEMENT_MENU.filter(
		(item) => !item.requiresProduction || props.showProduction,
	),
)

function handleMenuClick(menuItem) {
	activeMenu.value = menuItem
	emit("menu-clicked", menuItem)
}
</script>
