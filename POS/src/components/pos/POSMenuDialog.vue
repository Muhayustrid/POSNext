<template>
	<!-- Application shell for the management menu: one overlay hosting every
	     management module as an embedded view, sharing a single sidebar.
	     Rendered as a full-screen page (still a mounted overlay, no routes).
	     z-[300] keeps it below frappe Dialog overlays (z-400/500) so nested
	     sub-dialogs (payment, invoice detail...) stack above it. -->
	<Transition name="fade">
		<div
			v-if="open"
			role="dialog"
			aria-modal="true"
			:aria-label="activeLabel"
			class="fixed inset-0 z-[300] bg-white overflow-hidden flex flex-col"
		>
			<!-- Header -->
			<div
				class="flex shrink-0 items-center gap-3 px-4 py-3 sm:px-5 sm:py-4 border-b border-gray-200"
			>
				<button
					type="button"
					class="flex items-center justify-center w-10 h-10 shrink-0 text-gray-700 hover:text-gray-900 transition-colors"
					:aria-label="__('Close menu')"
					:title="__('Back to POS')"
					@click="back"
				>
					<FeatherIcon name="arrow-left" class="w-5 h-5" />
				</button>
				<!-- < md: the burger lands on the menu list; the title follows it -->
				<FeatherIcon
					v-if="activeItem"
					:name="activeItem.icon"
					class="w-5 h-5 text-gray-700 shrink-0"
					:class="{ 'hidden md:block': mobileList }"
				/>
				<h2 class="text-lg font-semibold text-gray-900 truncate">
					<span v-if="mobileList" class="md:hidden">{{ __("Menu") }}</span>
					<span :class="{ 'hidden md:inline': mobileList }">{{ activeLabel }}</span>
				</h2>
			</div>

			<div class="flex-1 flex min-h-0 overflow-hidden">
				<!-- Menu list page (< md) -->
				<nav
					v-if="mobileList"
					class="md:hidden flex-1 overflow-y-auto px-2 pb-4"
					data-testid="pos-menu-list"
				>
					<template v-for="group in menuGroups" :key="group.id">
						<div
							class="px-3 pt-4 pb-1 text-[11px] font-semibold uppercase tracking-wider text-gray-400"
						>
							{{ __(group.label) }}
						</div>
						<button
							v-for="item in group.items"
							:key="item.id"
							type="button"
							class="flex w-full items-center gap-3 px-3 py-3.5 rounded-xl text-base text-gray-700 active:bg-gray-100 transition-colors text-start"
							@click="selectItem(item.id)"
						>
							<FeatherIcon :name="item.icon" class="w-5 h-5 shrink-0 text-gray-500" />
							<span class="flex-1 truncate">{{ __(item.label) }}</span>
							<FeatherIcon name="chevron-right" class="w-5 h-5 shrink-0 text-gray-300" />
						</button>
					</template>
				</nav>

				<!-- Sidebar (md+) -->
				<div
					class="hidden md:flex w-56 shrink-0 flex-col py-2 border-e border-gray-200 bg-white overflow-y-auto"
					data-testid="pos-menu-sidebar"
				>
					<template v-for="group in menuGroups" :key="group.id">
						<div
							class="px-4 pt-3 pb-1 text-[11px] font-semibold uppercase tracking-wider text-gray-400"
						>
							{{ __(group.label) }}
						</div>
						<button
							v-for="item in group.items"
							:key="item.id"
							type="button"
							:aria-current="activeView === item.id ? 'page' : undefined"
							class="flex items-center gap-3 mx-2 px-3 py-2.5 rounded-lg text-sm transition-colors text-start"
							:class="
								activeView === item.id
									? 'bg-blue-50 text-blue-700 font-semibold'
									: 'text-gray-700 hover:bg-gray-100 hover:text-gray-900'
							"
							@click="selectItem(item.id)"
						>
							<FeatherIcon :name="item.icon" class="w-4 h-4 shrink-0" />
							<span class="truncate">{{ __(item.label) }}</span>
						</button>
					</template>
				</div>

				<!-- Active view. $attrs forwards POSSale's module listeners
				     (view-invoice, print-invoice, promotion-saved, ...) onto
				     whichever view is mounted, so its handlers stay unchanged. -->
				<div
					class="flex-1 min-w-0 overflow-hidden"
					:class="{ 'hidden md:block': mobileList }"
				>
					<PromotionManagement
						v-if="activeView === 'promotions'"
						embedded
						:model-value="true"
						:pos-profile="posProfile"
						:company="company"
						:currency="currency"
						class="h-full"
						v-bind="$attrs"
					/>
					<POSSettings
						v-else-if="activeView === 'settings'"
						embedded
						:model-value="true"
						:pos-profile="posProfile"
						:current-warehouse="currentWarehouse"
						class="h-full"
						v-bind="$attrs"
					/>
					<InvoiceManagement
						v-else-if="activeView === 'invoices'"
						embedded
						:model-value="true"
						:pos-profile="posProfile"
						:currency="currency"
						:history-invoices="historyInvoices"
						:draft-invoices="draftInvoices"
						class="h-full"
						v-bind="$attrs"
						@load-draft="close"
					/>
					<template v-else-if="activeView === 'sales-recap'">
						<div class="h-full flex flex-col text-start">
							<div class="flex-1 min-h-0 overflow-y-auto px-4 py-4 sm:px-6">
								<SessionSummary
									:opening-shift="openingShift"
									:pos-profile="posProfile"
								/>
							</div>
						</div>
					</template>
					<template v-else-if="activeView === 'dashboard'">
						<div class="h-full flex flex-col text-start">
							<div class="flex-1 min-h-0 overflow-y-auto px-4 py-4 sm:px-6">
								<ShiftDashboard
									v-bind="$attrs"
									:opening-shift="openingShift"
									:pos-profile="posProfile"
									@navigate="selectItem"
								/>
							</div>
						</div>
					</template>
					<ProductsView
						v-else-if="activeView === 'products'"
						:pos-profile="posProfile"
						:company="company"
						:currency="currency"
					/>
					<ProductionDialog
						v-else-if="activeView === 'production'"
						embedded
						:model-value="true"
						:pos-profile="posProfile"
						:company="company"
						:currency="currency"
						class="h-full"
						v-bind="$attrs"
						@update:model-value="closeIfHidden"
					/>
					<PurchaseOrderDialog
						v-else-if="activeView === 'purchase-order'"
						embedded
						:model-value="true"
						:pos-profile="posProfile"
						:company="company"
						:warehouse="warehouse"
						:currency="currency"
						class="h-full"
						v-bind="$attrs"
						@update:model-value="closeIfHidden"
					/>
					<PurchaseReceiptDialog
						v-else-if="activeView === 'purchase-receipt'"
						embedded
						:model-value="true"
						:pos-profile="posProfile"
						class="h-full"
						v-bind="$attrs"
						@update:model-value="closeIfHidden"
					/>
				</div>
			</div>
		</div>
	</Transition>
