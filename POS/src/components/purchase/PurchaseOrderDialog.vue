<template>
	<DialogHost
		v-model:show="show"
		:embedded="embedded"
		:options="{ title: dialogTitle, size: 'lg' }"
	>
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
					<RefreshButton :loading="loadingOrders" @click="loadOrders" />
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

				<div
					v-if="loadingOrders"
					class="py-8 text-center text-sm text-gray-500"
				>
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
										<StatusBadge
											v-if="order.is_internal_supplier && order.inter_company_order_reference"
											variant="gray"
											size="xs"
											:text="__('Factory SO')"
										/>
									</div>
									<p class="text-xs text-gray-500 mt-0.5 truncate">{{ order.supplier_name }}</p>
									<p class="text-xs text-gray-400 mt-0.5">
										{{ formatDate(order.transaction_date) }}
									</p>
								</div>
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
								v-if="
									order.docstatus === 1 &&
									order.per_received < 100 &&
									(!poDefaults?.receive_requires_delivery_note || order.delivery_ready) &&
									canReceivePR
								"
								type="button"
								data-test="receive-button"
								class="px-2 py-1 text-xs rounded text-purple-600 hover:bg-purple-50"
								@click="openReceive(order)"
							>
								{{ __("Receive") }}
							</button>
							<button
								v-if="order.docstatus === 1 && canCancelPO && !isFactoryLinked(order)"
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

			<!-- RECEIVE VIEW -->
			<div v-else-if="view === 'receive'" class="flex flex-col gap-3">
				<div class="flex items-end justify-between gap-3">
					<div class="min-w-0">
						<p class="text-sm font-semibold text-gray-900 truncate">
							{{ receive.supplier_name }}
						</p>
						<p class="text-xs text-gray-500 mt-0.5 truncate">{{ receive.supplier }}</p>
						<p
							v-if="receive.inter_company_reference"
							class="text-xs text-gray-400 mt-0.5 truncate"
						>
							{{ __("From Delivery Note: {0}", [receive.inter_company_reference]) }}
						</p>
					</div>
					<div>
						<label for="pr-posting-date" class="block text-xs font-medium text-gray-600 mb-1">
							{{ __("Posting Date") }}
						</label>
						<input
							id="pr-posting-date"
							v-model="receive.posting_date"
							type="date"
							data-test="receive-posting-date"
							class="px-3 py-2 text-sm border border-gray-300 rounded-lg"
						/>
					</div>
				</div>

				<div v-if="receive.items.length" class="overflow-x-auto">
					<table class="w-full text-sm" data-test="receive-table">
						<thead>
							<tr class="text-xs text-gray-500 uppercase">
								<th class="py-1 text-start">{{ __("Item") }}</th>
								<th class="py-1 text-start w-16">{{ __("Ordered") }}</th>
								<th class="py-1 text-start w-16">{{ __("Received") }}</th>
								<th class="py-1 text-start w-16">{{ __("Remaining") }}</th>
								<th class="py-1 text-start w-20">{{ __("Qty") }}</th>
							</tr>
						</thead>
						<tbody>
							<tr
								v-for="row in receive.items"
								:key="row.purchase_order_item"
								class="border-t border-gray-100"
							>
								<td class="py-1.5 pe-2">
									<div class="truncate max-w-[180px]">{{ row.item_name }}</div>
									<div class="text-xs text-gray-400">{{ row.uom }}</div>
								</td>
								<td class="py-1.5 pe-2">{{ row.ordered_qty }}</td>
								<td class="py-1.5 pe-2">{{ row.received_qty }}</td>
								<td class="py-1.5 pe-2">{{ row.pending_qty }}</td>
								<td class="py-1.5 pe-2">
									<input
										v-model.number="row.qty"
										data-test="receive-qty"
										type="number"
										min="0"
										step="any"
										:aria-label="`${row.item_name} — ${__('Qty')}`"
										class="w-full px-2 py-1 text-sm border border-gray-300 rounded"
									/>
								</td>
							</tr>
						</tbody>
					</table>
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
								<th class="py-1 text-start w-24">{{ __("UOM") }}</th>
								<th class="py-1 w-8"></th>
							</tr>
						</thead>
						<tbody>
							<tr v-for="(row, idx) in form.items" :key="idx" class="border-t border-gray-100">
								<td class="py-1.5 pe-2">
									<div class="truncate max-w-[180px]">{{ row.item_name }}</div>
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
									<select
										v-model="row.uom"
										data-test="item-uom"
										:aria-label="`${row.item_name} — ${__('UOM')}`"
										class="w-full px-2 py-1 text-sm border border-gray-300 rounded bg-white"
										@change="onUomChange(row)"
									>
										<option v-for="u in uomOptionsOf(row)" :key="u" :value="u">
											{{ u }}
										</option>
									</select>
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
			<div v-else-if="view === 'receive'" class="flex justify-between items-center w-full">
				<Button variant="subtle" @click="view = 'list'">{{ __("Back") }}</Button>
				<div class="flex gap-2">
					<Button variant="subtle" :loading="saving" @click="saveReceipt(false)">
						{{ __("Save Draft") }}
					</Button>
					<Button v-if="canSubmitPR" variant="solid" :loading="saving" @click="saveReceipt(true)">
						{{ __("Submit Receipt") }}
					</Button>
				</div>
			</div>
			<div v-else-if="!embedded" class="flex justify-end w-full">
				<Button variant="subtle" @click="show = false">{{ __("Close") }}</Button>
			</div>
		</template>
	</DialogHost>
