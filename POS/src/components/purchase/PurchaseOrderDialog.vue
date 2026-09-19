<template>
	<Dialog v-model="show" :options="{ title: dialogTitle, size: 'lg' }">
		<template #body-content>
			<!-- LIST VIEW -->
			<div v-if="view === 'list'" class="flex flex-col gap-3">
				<div class="flex gap-2">
					<input
						v-model="searchTerm"
						type="text"
						data-test="list-search"
						:placeholder="__('Search PO or supplier...')"
						class="flex-1 px-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
						@input="onListSearch"
					/>
					<Button variant="solid" data-test="new-button" @click="openNew">
						{{ __("New") }}
					</Button>
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

				<div v-if="loadingOrders" class="py-8 text-center text-sm text-gray-500">
					{{ __("Loading...") }}
				</div>
				<div v-else-if="orders.length === 0" class="py-8 text-center">
					<p class="text-sm font-medium text-gray-900">{{ __("No purchase orders") }}</p>
					<p class="text-xs text-gray-500 mt-1">
						{{ __("Create one with the New button") }}
					</p>
				</div>
				<div v-else class="flex flex-col gap-2 max-h-96 overflow-y-auto">
					<div
						v-for="order in orders"
						:key="order.name"
						class="bg-white border border-gray-200 rounded-lg p-3"
					>
						<div class="flex items-start justify-between gap-2">
							<div class="min-w-0">
								<div class="flex items-center gap-2">
									<span class="text-sm font-semibold text-gray-900">{{ order.name }}</span>
									<StatusBadge :variant="statusVariant(order.status)" size="xs" :text="order.status" />
								</div>
								<p class="text-xs text-gray-500 mt-0.5 truncate">{{ order.supplier_name }}</p>
								<p class="text-xs text-gray-400 mt-0.5">
									{{ formatDate(order.transaction_date) }}
								</p>
							</div>
							<span class="text-sm font-bold text-gray-900 whitespace-nowrap">
								{{ formatMoney(order.grand_total, order.currency) }}
							</span>
						</div>

						<div class="flex flex-wrap gap-1 mt-2 pt-2 border-t border-gray-100">
							<button
								v-if="order.docstatus === 0"
								type="button"
								class="px-2 py-1 text-xs rounded text-blue-600 hover:bg-blue-50"
								@click="openEdit(order)"
							>
								{{ __("Edit") }}
							</button>
							<button
								v-if="order.docstatus === 0 && canSubmitPO"
								type="button"
								class="px-2 py-1 text-xs rounded text-green-600 hover:bg-green-50"
								@click="submitOrder(order)"
							>
								{{ __("Submit") }}
							</button>
							<button
								v-if="order.docstatus === 1 && canCancelPO"
								type="button"
								class="px-2 py-1 text-xs rounded text-red-600 hover:bg-red-50"
								@click="cancelOrder(order)"
							>
								{{ __("Cancel") }}
							</button>
							<button
								type="button"
								class="px-2 py-1 text-xs rounded text-gray-500 hover:bg-gray-100"
								@click="openInErpnext(order)"
							>
								{{ __("Open in ERPNext") }}
							</button>
						</div>
					</div>
				</div>
			</div>

			<!-- FORM VIEW -->
			<div v-else class="flex flex-col gap-3">
				<div>
					<label class="block text-xs font-medium text-gray-600 mb-1">{{ __("Supplier") }}</label>
					<AutocompleteSelect
						:model-value="form.supplier"
						:options="supplierOptions"
						:placeholder="__('Search supplier...')"
						:loading="loadingSuppliers"
						data-test="supplier-select"
						@update:model-value="onSupplierSelect"
						@search="onSupplierSearch"
					/>
				</div>

				<div class="grid grid-cols-2 gap-3">
					<div>
						<label for="po-transaction-date" class="block text-xs font-medium text-gray-600 mb-1">
							{{ __("Transaction Date") }}
						</label>
						<input
							id="po-transaction-date"
							v-model="form.transaction_date"
							type="date"
							class="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg"
						/>
					</div>
					<div>
						<label for="po-schedule-date" class="block text-xs font-medium text-gray-600 mb-1">
							{{ __("Required By") }}
						</label>
						<input
							id="po-schedule-date"
							v-model="form.schedule_date"
							type="date"
							class="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg"
						/>
					</div>
				</div>

				<p class="text-xs text-gray-500">
					{{ __("Company: {0} | Warehouse: {1}", [props.company || "-", props.warehouse || "-"]) }}
				</p>

				<div
					v-if="form.taxes_and_charges"
					class="flex items-center justify-between px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg"
				>
					<span class="text-xs text-gray-600">
						{{ __("Tax Template: {0}", [form.taxes_and_charges]) }}
					</span>
					<button
						type="button"
						class="text-xs text-red-600 hover:text-red-700"
						@click="form.taxes_and_charges = ''"
					>
						{{ __("Remove") }}
					</button>
				</div>

				<div>
					<label class="block text-xs font-medium text-gray-600 mb-1">{{ __("Items") }}</label>
					<AutocompleteSelect
						:model-value="itemPick"
						:options="itemOptions"
						:placeholder="__('Search item...')"
						:loading="loadingItems"
						data-test="item-select"
						@update:model-value="onItemPick"
						@search="onItemSearch"
					/>
				</div>

				<div v-if="form.items.length" class="overflow-x-auto">
					<table class="w-full text-sm" data-test="items-table">
						<thead>
							<tr class="text-xs text-gray-500 uppercase">
								<th class="py-1 text-start">{{ __("Item") }}</th>
								<th class="py-1 text-start w-20">{{ __("Qty") }}</th>
								<th class="py-1 text-start w-28">{{ __("Rate") }}</th>
								<th class="py-1 text-end">{{ __("Amount") }}</th>
								<th class="py-1 w-8"></th>
							</tr>
						</thead>
						<tbody>
							<tr v-for="(row, idx) in form.items" :key="idx" class="border-t border-gray-100">
								<td class="py-1.5 pe-2">
									<div class="truncate max-w-[180px]">{{ row.item_name }}</div>
									<div class="text-xs text-gray-400">{{ row.uom }}</div>
								</td>
								<td class="py-1.5 pe-2">
									<input
										v-model.number="row.qty"
										data-test="item-qty"
										type="number"
										min="0"
										step="any"
										:aria-label="`${row.item_name} — ${__('Qty')}`"
										class="w-full px-2 py-1 text-sm border border-gray-300 rounded"
									/>
								</td>
								<td class="py-1.5 pe-2">
									<input
										v-model.number="row.rate"
										data-test="item-rate"
										type="number"
										min="0"
										step="any"
										:aria-label="`${row.item_name} — ${__('Rate')}`"
										class="w-full px-2 py-1 text-sm border border-gray-300 rounded"
									/>
								</td>
								<td class="py-1.5 text-end whitespace-nowrap">
									{{ formatMoney(rowAmount(row), form.currency) }}
								</td>
								<td class="py-1.5">
									<button
										type="button"
										class="text-gray-400 hover:text-red-600"
										@click="form.items.splice(idx, 1)"
									>
										×
									</button>
								</td>
							</tr>
						</tbody>
					</table>
					<div class="flex justify-end gap-2 mt-2 text-sm">
						<span class="text-gray-500">{{ __("Total") }}</span>
						<span class="font-bold text-gray-900">
							{{ formatMoney(totalAmount, form.currency) }}
						</span>
					</div>
				</div>

				<div>
					<label for="po-remarks" class="block text-xs font-medium text-gray-600 mb-1">{{
						__("Remarks")
					}}</label>
					<textarea
						id="po-remarks"
						v-model="form.remarks"
						rows="2"
						class="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg"
					></textarea>
				</div>
			</div>
		</template>

		<template #actions>
			<div v-if="view === 'form'" class="flex justify-between items-center w-full">
				<Button variant="subtle" @click="view = 'list'">{{ __("Back") }}</Button>
				<div class="flex gap-2">
					<Button variant="subtle" :loading="saving" @click="save(false)">
						{{ __("Save Draft") }}
					</Button>
					<Button v-if="canSubmitPO" variant="solid" :loading="saving" @click="save(true)">
						{{ __("Save & Submit") }}
					</Button>
				</div>
			</div>
			<div v-else class="flex justify-end w-full">
				<Button variant="subtle" @click="show = false">{{ __("Close") }}</Button>
			</div>
		</template>
	</Dialog>
