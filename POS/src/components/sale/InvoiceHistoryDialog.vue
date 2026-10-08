<template>
	<PosDialogShell
		v-model="show"
		:title="__('Invoice History')"
		:subtitle="__('Recent invoices of this POS profile')"
		icon="clock"
	>
		<template v-if="!viewingInvoice" #toolbar>
			<div class="flex items-center gap-2">
				<div class="relative flex-1">
					<FeatherIcon
						name="search"
						class="absolute start-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none"
					/>
					<input
						v-model="searchTerm"
						type="text"
						:placeholder="__('Search by invoice number, buyer, or customer...')"
						class="w-full h-10 ps-10 pe-3 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
						@input="onSearchInput"
					/>
				</div>
				<button
					type="button"
					class="w-10 h-10 shrink-0 rounded-lg border border-gray-300 flex items-center justify-center text-gray-600 hover:bg-gray-50 disabled:opacity-50"
					:disabled="invoicesResource.loading"
					:title="__('Refresh')"
					:aria-label="__('Refresh')"
					@click="loadInvoices"
				>
					<FeatherIcon
						name="refresh-cw"
						:class="['w-4 h-4', invoicesResource.loading && !isLoadingMore && 'animate-spin']"
					/>
				</button>
			</div>
		</template>

		<!-- Detail opens in place so the dialog keeps its size; Back returns to the list -->
		<InvoiceDetailDialog
			v-if="viewingInvoice"
			v-model="detailOpen"
			embedded
			:invoice-name="viewingInvoice"
			:pos-profile="posProfile"
			:currency="currency"
			@print-invoice="printInvoice"
		/>

		<div v-else-if="invoicesResource.loading && !isLoadingMore" class="text-center py-12">
			<div class="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500 mx-auto"></div>
			<p class="mt-3 text-xs text-gray-500">{{ __('Loading invoices...') }}</p>
		</div>

		<div v-else-if="filteredInvoices.length === 0" class="text-center py-12">
			<div class="w-16 h-16 bg-gray-100 rounded-full flex items-center justify-center mx-auto mb-3">
				<FeatherIcon name="file-text" class="h-8 w-8 text-gray-400" />
			</div>
			<p class="text-sm font-medium text-gray-900">{{ __('No invoices found') }}</p>
		</div>

		<div v-else class="flex flex-col gap-2">
			<div
				v-for="invoice in filteredInvoices"
				:key="invoice.name + invoice.posting_date"
				class="bg-white border border-gray-200 rounded-xl p-3 hover:border-blue-300 transition-colors"
			>
				<div class="flex items-start justify-between gap-3">
					<div class="flex-1 min-w-0">
						<div class="flex items-center gap-2 flex-wrap">
							<h4 class="text-sm font-semibold text-gray-900">{{ invoice.name }}</h4>
							<span
								v-if="invoice.is_return"
								class="text-xs px-2 py-0.5 rounded-full font-medium bg-red-100 text-red-800"
							>
								{{ __("Return") }}
							</span>
							<span
								v-else
								:class="['text-xs px-2 py-0.5 rounded-full font-medium', getInvoiceStatusColor(invoice)]"
							>
								{{ __(invoice.status) }}
							</span>
							<span
								v-if="invoice.pos_queue_number"
								class="text-xs font-mono font-semibold text-blue-700 bg-blue-50 px-1.5 py-0.5 rounded"
								:title="__('Queue Number')"
							>
								#{{ formatQueueNumber(invoice.pos_queue_number) }}
							</span>
						</div>
						<p class="text-sm text-gray-700 mt-1 truncate">
							{{ invoice.buyer_name || invoice.customer_name }}
						</p>
						<p class="text-xs text-gray-500 flex flex-wrap items-center gap-x-1.5 mt-0.5">
							<span>{{ formatDateTime(invoice.posting_date, invoice.posting_time) }}</span>
							<template v-if="invoice.cashier_name">
								<span class="text-gray-300">·</span>
								<span>{{ invoice.cashier_name }}</span>
							</template>
							<span class="text-gray-300">·</span>
							<span>{{ formatPaymentModes(invoice) }}</span>
						</p>
					</div>
					<p class="text-base font-bold text-gray-900 tabular-nums shrink-0">
						{{ formatCurrency(invoice.grand_total) }}
					</p>
				</div>
				<div class="flex items-center justify-end gap-1.5 mt-2 pt-2 border-t border-gray-100">
					<button
						type="button"
						class="inline-flex items-center gap-1.5 h-8 px-2.5 rounded-lg text-xs font-medium text-gray-700 hover:bg-gray-100"
						@click="viewInvoice(invoice)"
					>
						<FeatherIcon name="eye" class="w-4 h-4 text-blue-600" />
						{{ __('View') }}
					</button>
					<button
						type="button"
						class="inline-flex items-center gap-1.5 h-8 px-2.5 rounded-lg text-xs font-medium text-gray-700 hover:bg-gray-100"
						@click="printInvoice(invoice)"
					>
						<FeatherIcon name="printer" class="w-4 h-4 text-green-600" />
						{{ __('Print') }}
					</button>
					<button
						v-if="canCreateReturn(invoice)"
						type="button"
						class="inline-flex items-center gap-1.5 h-8 px-2.5 rounded-lg text-xs font-medium text-gray-700 hover:bg-orange-50"
						@click="openReturnModal(invoice)"
					>
						<FeatherIcon name="corner-up-left" class="w-4 h-4 text-orange-600" />
						{{ __('Return') }}
					</button>
				</div>
			</div>

			<div v-if="hasMore" class="text-center pt-1">
				<Button variant="subtle" :loading="isLoadingMore" @click="loadMore">
					{{ __('Load More') }}
				</Button>
			</div>
		</div>
	</PosDialogShell>

	<!-- Return Invoice Dialog -->
	<ReturnInvoiceDialog
		v-model="showReturnDialog"
		:pos-profile="posProfile"
		:pos-opening-shift="posOpeningShift"
		:currency="currency"
		:preselected-invoice="selectedInvoiceForReturn"
		@return-created="handleReturnCreated"
	/>
