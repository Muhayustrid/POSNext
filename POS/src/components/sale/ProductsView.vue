<template>
	<div class="h-full min-w-0 flex flex-col text-start bg-white">
		<!-- List: one period, one search, the outlet's qty balances -->
		<template v-if="view === 'list'">
			<div
				class="shrink-0 border-b border-gray-200 px-4 py-3 sm:px-6 flex flex-wrap items-center gap-2"
			>
				<div class="relative flex-1 min-w-[12rem]">
					<FeatherIcon
						name="search"
						class="absolute start-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none"
					/>
					<input
						v-model="search"
						type="search"
						:placeholder="__('Search products')"
						:aria-label="__('Search products')"
						class="w-full min-h-11 ps-9 pe-3 text-sm placeholder:text-gray-700 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
						data-test="products-search"
					/>
				</div>
				<select
					v-model="period"
					:aria-label="__('Period')"
					class="min-h-11 py-1.5 ps-3 pe-8 text-sm border border-gray-300 rounded-lg bg-white focus:ring-2 focus:ring-blue-500"
					data-test="products-period"
				>
					<option v-for="o in periodOptions" :key="o.value" :value="o.value">
						{{ __(o.label) }}
					</option>
				</select>
				<Button
					variant="subtle"
					class="min-h-11"
					:loading="exporting"
					data-test="products-export"
					@click="exportList"
				>
					<template #prefix><FeatherIcon name="download" class="w-4 h-4" /></template>
					{{ __("Export to Excel") }}
				</Button>
			</div>

			<div
				ref="listScroller"
				data-test="products-list"
				class="flex-1 min-h-0 min-w-0 overflow-auto"
			>
				<div v-if="loading && !rows.length" class="py-16 text-center text-sm text-gray-700">
					{{ __("Loading products...") }}
				</div>
				<div
					v-else-if="listError"
					data-test="products-error"
					class="mx-auto max-w-md py-16 text-center text-sm text-gray-700"
				>
					{{ listError }}
					<button
						type="button"
						class="mx-auto mt-3 flex min-h-11 items-center rounded-lg border border-gray-300 px-4 text-sm font-medium text-gray-900 hover:bg-gray-50 touch-manipulation"
						@click="load()"
					>
						{{ __("Retry") }}
					</button>
				</div>
				<div
					v-else-if="!rows.length"
					data-test="products-empty"
					class="py-16 text-center text-sm text-gray-700"
				>
					{{
						search.trim()
							? __("No products match these filters.")
							: __("No stock items in this outlet.")
					}}
				</div>
				<template v-else>
					<!-- One grid at every width; the panel scrolls sideways on phones -->
					<div
						class="sticky top-0 z-10 grid min-w-[38rem] grid-cols-[minmax(8rem,1fr)_3.5rem_5.5rem_5rem_5rem_6rem] items-center gap-2 border-b border-gray-200 bg-gray-50 px-4 py-2 text-xs font-medium text-gray-700 sm:px-6"
					>
						<div>{{ __("Product") }}</div>
						<div>{{ __("UOM") }}</div>
						<div class="text-end">{{ __("Opening Stock") }}</div>
						<div class="text-end">{{ __("Stock In") }}</div>
						<div class="text-end">{{ __("Stock Out") }}</div>
						<div class="text-end">{{ __("Closing Balance") }}</div>
					</div>
					<button
						v-for="r in rows"
						:key="r.item_code"
						type="button"
						data-test="products-row"
						class="grid w-full min-h-11 min-w-[38rem] grid-cols-[minmax(8rem,1fr)_3.5rem_5.5rem_5rem_5rem_6rem] items-center gap-2 border-b border-gray-100 px-4 py-2 text-start text-sm hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500 touch-manipulation sm:px-6"
						@click="openLedger(r)"
					>
						<span class="min-w-0">
							<span class="block truncate font-medium text-gray-900">{{ r.item_name }}</span>
							<span class="block truncate text-xs text-gray-700">{{ r.item_code }}</span>
						</span>
						<span class="text-gray-700">{{ r.stock_uom }}</span>
						<span class="text-end tabular-nums text-gray-700">{{ qty(r.opening_qty) }}</span>
						<span class="text-end tabular-nums text-gray-700">{{ qty(r.in_qty) }}</span>
						<span class="text-end tabular-nums text-gray-700">{{ qty(r.out_qty) }}</span>
						<span
							class="text-end tabular-nums font-semibold"
							:class="stockClass(r.closing_qty)"
						>
							{{ qty(r.closing_qty) }}
						</span>
					</button>
					<div v-if="hasMore" class="py-4 flex justify-center">
						<Button
							variant="subtle"
							size="sm"
							class="min-h-11"
							:loading="loading"
							data-test="products-more"
							@click="load(false)"
						>
							{{ __("Load More") }}
						</Button>
					</div>
				</template>
			</div>
		</template>

		<!-- Ledger: one product's movements in the same panel, no second modal -->
		<template v-else>
			<div class="shrink-0 flex items-center gap-3 border-b border-gray-200 px-4 py-3 sm:px-6">
				<button
					type="button"
					data-test="ledger-back"
					class="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-gray-200 text-gray-700 hover:bg-gray-50 touch-manipulation"
					:aria-label="__('Back')"
					@click="closeLedger"
				>
					<FeatherIcon name="arrow-left" class="h-5 w-5" />
				</button>
				<div class="min-w-0 flex-1">
					<div class="truncate font-semibold text-gray-900">{{ activeItem?.item_name }}</div>
					<div class="truncate text-xs text-gray-700">
						{{ activeItem?.item_code }} · {{ activeItem?.stock_uom }}
					</div>
				</div>
			</div>

			<div class="shrink-0 flex flex-wrap gap-x-6 gap-y-1 border-b border-gray-200 px-4 py-3 text-xs text-gray-700 sm:px-6">
				<span>{{ range().from }} — {{ range().to }}</span>
				<span>{{ __("Opening Stock") }}: <strong class="tabular-nums">{{ qty(ledger?.opening_qty ?? activeItem?.opening_qty) }}</strong></span>
				<span>{{ __("Closing Balance") }}: <strong class="tabular-nums">{{ qty(ledger?.closing_qty ?? activeItem?.closing_qty) }}</strong></span>
			</div>
			<div data-test="ledger" class="flex-1 min-h-0 min-w-0 overflow-auto">
				<div
					v-if="ledgerLoading && !ledger"
					class="py-16 text-center text-sm text-gray-700"
				>
					{{ __("Loading stock ledger...") }}
				</div>
				<div
					v-else-if="ledgerError"
					data-test="ledger-error"
					class="mx-auto max-w-md py-16 text-center text-sm text-gray-700"
				>
					{{ ledgerError }}
					<button
						type="button"
						class="mx-auto mt-3 flex min-h-11 items-center rounded-lg border border-gray-300 px-4 text-sm font-medium text-gray-900 hover:bg-gray-50 touch-manipulation"
						@click="loadLedger()"
					>
						{{ __("Retry") }}
					</button>
				</div>
				<div
					v-else-if="!ledger?.entries?.length"
					data-test="ledger-empty"
					class="py-16 text-center text-sm text-gray-700"
				>
					{{ __("No stock movements in this period.") }}
				</div>
				<template v-else>
					<div
						class="sticky top-0 z-10 grid min-w-[32rem] grid-cols-[5.5rem_minmax(9rem,1fr)_3.5rem_3.5rem_4.5rem] items-center gap-2 border-b border-gray-200 bg-gray-50 px-4 py-2 text-xs font-medium text-gray-700 sm:px-6"
					>
						<div>{{ __("Date") }}</div>
						<div>{{ __("Transaction") }}</div>
						<div class="text-end">{{ __("In") }}</div>
						<div class="text-end">{{ __("Out") }}</div>
						<div class="text-end">{{ __("Balance") }}</div>
					</div>
					<div
						v-for="e in ledger.entries"
						:key="e.name"
						data-test="ledger-row"
						class="grid min-w-[32rem] grid-cols-[5.5rem_minmax(9rem,1fr)_3.5rem_3.5rem_4.5rem] items-center gap-2 border-b border-gray-100 px-4 py-2.5 text-sm sm:px-6"
					>
						<div class="tabular-nums text-gray-700">
							<div>{{ dayPart(e.posting_datetime || e.posting_date) }}</div>
							<div class="text-xs text-gray-700">
								{{ timePart(e.posting_datetime) }}
							</div>
						</div>
						<div class="min-w-0">
							<div class="truncate font-medium text-gray-900">{{ e.voucher_no }}</div>
							<div class="truncate text-xs text-gray-700">
								{{ __(e.voucher_type) }} · {{ e.warehouse }}
							</div>
						</div>
						<div class="text-end tabular-nums text-gray-700">
							{{ e.in_qty ? qty(e.in_qty) : "—" }}
						</div>
						<div class="text-end tabular-nums text-gray-700">
							{{ e.out_qty ? qty(e.out_qty) : "—" }}
						</div>
						<div class="text-end tabular-nums font-semibold text-gray-900">
							{{ qty(e.balance) }}
						</div>
					</div>
					<div v-if="ledger.has_more" class="py-4 flex justify-center">
						<Button
							variant="subtle"
							size="sm"
							class="min-h-11"
							:loading="ledgerLoading"
							data-test="ledger-more"
							@click="loadLedger(false)"
						>
							{{ __("Load More") }}
						</Button>
					</div>
				</template>
			</div>
		</template>
	</div>