</template>

<script setup>
import AutocompleteSelect from "@/components/common/AutocompleteSelect.vue"
import StatusBadge from "@/components/common/StatusBadge.vue"
import { useToast } from "@/composables/useToast"
import { useFormatters } from "@/composables/useFormatters"
import { usePermissions } from "@/composables/usePermissions"
import { call, serverErrorMessage } from "@/utils/apiWrapper"
import { formatCurrency } from "@/utils/currency"
import { parseError } from "@/utils/errorHandler"
import { Button, Dialog } from "frappe-ui"
import { computed, ref, watch } from "vue"

const props = defineProps({
	modelValue: Boolean,
	posProfile: { type: String, default: null },
	company: { type: String, default: null },
	warehouse: { type: String, default: null },
	currency: { type: String, default: "" },
})

const emit = defineEmits(["update:modelValue"])

const { showSuccess, showError } = useToast()
const { formatDate } = useFormatters()
const { usePermissionCheck } = usePermissions()
const { hasPermission: canSubmitPO } = usePermissionCheck(
	"Purchase Order",
	"submit",
)
const { hasPermission: canCancelPO } = usePermissionCheck(
	"Purchase Order",
	"cancel",
)

const API = "pos_next.api.purchase_orders"

const view = ref("list")
const show = ref(props.modelValue)
watch(show, (val) => emit("update:modelValue", val))