</template>

<script setup>
import { useFormatters } from "@/composables/useFormatters"
import { useToast } from "@/composables/useToast"
import {
	DEFAULT_CURRENCY,
	DEFAULT_LOCALE,
	formatCurrency as formatCurrencyUtil,
} from "@/utils/currency"
import { getInvoiceStatusColor } from "@/utils/invoice"
import { formatQueueNumber } from "@/utils/queue/queueNumber"
import PosDialogShell from "@/components/common/PosDialogShell.vue"
import { Button, FeatherIcon, createResource } from "frappe-ui"
import { computed, ref, watch } from "vue"
import ReturnInvoiceDialog from "./ReturnInvoiceDialog.vue"
import InvoiceDetailDialog from "@/components/invoices/InvoiceDetailDialog.vue"

const { showError } = useToast()
const { formatDate, formatTime } = useFormatters()

const props = defineProps({
	modelValue: Boolean,
	posProfile: String,
	posOpeningShift: String,
	currency: {
		type: String,
		default: DEFAULT_CURRENCY,
	},
})

function formatCurrency(amount) {
	return formatCurrencyUtil(Number.parseFloat(amount || 0), props.currency)
}

const emit = defineEmits([
	"update:modelValue",
	"create-return",
	"print-invoice",
	"return-created",
])

const show = ref(props.modelValue)
const invoices = ref([])
const searchTerm = ref("")
const page = ref(0)
const pageSize = 20
const hasMore = ref(true)

// Return dialog state
const showReturnDialog = ref(false)
const selectedInvoiceForReturn = ref(null)

// Track if we're loading more (appending) vs fresh load (replacing)
const isLoadingMore = ref(false)

