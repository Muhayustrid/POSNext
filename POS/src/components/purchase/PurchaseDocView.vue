<template>
	<div class="space-y-4">
		<!-- header: back, breadcrumb, indicator, badge, actions -->
		<div data-testid="doc-header" class="flex flex-wrap items-center gap-2">
			<button
				type="button"
				data-testid="doc-back"
				class="-ms-1 rounded p-1 text-gray-500 hover:text-gray-900"
				:aria-label="__('Back')"
				:title="__('Back')"
				@click="emit('back')"
			>
				<FeatherIcon name="arrow-left" class="h-4 w-4" />
			</button>
			<nav class="flex min-w-0 items-center gap-1.5 text-sm">
				<span class="text-gray-500">{{ title }}</span>
				<span class="text-gray-300">/</span>
				<span class="truncate font-semibold text-gray-900">
					{{ doc?.name || "" }}
				</span>
			</nav>

			<span v-if="statusLabel" class="inline-flex items-center gap-1.5">
				<span
					data-testid="status-dot"
					:class="['h-2 w-2 shrink-0 rounded-full', toneClass(doc)]"
				></span>
				<span class="text-sm text-gray-700">{{ statusLabel }}</span>
			</span>

			<span
				v-if="doc?.inter_company_order_reference"
				data-testid="factory-so-badge"
				class="inline-flex items-center rounded-full bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700 ring-1 ring-inset ring-blue-200"
			>
				{{ __("Factory SO") }}
			</span>

			<div class="ms-auto flex items-center gap-2" data-testid="doc-actions">
				<slot name="actions" />
			</div>
		</div>

		<!-- Loading spinner / state -->
		<div
			v-if="loading"
			class="py-12 text-center text-sm text-gray-400"
			data-testid="doc-loading"
		>
			{{ __("Loading...") }}
		</div>

		<div v-else class="space-y-4">
			<!-- Details Card -->
			<section data-testid="doc-details" class="rounded-xl border border-gray-200 bg-white">
				<h4 class="px-4 pb-2 pt-4 text-sm font-semibold text-gray-900">{{ __("Details") }}</h4>
				<div class="grid grid-cols-2 gap-x-4 gap-y-3 border-t border-gray-200 px-4 py-3 md:grid-cols-3">
					<div v-if="doc?.supplier_name || doc?.supplier" class="min-w-0">
						<p class="text-xs text-gray-500">{{ __("Supplier") }}</p>
						<p class="truncate text-sm font-semibold text-gray-900">
							{{ doc.supplier_name || doc.supplier }}
						</p>
					</div>

					<div v-if="doc?.transaction_date" class="min-w-0">
						<p class="text-xs text-gray-500">{{ __("Transaction Date") }}</p>
						<p class="truncate text-sm text-gray-900">{{ formatDate(doc.transaction_date) }}</p>
					</div>
					<div v-else-if="doc?.posting_date" class="min-w-0">
						<p class="text-xs text-gray-500">{{ __("Posting Date") }}</p>
						<p class="truncate text-sm text-gray-900">{{ formatDate(doc.posting_date) }}</p>
					</div>

					<div v-if="doc?.schedule_date" class="min-w-0">
						<p class="text-xs text-gray-500">{{ __("Required By") }}</p>
						<p class="truncate text-sm text-gray-900">{{ formatDate(doc.schedule_date) }}</p>
					</div>

					<div v-if="doc?.set_warehouse" class="min-w-0">
						<p class="text-xs text-gray-500">{{ __("Warehouse") }}</p>
						<p class="truncate text-sm text-gray-900">{{ doc.set_warehouse }}</p>
					</div>

					<div v-if="doc?.inter_company_order_reference" class="min-w-0">
						<p class="text-xs text-gray-500">{{ __("Factory SO") }}</p>
						<p class="truncate text-sm text-gray-900">{{ doc.inter_company_order_reference }}</p>
					</div>

					<div v-if="purchaseOrdersText" class="min-w-0">
						<p class="text-xs text-gray-500">{{ __("Purchase Order") }}</p>
						<p class="truncate text-sm text-gray-900">{{ purchaseOrdersText }}</p>
					</div>

					<div v-if="doc?.inter_company_reference" class="min-w-0">
						<p class="text-xs text-gray-500">{{ __("Delivery Note") }}</p>
						<p class="truncate text-sm text-gray-900">{{ doc.inter_company_reference }}</p>
					</div>

					<div v-if="doc?.remarks" class="col-span-2 min-w-0 md:col-span-3">
						<p class="text-xs text-gray-500">{{ __("Remarks") }}</p>
						<p class="whitespace-pre-line text-sm text-gray-900">{{ doc.remarks }}</p>
					</div>
				</div>
			</section>

			<!-- Items Table -->
			<section data-testid="doc-items" class="rounded-xl border border-gray-200 bg-white overflow-hidden">
				<div class="px-4 pb-3 pt-4">
					<h4 class="text-sm font-semibold text-gray-900">{{ __("Items") }}</h4>
				</div>

				<div class="overflow-x-auto">
					<table class="w-full text-start text-sm">
						<thead class="border-y border-gray-200 bg-gray-50 text-xs text-gray-500">
							<tr>
								<th class="px-4 py-2 text-start font-medium">{{ __("Item") }}</th>
								<th class="px-4 py-2 text-end font-medium">
									{{ hasReceivedQty ? __("Ordered") : __("Qty") }}
								</th>
								<th v-if="hasReceivedQty" class="px-4 py-2 text-end font-medium">
									{{ __("Received") }}
								</th>
								<th class="px-4 py-2 text-start font-medium">{{ __("UOM") }}</th>
								<th v-if="hasRate" class="px-4 py-2 text-end font-medium">{{ __("Rate") }}</th>
								<th v-if="hasRate" class="px-4 py-2 text-end font-medium">{{ __("Amount") }}</th>
							</tr>
						</thead>
						<tbody class="divide-y divide-gray-100">
							<tr v-for="(row, idx) in doc?.items || []" :key="row.name || row.item_code || idx">
								<td class="px-4 py-2.5">
									<div class="truncate font-medium text-gray-900">
										{{ row.item_name || row.item_code }}
									</div>
									<div v-if="row.item_name && row.item_code" class="truncate text-xs text-gray-500">
										{{ row.item_code }}
									</div>
									<div v-if="kind === 'pr' && row.purchase_order" class="truncate text-xs text-gray-400">
										{{ __("PO: {0}", [row.purchase_order]) }}
									</div>
								</td>
								<td class="px-4 py-2.5 text-end tabular-nums text-gray-900">
									{{ formatQty(row.qty) }}
								</td>
								<td v-if="hasReceivedQty" class="px-4 py-2.5 text-end tabular-nums text-gray-900">
									{{ formatQty(row.received_qty) }}
								</td>
								<td class="px-4 py-2.5 text-gray-600">{{ row.uom }}</td>
								<td v-if="hasRate" class="px-4 py-2.5 text-end tabular-nums text-gray-900">
									{{ formatMoney(row.rate) }}
								</td>
								<td v-if="hasRate" class="px-4 py-2.5 text-end tabular-nums text-gray-900 font-medium">
									{{ formatMoney(row.amount) }}
								</td>
							</tr>
							<tr v-if="!(doc?.items?.length)">
								<td
									:colspan="itemColspan"
									class="px-4 py-4 text-center text-xs text-gray-400"
								>
									{{ __("No items") }}
								</td>
							</tr>
						</tbody>
					</table>
				</div>

				<!-- Totals Block -->
				<div
					v-if="hasTotals"
					data-testid="doc-totals"
					class="flex flex-col items-end gap-1.5 border-t border-gray-200 bg-gray-50/50 px-4 py-3"
				>
					<div v-if="'net_total' in doc && doc.net_total != null" class="flex gap-4 text-sm text-gray-600">
						<span>{{ __("Net Total") }}</span>
						<span class="font-medium tabular-nums text-gray-900">{{ formatMoney(doc.net_total) }}</span>
					</div>
					<div
						v-if="'total_taxes_and_charges' in doc && doc.total_taxes_and_charges != null"
						class="flex gap-4 text-sm text-gray-600"
					>
						<span>{{ __("Taxes") }}</span>
						<span class="font-medium tabular-nums text-gray-900">
							{{ formatMoney(doc.total_taxes_and_charges) }}
						</span>
					</div>
					<div class="flex gap-4 text-base font-semibold text-gray-900">
						<span>{{ __("Grand Total") }}</span>
						<span class="tabular-nums" data-testid="grand-total">{{ formatMoney(doc.grand_total) }}</span>
					</div>
				</div>
			</section>

			<!-- Slot after-items (e.g. linked receipts list in PO) -->
			<slot name="after-items" />

			<!-- Attachments Section -->
			<section data-testid="doc-attachments" class="rounded-xl border border-gray-200 bg-white p-4">
				<h4 class="mb-3 text-sm font-semibold text-gray-900">{{ __("Attachments") }}</h4>
				<div v-if="doc?.attachments?.length" class="mb-3">
					<PurchaseAttachments :attached="doc.attachments" :label="__('Attached Files')" />
				</div>
				<PurchaseAttachments
					v-if="canAttach"
					editable
					:pending="pendingFiles"
					:label="__('Attach File')"
					@add="emit('attach-add', $event)"
					@remove="onAttachRemove"
				/>
				<p
					v-else-if="!(doc?.attachments?.length)"
					class="text-sm text-gray-400"
				>
					{{ __("No attachments") }}
				</p>
			</section>
		</div>
	</div>
