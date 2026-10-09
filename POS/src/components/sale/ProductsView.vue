<template>
	<div class="h-full flex flex-col text-start bg-white">
		<!-- Toolbar: search, group, stock filter, sort, export -->
		<div class="shrink-0 border-b border-gray-200 px-4 py-3 sm:px-6 flex flex-col gap-3">
			<div class="flex items-center gap-2">
				<div class="relative flex-1 min-w-0">
					<FeatherIcon
						name="search"
						class="absolute start-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none"
					/>
					<input
						v-model="search"
						type="search"
						:placeholder="__('Search')"
						:aria-label="__('Search products')"
						class="w-full ps-9 pe-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
						data-test="products-search"
					/>
				</div>
				<Button
					variant="subtle"
					:loading="exporting"
					data-test="products-export"
					@click="exportList"
				>
					<template #prefix><FeatherIcon name="download" class="w-4 h-4" /></template>
					{{ __("Export to Excel") }}
				</Button>
			</div>
			<div class="flex flex-wrap items-center gap-2">
				<select
					v-model="itemGroup"
					:aria-label="__('Item Group')"
					class="py-1.5 ps-3 pe-8 text-sm border border-gray-300 rounded-lg bg-white focus:ring-2 focus:ring-blue-500"
					data-test="products-group"
				>
					<option value="">{{ __("All groups") }}</option>
					<option v-for="g in groups" :key="g.item_group" :value="g.item_group">
						{{ g.item_group }}
					</option>
				</select>
				<div class="flex rounded-lg border border-gray-300 overflow-hidden" role="group">
					<button
						v-for="opt in stockOptions"
						:key="opt.value"
						type="button"
						class="px-3 py-1.5 text-sm transition-colors border-e border-gray-300 last:border-e-0"
						:class="
							stock === opt.value
								? 'bg-gray-900 text-white'
								: 'bg-white text-gray-700 hover:bg-gray-50'
						"
						:aria-pressed="stock === opt.value"
						@click="stock = opt.value"
					>
						{{ opt.label }}
					</button>
				</div>
				<select
					v-model="sort"
					:aria-label="__('Sort by')"
					class="ms-auto py-1.5 ps-3 pe-8 text-sm border border-gray-300 rounded-lg bg-white focus:ring-2 focus:ring-blue-500"
					data-test="products-sort"
				>
					<option value="name">{{ __("Name A–Z") }}</option>
					<option value="stock_asc">{{ __("Lowest stock first") }}</option>
					<option value="stock_desc">{{ __("Highest stock first") }}</option>
				</select>
			</div>
		</div>

		<!-- List -->
		<div class="flex-1 min-h-0 overflow-y-auto">
			<div v-if="loading && !rows.length" class="py-16 text-center text-sm text-gray-500">
				{{ __("Loading products...") }}
			</div>
			<div
				v-else-if="!rows.length"
				class="py-16 text-center text-sm text-gray-500"
				data-test="products-empty"
			>
				{{ hasFilters ? __("No products match these filters.") : __("No stock items in this outlet.") }}
				<button
					v-if="hasFilters"
					type="button"
					class="block mx-auto mt-2 text-blue-600 hover:underline"
					@click="resetFilters"
				>
					{{ __("Clear filters") }}
				</button>
			</div>
			<table v-else class="w-full text-sm">
				<thead class="sticky top-0 bg-gray-50 text-gray-500 text-xs z-10">
					<tr>
						<th class="py-2 ps-4 sm:ps-6 pe-2 text-start font-medium">{{ __("Product") }}</th>
						<th class="py-2 px-2 text-start font-medium hidden md:table-cell">
							{{ __("Group") }}
						</th>
						<th class="py-2 px-2 text-end font-medium">{{ __("Price") }}</th>
						<th class="py-2 ps-2 pe-4 sm:pe-6 text-end font-medium">{{ __("Stock") }}</th>
					</tr>
				</thead>
				<tbody>
					<tr
						v-for="r in rows"
						:key="r.item_code"
						tabindex="0"
						class="border-t border-gray-100 hover:bg-blue-50/50 cursor-pointer focus:outline-none focus-visible:bg-blue-50"
						data-test="products-row"
						@click="openWarehouses(r)"
						@keydown.enter="openWarehouses(r)"
					>
						<td class="py-2.5 ps-4 sm:ps-6 pe-2">
							<div class="flex items-center gap-3 min-w-0">
								<div
									class="w-10 h-10 shrink-0 rounded-md bg-gray-100 overflow-hidden flex items-center justify-center"
								>
									<img
										v-if="r.image"
										:src="r.image"
										:alt="r.item_name"
										loading="lazy"
										class="w-full h-full object-cover"
									/>
									<span v-else class="text-sm font-semibold text-gray-400">
										{{ (r.item_name || "?").charAt(0).toUpperCase() }}
									</span>
								</div>
								<div class="min-w-0">
									<div class="font-medium text-gray-900 truncate">{{ r.item_name }}</div>
									<div class="text-xs text-gray-500 truncate">
										{{ r.item_code }}<span class="md:hidden"> — {{ r.item_group }}</span>
									</div>
								</div>
							</div>
						</td>
						<td class="py-2.5 px-2 text-gray-600 hidden md:table-cell">{{ r.item_group }}</td>
						<td class="py-2.5 px-2 text-end tabular-nums whitespace-nowrap">
							<span v-if="r.rate" class="font-semibold text-gray-900">{{ money(r.rate) }}</span>
							<span v-else class="text-gray-400">{{ __("No price") }}</span>
						</td>
						<td class="py-2.5 ps-2 pe-4 sm:pe-6 text-end tabular-nums whitespace-nowrap">
							<span class="font-semibold" :class="stockClass(r.actual_qty)">
								{{ qty(r.actual_qty) }}
							</span>
							<span class="text-xs text-gray-500"> {{ r.stock_uom }}</span>
						</td>
					</tr>
				</tbody>
			</table>
			<div v-if="hasMore" class="py-4 flex justify-center">
				<Button variant="subtle" size="sm" :loading="loading" @click="load(false)">
					{{ __("Load More") }}
				</Button>
			</div>
		</div>
	</div>

	<WarehouseAvailabilityDialog
		v-if="selected"
		v-model="showWarehouses"
		:item-code="selected.item_code"
		:item-name="selected.item_name"
		:uom="selected.stock_uom"
		:company="company"
	/>