</template>

<script setup>
import { Button, FeatherIcon } from "frappe-ui"
import { call } from "@/utils/apiWrapper"
import { nextTick, onUnmounted, ref, watch } from "vue"
import { useToast } from "@/composables/useToast"
import { downloadXlsx } from "@/utils/downloadXlsx"
import { periodRange } from "@/utils/salesRecap"

const props = defineProps({
	posProfile: { type: String, default: null },
	// company and currency are still passed by POSMenuDialog
	company: { type: String, default: null },
	currency: { type: String, default: "" },
})

const PAGE = 50
const { showError } = useToast()

const view = ref("list") // "list" | "ledger"
const search = ref("")
const period = ref("today")
const rows = ref([])
const loading = ref(false)
const listError = ref("")
const hasMore = ref(false)
const exporting = ref(false)
const listScroller = ref(null)
let listScrollTop = 0

const activeItem = ref(null)
const ledger = ref(null)
const ledgerLoading = ref(false)
const ledgerError = ref("")

const periodOptions = [
	{ value: "today", label: "Today" },
	{ value: "last7", label: "Last 7 Days" },
	{ value: "month", label: "This Month" },
	{ value: "year", label: "This Year" },
]

// The list and the export must always describe the same window.
function range() {
	return periodRange(period.value)
}

