<template>
	<DialogHost
		v-model:show="show"
		:embedded="embedded"
		:options="{ title: dialogTitle, size: 'lg' }"
	>
		<template #body-content>
			<div class="flex flex-col gap-3">
			<div class="flex gap-2">
				<input
					v-model="searchTerm"
					type="text"
					data-test="receipt-list-search"
					:placeholder="__('Search receipt or supplier...')"
					class="flex-1 px-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
					@input="onSearch"
				/>
				<RefreshButton :loading="loadingReceipts" @click="loadReceipts" />
			</div>

				<div class="flex flex-wrap gap-1">
					<button
						v-for="chip in STATUS_CHIPS"
						:key="chip.value"
						type="button"
						class="px-2.5 py-1 text-xs rounded-full border transition-colors"
						:class="
							statusFilter === chip.value
								? 'bg-blue-600 text-white border-blue-600'
								: 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50'
						"
						@click="setStatus(chip.value)"
					>
						{{ __(chip.label) }}
					</button>
				</div>

				<div
					v-if="loadingReceipts"
					class="flex items-center justify-center gap-2 py-8 text-sm text-gray-500"
				>
					<LoadingIndicator class="w-4 h-4" />
					{{ __("Loading...") }}
				</div>
				<div
					v-else-if="receipts.length === 0"
					class="py-8 text-center"
				>
					<p class="text-sm font-medium text-gray-900">
						{{ __("No purchase receipts") }}
					</p>
					<p class="text-xs text-gray-400 mt-1">
						{{ __("Receipts appear here once goods are received") }}
					</p>
				</div>
				<div v-else class="flex flex-col gap-2 overflow-y-auto">
					<div
						v-for="receipt in receipts"
						:key="receipt.name"
						class="border border-gray-200 rounded-lg p-3 cursor-pointer hover:border-gray-300 transition-colors"
						:data-test="`pr-${receipt.name}`"
						@click="toggleReceipt(receipt)"
					>
						<div class="flex items-start justify-between gap-3">
							<div class="min-w-0">
								<div class="flex items-center gap-2">
									<span class="text-sm font-semibold text-gray-900">{{ receipt.name }}</span>
									<StatusBadge
										:variant="statusVariant(receipt.status)"
										size="xs"
										:text="receipt.status"
									/>
								</div>
								<p class="text-xs text-gray-500 mt-0.5 truncate">{{ receipt.supplier_name }}</p>
								<p class="text-xs text-gray-400 mt-0.5">
									{{ formatDate(receipt.posting_date) }}
								</p>
							</div>
							<div class="flex items-start gap-2 shrink-0">
								<div class="text-right">
									<p class="text-sm font-medium text-gray-900">
										{{ formatMoney(receipt) }}
									</p>
									<p v-if="receipt.docstatus === 1" class="text-xs text-gray-400 mt-0.5">
										{{ __("Billed {0}%", [Math.round(receipt.per_billed || 0)]) }}
									</p>
								</div>
								<FeatherIcon
									name="chevron-down"
									class="w-4 h-4 mt-1 text-gray-400 transition-transform"
									:class="{ 'rotate-180': expandedReceipt === receipt.name }"
								/>
							</div>
						</div>

						<!-- item peek: lazy-loaded once per receipt, cached for the session -->
						<div
							v-if="expandedReceipt === receipt.name"
							class="mt-2 pt-2 border-t border-gray-100"
							@click.stop
						>
							<div
								v-if="loadingDetail && !receiptDetails[receipt.name]"
								class="py-2 text-center text-xs text-gray-400"
							>
								{{ __("Loading...") }}
							</div>
							<div v-else-if="receiptDetails[receipt.name]?.length" class="flex flex-col">
								<div
									v-for="row in receiptDetails[receipt.name]"
									:key="row.name"
									class="flex items-center justify-between gap-3 py-1.5 text-xs"
								>
									<span class="min-w-0 truncate text-gray-700">
										{{ row.item_name || row.item_code }}
									</span>
									<span class="shrink-0 font-medium text-gray-900">
										{{ formatQty(row.qty) }} {{ row.uom }}
									</span>
								</div>
							</div>
							<p v-else class="py-2 text-center text-xs text-gray-400">
								{{ __("No items") }}
							</p>
						</div>

						<div
							class="flex flex-wrap gap-1 mt-2 pt-2 border-t border-gray-100"
							@click.stop
						>
							<button
								type="button"
								class="px-2 py-1 text-xs rounded text-gray-500 hover:bg-gray-100"
								@click="openInErpnext(receipt)"
							>
								{{ __("Open in ERPNext") }}
							</button>
						</div>
					</div>
				</div>
			</div>
		</template>
	</DialogHost>