</template>

<script setup>
import { Button, call, FeatherIcon } from "frappe-ui"
import { computed, onUnmounted, ref, watch } from "vue"
import WarehouseAvailabilityDialog from "@/components/sale/WarehouseAvailabilityDialog.vue"
import { useToast } from "@/composables/useToast"
import { formatCurrency } from "@/utils/currency"
import { downloadXlsx } from "@/utils/downloadXlsx"

const props = defineProps({
	posProfile: { type: String, default: null },
	company: { type: String, default: null },
	currency: { type: String, default: "" },
})

const PAGE = 50
const { showError } = useToast()

const search = ref("")
const itemGroup = ref("")
const stock = ref("")
const sort = ref("name")
const rows = ref([])
const groups = ref([])
const loading = ref(false)
const hasMore = ref(false)
const exporting = ref(false)
const selected = ref(null)
const showWarehouses = ref(false)

const stockOptions = [
	{ value: "", label: __("All") },
	{ value: "in", label: __("In stock") },
	{ value: "out", label: __("Out of stock") },
]

const hasFilters = computed(() => Boolean(search.value || itemGroup.value || stock.value))

function filters() {
	return {
		pos_profile: props.posProfile,
		search: search.value.trim(),
		item_group: itemGroup.value,
		stock: stock.value,
		sort: sort.value,
	}
}

// Every request carries a sequence number: a slow page 1 for an old search
// must not overwrite the rows of the newer one.
let seq = 0
async function load(reset = true) {
	if (!props.posProfile) return
	const mine = ++seq
	loading.value = true
	try {
		const data = await call("pos_next.api.items.get_product_list", {
			...filters(),
			start: reset ? 0 : rows.value.length,
			limit: PAGE,
		})
		if (mine !== seq) return
		rows.value = reset ? data || [] : rows.value.concat(data || [])
		hasMore.value = (data || []).length === PAGE
	} catch (err) {
		if (mine === seq) showError(err?.messages?.join("\n") || __("Failed to load products"))
	} finally {
		if (mine === seq) loading.value = false
	}
}

async function loadGroups() {
	if (!props.posProfile) return
	try {
		groups.value = (await call("pos_next.api.items.get_item_groups", { pos_profile: props.posProfile })) || []
	} catch {
		groups.value = [] // the filter is optional; the list still works
	}
}

let searchTimer = null
watch(search, () => {
	clearTimeout(searchTimer)
	searchTimer = setTimeout(() => load(), 300)
})
watch([itemGroup, stock, sort], () => load())
watch(
	() => props.posProfile,
	() => {
		loadGroups()
		load()
	},
	{ immediate: true },
)
onUnmounted(() => clearTimeout(searchTimer))

function resetFilters() {
	search.value = ""
	itemGroup.value = ""
	stock.value = ""
}

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

function openWarehouses(r) {
	selected.value = r
	showWarehouses.value = true
}

const money = (v) => formatCurrency(Number(v) || 0, props.currency || undefined)
const qty = (v) => Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: 3 })
function stockClass(v) {
	if (v <= 0) return "text-red-600"
	if (v < 10) return "text-amber-600"
	return "text-gray-900"
}
</script>