</template>

<script setup>
import AutocompleteSelect from "@/components/common/AutocompleteSelect.vue"
import StatusBadge from "@/components/common/StatusBadge.vue"
import { useToast } from "@/composables/useToast"
import { useFormatters } from "@/composables/useFormatters"
import { usePermissions } from "@/composables/usePermissions"
import { call, serverErrorMessage } from "@/utils/apiWrapper"
import { parseError } from "@/utils/errorHandler"
import { Button } from "frappe-ui"
import { computed, ref, watch } from "vue"
import DialogHost from "@/components/common/DialogHost.js"
import RefreshButton from "@/components/common/RefreshButton.vue"

const props = defineProps({
	modelValue: Boolean,
	posProfile: { type: String, default: null },
	company: { type: String, default: null },
	warehouse: { type: String, default: null },
	currency: { type: String, default: "" },
	embedded: { type: Boolean, default: false },
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
const { hasPermission: canReceivePR } = usePermissionCheck(
	"Purchase Receipt",
	"create",
)
const { hasPermission: canSubmitPR } = usePermissionCheck(
	"Purchase Receipt",
	"submit",
)

const API = "pos_next.api.purchase_orders"
const PR_API = "pos_next.api.purchase_receipts"

const view = ref("list")

// AutocompleteSelect only fires @search while typing; without a preload the
// dropdowns open empty and look broken. Prime the top options on form entry.
watch(view, (v) => {
	if (v === "form") {
		onSupplierSearch("")
		onItemSearch("")
	}
})
const show = ref(props.modelValue)
watch(show, (val) => emit("update:modelValue", val))

// ---------- list view ----------
const STATUS_CHIPS = [
	{ value: "", label: __("All") },
	{ value: "Draft", label: __("Draft") },
	{ value: "To Receive and Bill", label: __("To Receive and Bill") },
	{ value: "To Receive", label: __("To Receive") },
	{ value: "To Bill", label: __("To Bill") },
	{ value: "Completed", label: __("Completed") },
	{ value: "Cancelled", label: __("Cancelled") },
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

// POS Settings defaults (per profile) — one fetch per dialog session; also
// drives the Receive gate (receive_requires_delivery_note). Declared above
// the open watcher: the watcher fires immediately during setup.
const poDefaults = ref(null)

async function loadPoDefaults() {
	if (poDefaults.value) return poDefaults.value
	try {
		poDefaults.value = await call(`${API}.get_po_defaults`, { pos_profile: props.posProfile })
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
		poDefaults.value = {}
	}
	return poDefaults.value
}

// immediate: also covers mounting with the dialog already open
watch(
	() => props.modelValue,
	(val) => {
		show.value = val
		if (val) {
			view.value = "list"
			loadOrders()
			// the Receive gate reads the POS Settings flag — have it ready by
			// the time the list renders
			loadPoDefaults()
		} else {
			clearTimeout(listTimer)
		}
	},
	{ immediate: true },
)

const dialogTitle = computed(() => {
	if (view.value === "form")
		return form.value.name
			? __("Edit Purchase Order")
			: __("New Purchase Order")
	if (view.value === "receive") return __("Receive Goods")
	return __("Purchase Orders")
})

function statusVariant(status) {
	return STATUS_VARIANTS[status] || "gray"
}

// Same condition as the "Factory SO" badge: the live PO↔SO link resolves on
// the selling-company SO side, and a factory-linked PO must not be cancelled
// from the POS (the factory plans against it).
function isFactoryLinked(order) {
	return Boolean(order.is_internal_supplier && order.inter_company_order_reference)
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

// ---------- receive view ----------
const receive = ref({
	po_name: null,
	supplier: "",
	supplier_name: "",
	posting_date: "",
	company: "",
	currency: "",
	set_warehouse: "",
	// internal POs are received from the factory's Delivery Note — the DN
	// reference rides the payload so the PR keeps the DN <-> PR cross links
	inter_company_reference: null,
	items: [],
})

async function openReceive(order) {
	try {
		// internal supplier POs draft from the factory DN, everyone else
		// straight from the PO
		const method = order.is_internal_supplier
			? `${PR_API}.get_intercompany_receipt_draft`
			: `${PR_API}.get_purchase_receipt_draft`
		const d = await call(method, {
			po_name: order.name,
		})
		receive.value = {
			po_name: order.name,
			supplier: d?.supplier || "",
			supplier_name: d?.supplier_name || order.supplier_name,
			posting_date: d?.posting_date || localDate(),
			company: d?.company || props.company,
			currency: d?.currency || props.currency || "",
			set_warehouse: d?.set_warehouse || "",
			inter_company_reference: d?.inter_company_reference || null,
			// default each row to what is still owed; the mapper only returns
			// rows with a pending qty
			items: (d?.items || []).map((row) => ({ ...row, qty: row.pending_qty })),
		}
		view.value = "receive"
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	}
}

async function saveReceipt(submitAfter) {
	// rows left at 0 are "not receiving this now", not "receive nothing" —
	// drop them; the mapper links (purchase_order / purchase_order_item /
	// delivery_note_item) ride along on every row that is sent
	const items = receive.value.items
		.filter((row) => Number(row.qty) > 0)
		.map((row) => ({
			item_code: row.item_code,
			qty: Number(row.qty),
			rate: row.rate,
			uom: row.uom,
			warehouse: row.warehouse,
			purchase_order: row.purchase_order,
			purchase_order_item: row.purchase_order_item,
			delivery_note_item: row.delivery_note_item,
		}))
	if (!items.length) {
		showError(__("At least one item is required"))
		return
	}
	saving.value = true
	try {
		const payload = {
			supplier: receive.value.supplier,
			posting_date: receive.value.posting_date,
			company: receive.value.company,
			set_warehouse: receive.value.set_warehouse || null,
			currency: receive.value.currency || null,
			items,
		}
		if (receive.value.inter_company_reference)
			payload.inter_company_reference = receive.value.inter_company_reference
		const res = await call(`${PR_API}.save_purchase_receipt`, {
			data: JSON.stringify(payload),
			submit: submitAfter ? 1 : 0,
		})
		showSuccess(
			submitAfter
				? __("Purchase Receipt {0} submitted", [res?.name])
				: __("Purchase Receipt {0} saved", [res?.name]),
		)
		view.value = "list"
		await loadOrders()
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	} finally {
		saving.value = false
	}
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
		set_warehouse: "",
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

// AutocompleteSelect only shows a label for options it has; make the
// prefilled supplier selectable/displayable even before a search runs
function seedSupplierOption(name, label) {
	if (name && !supplierOptions.value.some((o) => o.value === name)) {
		supplierOptions.value.unshift({ value: name, label: label || name })
	}
}

async function openNew() {
	form.value = blankForm()
	view.value = "form"
	const d = await loadPoDefaults()
	if (form.value.name) return // user switched away mid-await
	form.value.supplier = d?.supplier || ""
	form.value.set_warehouse = d?.warehouse || ""
	seedSupplierOption(d?.supplier, d?.supplier_name)
	if (d?.supplier) await onSupplierSelect(d.supplier)
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
			set_warehouse: d?.set_warehouse || "",
			// prefill the loaded template — an empty value here is the explicit
			// remove-tax signal, not "leave untouched"
			taxes_and_charges: d?.taxes_and_charges || "",
			remarks: d?.remarks || "",
			items: (d?.items || []).map((i) => ({
				item_code: i.item_code,
				item_name: i.item_name,
				qty: i.qty,
				uom: i.uom,
				uoms: [i.uom],
				rate: i.rate,
			})),
		}
		seedSupplierOption(d?.supplier, d?.supplier_name)
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

// the cashier's last-chosen UOM per item, remembered in this browser — wins
// over the backend's default (custom field, else stock UOM)
const PO_UOM_STORAGE_KEY = "posNext.poUom"

function readLastUoms() {
	try {
		return JSON.parse(localStorage.getItem(PO_UOM_STORAGE_KEY)) || {}
	} catch {
		return {}
	}
}

function lastUsedUom(itemCode) {
	return readLastUoms()[itemCode] || null
}

function rememberUom(itemCode, uom) {
	const all = readLastUoms()
	all[itemCode] = uom
	localStorage.setItem(PO_UOM_STORAGE_KEY, JSON.stringify(all))
}

function uomOptionsOf(row) {
	// rows edited from an existing PO start with their saved UOM only — the
	// full list fills in after the first change re-fetches details
	return row.uoms?.length ? row.uoms : [row.uom]
}

async function fetchItemDetails(code, uom, qty) {
	const defaults = await loadPoDefaults()
	return call(`${API}.get_purchase_item_details`, {
		item_code: code,
		supplier: form.value.supplier || null,
		pos_profile: props.posProfile,
		qty: qty || 1,
		transaction_date: form.value.transaction_date || null,
		warehouse: props.warehouse || null,
		uom: uom || null,
		price_list: defaults?.price_list || null,
	})
}

async function onItemPick(code) {
	itemPick.value = ""
	if (!code) return
	try {
		// the remembered pick (if any) prices the row in that UOM from the start
		const d = await fetchItemDetails(code, lastUsedUom(code), 1)
		form.value.items.push({
			item_code: d?.item_code || code,
			item_name: d?.item_name || code,
			qty: 1,
			uom: d?.uom || d?.default_uom || d?.stock_uom || "",
			uoms: (d?.uoms || []).map((u) => u.uom),
			// kept for the payload but never shown — pricing lives in Desk
			rate: d?.price_list_rate || d?.rate || 0,
		})
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	}
}

async function onUomChange(row) {
	rememberUom(row.item_code, row.uom)
	try {
		const d = await fetchItemDetails(row.item_code, row.uom, row.qty)
		row.rate = d?.price_list_rate || d?.rate || row.rate
		if (d?.uoms?.length) row.uoms = d.uoms.map((u) => u.uom)
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	}
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
		const defaults = await loadPoDefaults()
		const payload = {
			supplier: form.value.supplier,
			transaction_date: form.value.transaction_date,
			schedule_date: form.value.schedule_date,
			company: props.company,
			currency: form.value.currency,
			set_warehouse: form.value.set_warehouse || null,
			buying_price_list: defaults?.price_list || null,
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