// ---------- list view ----------
const STATUS_CHIPS = [
	{ value: "", label: "All" },
	{ value: "Draft", label: "Draft" },
	{ value: "To Receive and Bill", label: "To Receive and Bill" },
	{ value: "To Receive", label: "To Receive" },
	{ value: "To Bill", label: "To Bill" },
	{ value: "Completed", label: "Completed" },
	{ value: "Cancelled", label: "Cancelled" },
]

const STATUS_VARIANTS = {
	Draft: "orange",
	"To Receive and Bill": "blue",
	"To Receive": "blue",
	"To Bill": "blue",
	Completed: "green",
	Cancelled: "red",
}

const orders = ref([])
const loadingOrders = ref(false)
const searchTerm = ref("")
const statusFilter = ref("")
let listTimer = null

// immediate: also covers mounting with the dialog already open
watch(
	() => props.modelValue,
	(val) => {
		show.value = val
		if (val) {
			view.value = "list"
			loadOrders()
		} else {
			clearTimeout(listTimer)
		}
	},
	{ immediate: true },
)

const dialogTitle = computed(() =>
	view.value === "form"
		? form.value.name
			? __("Edit Purchase Order")
			: __("New Purchase Order")
		: __("Purchase Orders"),
)

function statusVariant(status) {
	return STATUS_VARIANTS[status] || "gray"
}

async function loadOrders() {
	loadingOrders.value = true
	try {
		const res = await call(`${API}.get_purchase_orders`, {
			pos_profile: props.posProfile,
			status: statusFilter.value || null,
			search_term: searchTerm.value || null,
			limit: 50,
		})
		orders.value = res?.orders || []
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	} finally {
		loadingOrders.value = false
	}
}

function onListSearch() {
	clearTimeout(listTimer)
	listTimer = setTimeout(loadOrders, 300)
}

function setStatus(value) {
	if (statusFilter.value === value) return
	statusFilter.value = value
	loadOrders()
}

async function submitOrder(order) {
	try {
		const res = await call(`${API}.submit_purchase_order`, { name: order.name })
		showSuccess(__("Purchase Order {0} submitted", [res?.name || order.name]))
		await loadOrders()
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	}
}

async function cancelOrder(order) {
	try {
		const res = await call(`${API}.cancel_purchase_order`, { name: order.name })
		showSuccess(__("Purchase Order {0} cancelled", [res?.name || order.name]))
		await loadOrders()
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	}
}

function openInErpnext(order) {
	window.open(`/app/purchase-order/${order.name}`, "_blank")
}

// ---------- form view ----------
function localDate(offsetDays = 0) {
	const d = new Date(Date.now() + offsetDays * 86400000)
	const m = String(d.getMonth() + 1).padStart(2, "0")
	const day = String(d.getDate()).padStart(2, "0")
	return `${d.getFullYear()}-${m}-${day}`
}

function blankForm() {
	return {
		name: null,
		supplier: "",
		transaction_date: localDate(),
		schedule_date: localDate(1),
		currency: props.currency || "",
		taxes_and_charges: "",
		remarks: "",
		items: [],
	}
}

const form = ref(blankForm())
const itemPick = ref("")
const supplierOptions = ref([])
const itemOptions = ref([])
const loadingSuppliers = ref(false)
const loadingItems = ref(false)
const saving = ref(false)
let supplierTimer = null
let itemTimer = null

function openNew() {
	form.value = blankForm()
	view.value = "form"
}