// Create resource for loading invoices
const invoicesResource = createResource({
	url: "pos_next.api.invoices.get_invoices",
	makeParams() {
		return {
			pos_profile: props.posProfile,
			search: searchTerm.value || undefined,
			limit: pageSize,
			offset: page.value * pageSize,
		}
	},
	auto: false,
	onSuccess(data) {
		if (data && Array.isArray(data)) {
			const newInvoices = data.map((inv) => ({
				...inv,
				items_count: 0,
			}))

			if (isLoadingMore.value) {
				// Append to existing list
				invoices.value = [...invoices.value, ...newInvoices]
			} else {
				// Replace the list
				invoices.value = newInvoices
			}

			// Check if there are more results
			hasMore.value = data.length === pageSize
			isLoadingMore.value = false
		}
		isLoadingMore.value = false
	},
	onError(error) {
		console.error("Error loading invoices:", error)
		showError(__("Failed to load invoices"))
		isLoadingMore.value = false
	},
})

watch(
	() => props.modelValue,
	(val) => {
		show.value = val
		if (val && props.posProfile) {
			loadInvoices()
		}
	},
)

watch(show, (val) => {
	emit("update:modelValue", val)
	// Dialog cleanup: reset state when dialog closes
	if (!val) {
		viewingInvoice.value = null
		searchTerm.value = ""
		page.value = 0
		invoices.value = []
		hasMore.value = true
	}
})

// Clear selected invoice when return dialog closes
watch(showReturnDialog, (val) => {
	if (!val) {
		selectedInvoiceForReturn.value = null
	}
})

const filteredInvoices = computed(() => {
	if (!searchTerm.value) return invoices.value

	const term = searchTerm.value.toLowerCase()
	return invoices.value.filter(
		(inv) =>
			inv.name.toLowerCase().includes(term) ||
			inv.customer_name?.toLowerCase().includes(term) ||
			(inv.buyer_name || "").toLowerCase().includes(term),
	)
})

function loadInvoices() {
	if (props.posProfile) {
		// Reset to first page for fresh load
		page.value = 0
		isLoadingMore.value = false
		invoicesResource.reload()
	}
}

function loadMore() {
	page.value++
	isLoadingMore.value = true
	invoicesResource.reload()
}

function debounce(fn, wait) {
	let timer
	return (...args) => {
		clearTimeout(timer)
		timer = setTimeout(() => fn(...args), wait)
	}
}

const _debouncedSearch = debounce(() => {
	loadInvoices()
}, 300)

function onSearchInput() {
	_debouncedSearch()
}

const viewingInvoice = ref(null)
const detailOpen = computed({
	get: () => !!viewingInvoice.value,
	set: (val) => {
		if (!val) viewingInvoice.value = null
	},
})

function viewInvoice(invoice) {
	viewingInvoice.value = invoice.name
}

function printInvoice(invoice) {
	emit("print-invoice", invoice)
}

function canCreateReturn(invoice) {
	// Can create return if:
	// 1. Invoice is submitted (docstatus === 1)
	// 2. Not already a return invoice
	// 3. Status is not "Credit Note Issued" (already has a return)
	return (
		invoice.docstatus === 1 &&
		!invoice.is_return &&
		invoice.status !== "Credit Note Issued"
	)
}

function openReturnModal(invoice) {
	selectedInvoiceForReturn.value = invoice
	showReturnDialog.value = true
}

function handleReturnCreated(returnInvoice) {
	// Refresh the invoice list to show updated statuses
	invoicesResource.reload()
	// Emit the event to parent
	emit("return-created", returnInvoice)
}

function formatDateTime(date, time) {
	const dateStr = formatDate(date)
	const timeStr = formatTime(time)
	return [dateStr, timeStr].filter(Boolean).join(" ")
}

function formatPaymentModes(invoice) {
	const payments = Array.isArray(invoice?.payments) ? invoice.payments : []
	const validPayments = payments.filter((payment) => payment.mode_of_payment)

	if (validPayments.length === 0) {
		return __("No payment mode")
	}

	if (validPayments.length === 1) {
		return __(validPayments[0].mode_of_payment)
	}

	return validPayments
		.map(
			(payment) =>
				`${__(payment.mode_of_payment)} ${formatCurrency(Number.parseFloat(payment.amount || 0))}`,
		)
		.join(", ")
}
</script>