</template>

<script setup>
import { Button, FeatherIcon } from "frappe-ui"
import { computed, ref, watch } from "vue"
import { MANAGEMENT_MENU, MENU_GROUPS } from "./managementMenu"
import PromotionManagement from "@/components/sale/PromotionManagement.vue"
import POSSettings from "@/components/settings/POSSettings.vue"
import InvoiceManagement from "@/components/invoices/InvoiceManagement.vue"
import SessionSummary from "@/components/sale/SessionSummary.vue"
import ShiftDashboard from "@/components/sale/ShiftDashboard.vue"
import ProductsView from "@/components/sale/ProductsView.vue"
import ProductionDialog from "@/components/pos/ProductionDialog.vue"
import PurchaseOrderDialog from "@/components/purchase/PurchaseOrderDialog.vue"
import PurchaseReceiptDialog from "@/components/purchase/PurchaseReceiptDialog.vue"

defineOptions({ inheritAttrs: false })

const props = defineProps({
	open: { type: Boolean, default: false },
	initialView: { type: String, default: null },
	posProfile: { type: String, default: null },
	company: { type: String, default: null },
	currency: { type: String, default: "" },
	warehouse: { type: String, default: null },
	currentWarehouse: { type: String, default: null },
	openingShift: { type: String, default: null },
	historyInvoices: { type: Array, default: () => [] },
	draftInvoices: { type: Array, default: () => [] },
	canPurchaseOrder: { type: Boolean, default: false },
	canProduction: { type: Boolean, default: false },
	isOffline: { type: Boolean, default: false },
})

const emit = defineEmits(["update:open", "menu-selected"])

const activeView = ref(null)
// < md the shell has no sidebar: the burger opens a full-page menu list and
// the back arrow steps view -> list -> POS. md+ ignores it (sidebar visible).
const mobileList = ref(false)

const visibleItems = computed(() =>
	MANAGEMENT_MENU.filter(
		(item) =>
			(!item.requiresProduction || (props.canProduction && !props.isOffline)) &&
			(!item.requiresPurchaseOrder ||
				(props.canPurchaseOrder && !props.isOffline)) &&
			(!item.requiresOnline || !props.isOffline),
	),
)

// Sidebar and the mobile menu list render items grouped (and ordered) by workflow.
const menuGroups = computed(() =>
	MENU_GROUPS.map((group) => ({
		...group,
		items: visibleItems.value.filter((item) => item.group === group.id),
	})).filter((group) => group.items.length > 0),
)

const activeItem = computed(() =>
	visibleItems.value.find((i) => i.id === activeView.value),
)
const activeLabel = computed(() =>
	activeItem.value ? __(activeItem.value.label) : __("Menu"),
)

// initialView is one-shot per open: every false -> true transition re-reads it.
// Without (or with an unavailable) initialView, the burger keeps its
// pre-dashboard landing: Invoice Management when visible, else the first
// available item. Dashboard stays first in the sidebar order itself.
watch(
	() => props.open,
	(open) => {
		if (!open) return
		const requested = props.initialView
		if (requested && visibleItems.value.some((i) => i.id === requested)) {
			activeView.value = requested
			mobileList.value = false
			return
		}
		mobileList.value = true
		const fallback =
			visibleItems.value.find((i) => i.id === "invoices") ||
			visibleItems.value[0]
		activeView.value = fallback?.id ?? null
	},
	{ immediate: true },
)

function isDesktop() {
	return window.matchMedia?.("(min-width: 768px)").matches ?? true
}

function back() {
	if (!mobileList.value && !isDesktop()) mobileList.value = true
	else close()
}

function selectItem(itemId) {
	mobileList.value = false
	activeView.value = itemId
	emit("menu-selected", itemId)
}

function close() {
	emit("update:open", false)
}

// DialogHost views keep their own close/cancel controls when embedded; the
// shell pins model-value to true, so a view closing itself means "leave".
function closeIfHidden(value) {
	if (!value) close()
}

</script>

<style scoped>
.fade-enter-active,
.fade-leave-active {
	transition: opacity 0.3s ease;
}

.fade-enter-from,
.fade-leave-to {
	opacity: 0;
}
</style>