</template>

<script setup>
import { computed } from "vue"
import { FeatherIcon } from "frappe-ui"
import PurchaseAttachments from "@/components/purchase/PurchaseAttachments.vue"
import { useFormatters } from "@/composables/useFormatters"
import { __ } from "@/utils/translation"

const props = defineProps({
	title: { type: String, default: "" },
	doc: { type: Object, default: () => ({}) },
	kind: { type: String, default: "po" }, // "po" | "pr"
	loading: { type: Boolean, default: false },
	pendingFiles: { type: Array, default: () => [] },
	canAttach: { type: Boolean, default: false },
})

const emit = defineEmits([
	"back",
	"attach-add",
	"attach-remove",
])

const { formatDate } = useFormatters()

const statusLabel = computed(
	() => props.doc?.workflow_state || props.doc?.status || "",
)

const purchaseOrdersText = computed(() => {
	if (!props.doc?.purchase_orders) return ""
	if (Array.isArray(props.doc.purchase_orders)) {
		return props.doc.purchase_orders.join(", ")
	}
	return String(props.doc.purchase_orders)
})

const hasRate = computed(() => {
	const first = props.doc?.items?.[0]
	return Boolean(
		first && "rate" in first && first.rate !== undefined && first.rate !== null,
	)
})

const hasReceivedQty = computed(() => {
	return (
		props.kind === "po" &&
		Boolean(
			props.doc?.items?.some(
				(i) =>
					"received_qty" in i &&
					i.received_qty !== undefined &&
					i.received_qty !== null,
			),
		)
	)
})

const hasTotals = computed(() => {
	return Boolean(
		props.doc &&
			"grand_total" in props.doc &&
			props.doc.grand_total !== undefined &&
			props.doc.grand_total !== null,
	)
})

const itemColspan = computed(() => {
	let cols = 3 // item, qty, uom
	if (hasReceivedQty.value) cols += 1
	if (hasRate.value) cols += 2
	return cols
})

function toneClass(doc) {
	if (!doc) return "bg-gray-400"
	if (doc.docstatus === 2 || doc.status === "Cancelled") return "bg-gray-400"
	if (doc.docstatus === 1 || doc.status === "Completed") return "bg-green-500"
	if (doc.status === "Draft" || doc.docstatus === 0) return "bg-orange-400"
	return "bg-blue-500"
}

function formatQty(value) {
	return Number(value || 0).toLocaleString("id-ID")
}

function formatMoney(amount) {
	const currency = props.doc?.currency || "Rp"
	return `${currency} ${Number(amount || 0).toLocaleString("id-ID")}`
}

function onAttachRemove(row) {
	const idx = props.pendingFiles.indexOf(row)
	emit("attach-remove", idx >= 0 ? idx : row)
}
</script>
