<template>
	<!-- Application shell for the management menu: one overlay hosting every
	     management module as an embedded view, sharing a single sidebar.
	     z-[300] keeps it below frappe Dialog overlays (z-400/500) so nested
	     sub-dialogs (payment, invoice detail...) stack above it. -->
	<Transition name="fade">
		<div
			v-if="open"
			class="fixed inset-0 bg-black bg-opacity-50 z-[300]"
			@click.self="close"
		>
			<div class="fixed inset-0 flex items-center justify-center sm:p-4">
				<div
					role="dialog"
					aria-modal="true"
					:aria-label="activeLabel"
					class="w-full h-full sm:w-[95vw] sm:h-[92vh] max-w-none bg-white sm:rounded-xl shadow-2xl overflow-hidden flex flex-col"
				>
					<!-- Header -->
					<div
						class="flex shrink-0 items-center justify-between px-4 py-3 sm:px-5 sm:py-4 border-b border-gray-200"
					>
						<div class="flex items-center gap-3 min-w-0">
							<FeatherIcon
								v-if="activeItem"
								:name="activeItem.icon"
								class="w-5 h-5 text-gray-700 shrink-0"
							/>
							<h2 class="text-lg font-semibold text-gray-900 truncate">
								{{ activeLabel }}
							</h2>
						</div>
						<button
							type="button"
							class="p-2 rounded-lg text-gray-600 hover:bg-gray-100 hover:text-gray-900 active:bg-gray-200 transition-colors"
							:aria-label="__('Close menu')"
							@click="close"
						>
							<FeatherIcon name="x" class="w-5 h-5" />
						</button>
					</div>

					<!-- Menu chips (< lg): wrapped, so every destination stays visible
					     without horizontal scrolling on phones -->
						<div
							class="lg:hidden shrink-0 border-b border-gray-200 bg-white px-3 py-2"
						>
							<div class="flex flex-wrap items-center gap-2">
							<button
								v-for="item in visibleItems"
								:key="item.id"
								type="button"
								class="flex shrink-0 items-center gap-1.5 px-3 py-1.5 rounded-full text-sm font-medium transition-colors"
								:class="
									activeView === item.id
										? item.activeClass
										: 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
								"
								@click="selectItem(item.id)"
							>
								<FeatherIcon :name="item.icon" class="w-4 h-4" />
								<span>{{ __(item.label) }}</span>
							</button>
						</div>
					</div>

					<div class="flex-1 flex min-h-0 overflow-hidden">
						<!-- Sidebar (lg+) -->
						<div
							class="hidden lg:flex w-56 shrink-0 flex-col py-2 border-e border-gray-200 bg-white overflow-y-auto"
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
									class="flex items-center gap-3 mx-2 px-3 py-2.5 rounded-lg text-sm transition-colors text-start"
									:class="
										activeView === item.id
											? item.activeClass
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
						<div class="flex-1 min-w-0 overflow-hidden">
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
											ref="summaryEl"
											:opening-shift="openingShift"
											:pos-profile="posProfile"
										/>
									</div>
									<!-- Same fixed print footer as the old SalesRecapDialog -->
									<div
										class="flex shrink-0 items-center border-t border-gray-200 px-4 pb-4 pt-3 sm:px-6"
									>
										<Button
											variant="subtle"
											theme="blue"
											:loading="printing"
											:disabled="!canPrint"
											@click="printRecap"
										>
											{{ __("Print") }}
										</Button>
									</div>
								</div>
							</template>
							<WarehouseAvailabilityDialog
								v-else-if="activeView === 'products'"
								embedded
								:model-value="true"
								mode="search"
								:pos-profile="posProfile"
								:company="company"
								class="h-full"
								v-bind="$attrs"
								@close="close"
								@update:model-value="closeIfHidden"
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
import WarehouseAvailabilityDialog from "@/components/sale/WarehouseAvailabilityDialog.vue"
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

const visibleItems = computed(() =>
	MANAGEMENT_MENU.filter(
		(item) =>
			(!item.requiresProduction || (props.canProduction && !props.isOffline)) &&
			(!item.requiresPurchaseOrder ||
				(props.canPurchaseOrder && !props.isOffline)),
	),
)

// Sidebar renders items grouped (and ordered) by workflow; the mobile chip
// bar stays flat, following the same order.
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

// initialView is one-shot per open: every false -> true transition re-reads it,
// so reopening without one falls back to the first available item.
watch(
	() => props.open,
	(open) => {
		if (!open) return
		const requested = props.initialView
		activeView.value =
			requested && visibleItems.value.some((i) => i.id === requested)
				? requested
				: (visibleItems.value[0]?.id ?? null)
	},
	{ immediate: true },
)

function selectItem(itemId) {
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

// Sales recap print — same gating as the old SalesRecapDialog footer: disabled
// until SessionSummary has a recap loaded, busy while it prints.
const summaryEl = ref(null)
const printing = ref(false)
const canPrint = ref(false)

watch(summaryEl, (el) => {
	canPrint.value = Boolean(el?.printable)
})
watch(
	() => summaryEl.value?.printable,
	(val) => {
		canPrint.value = Boolean(val)
	},
)

async function printRecap() {
	if (!canPrint.value || printing.value) return
	printing.value = true
	try {
		await summaryEl.value?.print()
	} finally {
		printing.value = false
	}
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