async function openEdit(order) {
	try {
		const d = await call(`${API}.get_purchase_order`, { name: order.name })
		form.value = {
			name: d?.name || null,
			supplier: d?.supplier || "",
			transaction_date: d?.transaction_date || localDate(),
			schedule_date: d?.schedule_date || localDate(1),
			currency: d?.currency || props.currency || "",
			// prefill the loaded template — an empty value here is the explicit
			// remove-tax signal, not "leave untouched"
			taxes_and_charges: d?.taxes_and_charges || "",
			remarks: d?.remarks || "",
			items: (d?.items || []).map((i) => ({
				item_code: i.item_code,
				item_name: i.item_name,
				qty: i.qty,
				uom: i.uom,
				rate: i.rate,
			})),
		}
		view.value = "form"
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	}
}

function onSupplierSearch(term) {
	clearTimeout(supplierTimer)
	supplierTimer = setTimeout(async () => {
		loadingSuppliers.value = true
		try {
			const res = await call(`${API}.search_suppliers`, {
				search_term: term || null,
				limit: 20,
			})
			supplierOptions.value = (res?.suppliers || []).map((s) => ({
				value: s.name,
				label: s.supplier_name || s.name,
				subtitle: s.supplier_group,
			}))
		} catch (error) {
			showError(parseError(error)?.message || serverErrorMessage(error))
		} finally {
			loadingSuppliers.value = false
		}
	}, 300)
}

async function onSupplierSelect(name) {
	form.value.supplier = name || ""
	if (!name) {
		// clear selection must not leave the previous supplier's defaults behind
		form.value.currency = props.currency || ""
		form.value.taxes_and_charges = ""
		return
	}
	try {
		const d = await call(`${API}.get_supplier_details`, {
			supplier: name,
			pos_profile: props.posProfile,
		})
		form.value.currency = d?.currency || props.currency || ""
		form.value.taxes_and_charges = d?.taxes_and_charges || ""
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	}
}

function onItemSearch(term) {
	clearTimeout(itemTimer)
	itemTimer = setTimeout(async () => {
		loadingItems.value = true
		try {
			const res = await call(`${API}.search_purchase_items`, {
				search_term: term || null,
				limit: 20,
			})
			itemOptions.value = (res?.items || []).map((i) => ({
				value: i.item_code,
				label: i.item_name,
				subtitle: i.stock_uom,
			}))
		} catch (error) {
			showError(parseError(error)?.message || serverErrorMessage(error))
		} finally {
			loadingItems.value = false
		}
	}, 300)
}

async function onItemPick(code) {
	itemPick.value = ""
	if (!code) return
	try {
		const d = await call(`${API}.get_purchase_item_details`, {
			item_code: code,
			supplier: form.value.supplier || null,
			pos_profile: props.posProfile,
			qty: 1,
			transaction_date: form.value.transaction_date || null,
			warehouse: props.warehouse || null,
		})
		form.value.items.push({
			item_code: d?.item_code || code,
			item_name: d?.item_name || code,
			qty: 1,
			uom: d?.uom || d?.stock_uom || "",
			rate: d?.price_list_rate || d?.rate || 0,
		})
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	}
}

function rowAmount(row) {
	return (Number(row.qty) || 0) * (Number(row.rate) || 0)
}

const totalAmount = computed(() =>
	form.value.items.reduce((sum, row) => sum + rowAmount(row), 0),
)

function formatMoney(amount, currency) {
	return formatCurrency(
		Number(amount) || 0,
		currency || props.currency || "USD",
	)
}

async function save(submitAfter) {
	if (!form.value.supplier) {
		showError(__("Supplier is required"))
		return
	}
	if (!form.value.items.length) {
		showError(__("At least one item is required"))
		return
	}
	saving.value = true
	try {
		const payload = {
			supplier: form.value.supplier,
			transaction_date: form.value.transaction_date,
			schedule_date: form.value.schedule_date,
			company: props.company,
			currency: form.value.currency,
			set_warehouse: props.warehouse,
			taxes_and_charges: form.value.taxes_and_charges,
			remarks: form.value.remarks || "",
			items: form.value.items.map((row) => ({
				item_code: row.item_code,
				qty: Number(row.qty) || 0,
				rate: Number(row.rate) || 0,
				uom: row.uom,
			})),
		}
		if (form.value.name) payload.name = form.value.name
		const res = await call(`${API}.save_purchase_order`, {
			data: JSON.stringify(payload),
			pos_profile: props.posProfile,
			submit: submitAfter ? 1 : 0,
		})
		showSuccess(
			submitAfter
				? __("Purchase Order {0} submitted", [res?.name])
				: __("Purchase Order {0} saved", [res?.name]),
		)
		view.value = "list"
		await loadOrders()
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	} finally {
		saving.value = false
	}
}
</script>