function filters() {
	const r = range()
	return {
		pos_profile: props.posProfile,
		search: search.value.trim(),
		from_date: r.from,
		to_date: r.to,
	}
}

function errMsg(err, fallback) {
	const text =
		err?.messages?.join("\n") || (typeof err === "string" ? err : err?.message) || ""
	return text || __(fallback)
}

// A slow answer for an older search or period must never overwrite the state
// of the newer one, so each request carries its own sequence number.
let listSeq = 0
async function load(reset = true) {
	if (!props.posProfile) return
	const mine = ++listSeq
	if (reset) {
		rows.value = []
		hasMore.value = false
	}
	loading.value = true
	listError.value = ""
	try {
		const data = await call("pos_next.api.items.get_product_list", {
			...filters(),
			start: reset ? 0 : rows.value.length,
			limit: PAGE,
		})
		if (mine !== listSeq) return
		rows.value = reset ? data || [] : rows.value.concat(data || [])
		hasMore.value = (data || []).length === PAGE
	} catch (err) {
		if (mine === listSeq) listError.value = errMsg(err, "Failed to load products")
	} finally {
		if (mine === listSeq) loading.value = false
	}
}

let ledgerSeq = 0
async function loadLedger(reset = true) {
	if (!props.posProfile || !activeItem.value) return
	const mine = ++ledgerSeq
	const r = range()
	ledgerLoading.value = true
	ledgerError.value = ""
	try {
		const data = await call("pos_next.api.items.get_item_stock_ledger", {
			item_code: activeItem.value.item_code,
			pos_profile: props.posProfile,
			from_date: r.from,
			to_date: r.to,
			start: reset ? 0 : ledger.value?.entries?.length || 0,
			limit: PAGE,
		})
		if (mine !== ledgerSeq) return
		ledger.value = reset
			? data
			: { ...data, entries: [...(ledger.value?.entries || []), ...(data?.entries || [])] }
	} catch (err) {
		if (mine === ledgerSeq) ledgerError.value = errMsg(err, "Failed to load stock ledger")
	} finally {
		if (mine === ledgerSeq) ledgerLoading.value = false
	}
}

function openLedger(r) {
	listScrollTop = listScroller.value?.scrollTop || 0
	activeItem.value = r
	ledger.value = null
	ledgerError.value = ""
	view.value = "ledger"
	loadLedger()
}

async function closeLedger() {
	ledgerSeq++ // a response landing after Back belongs to a closed view
	view.value = "list"
	activeItem.value = null
	ledger.value = null
	ledgerError.value = ""
	ledgerLoading.value = false
	await nextTick()
	if (listScroller.value) listScroller.value.scrollTop = listScrollTop
}

let searchTimer = null
watch(search, () => {
	clearTimeout(searchTimer)
	searchTimer = setTimeout(() => load(), 300)
})
watch(period, () => load())
watch(
	() => props.posProfile,
	() => {
		clearTimeout(searchTimer)
		loading.value = false
		listSeq++
		ledgerSeq++
		rows.value = []
		hasMore.value = false
		listError.value = ""
		view.value = "list"
		activeItem.value = null
		ledger.value = null
		ledgerError.value = ""
		ledgerLoading.value = false
		load()
	},
	{ immediate: true },
)
onUnmounted(() => clearTimeout(searchTimer))

async function exportList() {
	if (exporting.value) return
	exporting.value = true
	try {
		await downloadXlsx("pos_next.api.items.export_product_list", filters())
	} catch (err) {
		showError(err?.message || __("Export failed"))
	} finally {
		exporting.value = false
	}
}

const qty = (v) => Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: 3 })
const dayPart = (v) => String(v || "").slice(0, 10)
const timePart = (v) => String(v || "").slice(11, 16)
function stockClass(v) {
	if (v <= 0) return "text-red-600"
	if (v < 10) return "text-amber-700"
	return "text-gray-900"
}
</script>