</template>

<script setup>
import { FeatherIcon } from "frappe-ui"
import DialogHost from "@/components/common/DialogHost.js"
import RefreshButton from "@/components/common/RefreshButton.vue"
import StatusBadge from "@/components/common/StatusBadge.vue"
import { useToast } from "@/composables/useToast"
import { useFormatters } from "@/composables/useFormatters"
import { call, serverErrorMessage } from "@/utils/apiWrapper"
import { parseError } from "@/utils/errorHandler"
import { computed, ref, watch } from "vue"

const props = defineProps({
	embedded: { type: Boolean, default: false },
	modelValue: { type: Boolean, default: true },
	posProfile: { type: String, default: null },
})

const emit = defineEmits(["update:modelValue"])

const PR_API = "pos_next.api.purchase_receipts"

// Receipts are born from the factory DN or the Receive flow, so this page is
// read-only by design — no create, submit or cancel from the POS.
const STATUS_CHIPS = [
	{ value: "", label: __("All") },
	{ value: "Draft", label: __("Draft") },
	{ value: "To Bill", label: __("To Bill") },
	{ value: "Completed", label: __("Completed") },
	{ value: "Cancelled", label: __("Cancelled") },
]

const STATUS_VARIANTS = {
	Draft: "orange",
	"To Bill": "blue",
	Completed: "green",
	Cancelled: "red",
	Closed: "gray",
}

const { showError } = useToast()
const { formatDate } = useFormatters()

const show = ref(props.modelValue)
watch(show, (val) => emit("update:modelValue", val))

const receipts = ref([])
const loadingReceipts = ref(false)
// expand-to-peek: item rows are fetched once per receipt and cached
const expandedReceipt = ref(null)
const receiptDetails = ref({})
const loadingDetail = ref(false)
const searchTerm = ref("")
const statusFilter = ref("")
let searchTimer = null

watch(
	() => props.modelValue,
	(val) => {
		show.value = val
		if (val) loadReceipts()
		else clearTimeout(searchTimer)
	},
	{ immediate: true },
)

const dialogTitle = computed(() => __("Purchase Receipts"))

function statusVariant(status) {
	return STATUS_VARIANTS[status] || "gray"
}

function formatMoney(receipt) {
	const currency = receipt.currency || "Rp"
	return `${currency} ${Number(receipt.grand_total || 0).toLocaleString("id-ID")}`
}

function formatQty(value) {
	return Number(value || 0).toLocaleString("id-ID")
}

async function toggleReceipt(receipt) {
	if (expandedReceipt.value === receipt.name) {
		expandedReceipt.value = null
		return
	}
	expandedReceipt.value = receipt.name
	if (receiptDetails.value[receipt.name]) return
	loadingDetail.value = true
	try {
		const res = await call(`${PR_API}.get_purchase_receipt`, { name: receipt.name })
		receiptDetails.value[receipt.name] = res?.items || []
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
		expandedReceipt.value = null
	} finally {
		loadingDetail.value = false
	}
}

async function loadReceipts() {
	loadingReceipts.value = true
	try {
		const res = await call(`${PR_API}.get_purchase_receipts`, {
			pos_profile: props.posProfile,
			status: statusFilter.value || null,
			search_term: searchTerm.value || null,
			limit: 50,
		})
		receipts.value = res?.receipts || []
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	} finally {
		loadingReceipts.value = false
	}
}

function onSearch() {
	clearTimeout(searchTimer)
	searchTimer = setTimeout(loadReceipts, 300)
}

function setStatus(value) {
	statusFilter.value = value
	loadReceipts()
}

function openInErpnext(receipt) {
	window.open(`/app/purchase-receipt/${receipt.name}`, "_blank")
}
</script>
