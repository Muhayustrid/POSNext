<template>
	<DialogHost
		v-model:show="show"
		:embedded="embedded"
		:options="{ title: dialogTitle, size: '4xl' }"
	>
		<template #body-content>
			<div class="mx-auto flex w-full max-w-6xl flex-col gap-3">
				<p class="text-sm text-gray-500">
					{{ __("Goods received from suppliers and the factory") }}
				</p>

				<div class="flex gap-2">
					<div class="relative flex-1">
						<FeatherIcon
							name="search"
							class="pointer-events-none absolute start-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400"
						/>
						<input
							v-model="searchTerm"
							type="text"
							data-test="receipt-list-search"
							:placeholder="__('Search receipt or supplier...')"
							class="w-full rounded-lg border border-gray-300 py-2 pe-3 ps-9 text-sm focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30"
							@input="onSearch"
						/>
					</div>
					<RefreshButton :loading="loadingReceipts" @click="loadReceipts" />
				</div>

				<div class="-mx-1 flex gap-1 overflow-x-auto px-1 pb-0.5" role="group" :aria-label="__('Status')">
					<button
						v-for="chip in STATUS_CHIPS"
						:key="chip.value"
						type="button"
						class="shrink-0 rounded-full border px-3 py-1 text-xs font-medium transition-colors"
						:class="
							statusFilter === chip.value
								? 'border-gray-900 bg-gray-900 text-white'
								: 'border-gray-200 bg-white text-gray-600 hover:border-gray-300 hover:text-gray-900'
						"
						:aria-pressed="statusFilter === chip.value"
						@click="setStatus(chip.value)"
					>
						{{ chip.label }}
					</button>
				</div>

				<div class="rounded-xl border border-gray-200 bg-white">
					<!-- desktop column header; rows below share the same grid -->
					<div
						class="hidden border-b border-gray-200 bg-gray-50 px-4 py-2 text-xs font-medium uppercase tracking-wide text-gray-500 md:grid md:grid-cols-[minmax(0,1.4fr)_minmax(0,1.6fr)_8.5rem_8rem_minmax(0,1.2fr)_2.5rem] md:gap-x-3"
					>
						<span>{{ __("Receipt No") }}</span>
						<span>{{ __("Supplier") }}</span>
						<span>{{ __("Posting Date") }}</span>
						<span>{{ __("Status") }}</span>
						<span class="text-end">{{ __("Grand Total") }}</span>
						<span></span>
					</div>

					<div v-if="loadingReceipts" class="divide-y divide-gray-100" data-test="list-loading">
						<div v-for="n in 3" :key="n" class="flex animate-pulse gap-4 px-4 py-3">
							<div class="h-4 w-28 rounded bg-gray-100"></div>
							<div class="h-4 flex-1 rounded bg-gray-100"></div>
							<div class="h-4 w-20 rounded bg-gray-100"></div>
						</div>
					</div>
					<div v-else-if="receipts.length === 0" class="px-4 py-6 text-center">
						<p class="text-sm font-medium text-gray-900">{{ __("No purchase receipts") }}</p>
						<p class="mt-1 text-xs text-gray-500">
							{{
								searchTerm || statusFilter
									? __("Try a different search or status")
									: __("Receipts appear here once goods are received")
							}}
						</p>
					</div>
					<div v-else class="divide-y divide-gray-100">
						<div
							v-for="receipt in receipts"
							:key="receipt.name"
							class="cursor-pointer px-4 py-3 transition-colors hover:bg-gray-50"
							:class="{ 'bg-gray-50': expandedReceipt === receipt.name }"
							:data-test="`pr-${receipt.name}`"
							@click="toggleReceipt(receipt)"
						>
							<div class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 md:gap-y-0 md:grid-cols-[minmax(0,1.4fr)_minmax(0,1.6fr)_8.5rem_8rem_minmax(0,1.2fr)_2.5rem] md:gap-x-3">
								<div class="flex min-w-0 items-center gap-1.5 md:order-1">
									<FeatherIcon
										name="chevron-right"
										class="h-3.5 w-3.5 shrink-0 text-gray-400 transition-transform"
										:class="{ 'rotate-90': expandedReceipt === receipt.name }"
									/>
									<span class="truncate text-sm font-semibold text-gray-900">{{ receipt.name }}</span>
									<span
										v-if="receipt.attachment_count"
										class="inline-flex shrink-0 items-center gap-0.5 text-xs text-gray-500"
										:title="__('Attachments')"
									>
										<FeatherIcon name="paperclip" class="h-3 w-3" />
										{{ receipt.attachment_count }}
									</span>
								</div>
								<!-- status sits top-right on cards, in its own column on desktop -->
								<div class="justify-self-end md:order-4 md:justify-self-start">
									<span :class="statusPill(receipt.status)">{{ __(receipt.status) }}</span>
								</div>
								<p class="col-span-2 truncate text-sm text-gray-700 md:order-2 md:col-span-1">
									{{ receipt.supplier_name || receipt.supplier }}
								</p>
								<p class="text-xs text-gray-500 md:order-3 md:text-sm md:text-gray-700">
									{{ formatDate(receipt.posting_date) }}
								</p>
								<div class="text-end md:order-5">
									<p class="text-sm font-medium tabular-nums text-gray-900">
										{{ formatMoney(receipt) }}
									</p>
									<p v-if="receipt.docstatus === 1" class="text-xs text-gray-500">
										{{ __("Billed {0}%", [Math.round(receipt.per_billed || 0)]) }}
									</p>
								</div>
								<div class="hidden justify-end md:order-6 md:flex" @click.stop>
									<button
										type="button"
										class="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700"
										:title="__('Open in ERPNext')"
										:aria-label="__('Open in ERPNext')"
										@click="openInErpnext(receipt)"
									>
										<FeatherIcon name="external-link" class="h-3.5 w-3.5" />
									</button>
								</div>
							</div>

							<!-- item peek: lazy-loaded once per receipt, cached for the session -->
							<div
								v-if="expandedReceipt === receipt.name"
								class="mt-2 rounded-lg border border-gray-200 bg-white px-3 py-1 md:ms-5"
								@click.stop
							>
								<div
									v-if="loadingDetail && !receiptDetails[receipt.name]"
									class="py-2 text-center text-xs text-gray-400"
								>
									{{ __("Loading...") }}
								</div>
								<div
									v-else-if="receiptDetails[receipt.name]?.items?.length"
									class="divide-y divide-gray-100"
								>
									<div
										v-for="row in receiptDetails[receipt.name].items"
										:key="row.name"
										class="flex items-center justify-between gap-3 py-1.5 text-xs"
									>
										<span class="min-w-0 truncate text-gray-700">
											{{ row.item_name || row.item_code }}
										</span>
										<span class="shrink-0 font-medium tabular-nums text-gray-900">
											{{ formatQty(row.qty) }} {{ row.uom }}
										</span>
									</div>
								</div>
								<p v-else class="py-2 text-center text-xs text-gray-400">
									{{ __("No items") }}
								</p>
								<div v-if="receiptDetails[receipt.name]?.attachments?.length" class="pb-2">
									<PurchaseAttachments
										:attached="receiptDetails[receipt.name].attachments"
										:label="__('Attachments')"
									/>
								</div>
								<!-- cards have no actions column; the link lives in the peek -->
								<button
									type="button"
									class="mb-1 inline-flex items-center gap-1 rounded px-1 py-1 text-xs text-gray-500 hover:bg-gray-100 md:hidden"
									@click="openInErpnext(receipt)"
								>
									<FeatherIcon name="external-link" class="h-3 w-3" />
									{{ __("Open in ERPNext") }}
								</button>
							</div>
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
import PurchaseAttachments from "@/components/purchase/PurchaseAttachments.vue"
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

const STATUS_TONES = {
	Draft: "bg-orange-50 text-orange-700 ring-orange-200",
	"To Bill": "bg-purple-50 text-purple-700 ring-purple-200",
	Completed: "bg-green-50 text-green-700 ring-green-200",
	Cancelled: "bg-red-50 text-red-700 ring-red-200",
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

const dialogTitle = computed(() => __("Purchase Receipt"))

function statusPill(status) {
	return [
		"inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset",
		STATUS_TONES[status] || "bg-gray-50 text-gray-700 ring-gray-200",
	]
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
		const res = await call(`${PR_API}.get_purchase_receipt`, {
			name: receipt.name,
		})
		// full summary — items feed the peek, attachments ride along
		receiptDetails.value[receipt.name] = res || {}
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
