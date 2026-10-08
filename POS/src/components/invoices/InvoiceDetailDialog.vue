<template>
	<!-- embedded: rendered in place (inside another dialog) instead of a side drawer -->
	<Teleport to="body" :disabled="embedded">
		<Transition :name="embedded ? '' : 'drawer'">
			<div
				v-if="show"
				:class="embedded ? 'flex' : 'fixed inset-0 z-[400] flex'"
				:role="embedded ? undefined : 'dialog'"
				:aria-modal="embedded ? undefined : 'true'"
				:aria-label="__('Invoice Detail')"
			>
				<!-- Backdrop -->
				<div v-if="!embedded" class="absolute inset-0 bg-black/30" @click="show = false"></div>

				<!-- Panel -->
				<div
					:class="
						embedded
							? 'w-full bg-white flex flex-col'
							: 'relative ms-auto h-full w-full sm:max-w-md bg-white shadow-2xl flex flex-col'
					"
				>
					<template v-if="loading">
						<div class="flex-1 flex flex-col items-center justify-center py-12">
							<div
								class="animate-spin rounded-full h-10 w-10 border-b-2 border-blue-500"
							></div>
							<p class="mt-3 text-sm text-gray-500">
								{{ __("Loading invoice details...") }}
							</p>
						</div>
					</template>

					<template v-else-if="invoiceData">
						<!-- Header -->
						<div :class="embedded ? 'pb-3 border-b border-gray-100' : 'px-5 pt-5 pb-4 border-b border-gray-100'">
							<div class="flex items-start justify-between gap-3">
								<button
									v-if="embedded"
									type="button"
									class="w-9 h-9 shrink-0 rounded-lg border border-gray-200 flex items-center justify-center text-gray-600 hover:bg-gray-50"
									:aria-label="__('Back')"
									:title="__('Back')"
									@click="show = false"
								>
									<FeatherIcon name="arrow-left" class="w-4 h-4 rtl:rotate-180" />
								</button>
								<div class="min-w-0 flex-1">
									<div class="text-xs font-semibold uppercase tracking-wide text-blue-600">
										{{ __("Invoice Detail") }}
									</div>
									<h3 class="mt-1 text-xl font-bold text-gray-900 truncate">
										{{ invoiceData.name }}
									</h3>
									<div class="mt-1 text-sm text-gray-500">
										{{ formatDate(invoiceData.posting_date) }} ·
										{{ formatTime(invoiceData.posting_time) }}
									</div>
								</div>
								<button
									v-if="!embedded"
									class="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100"
									:aria-label="__('Close')"
									@click="show = false"
								>
									<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
										<path
											stroke-linecap="round"
											stroke-linejoin="round"
											stroke-width="2"
											d="M6 18L18 6M6 6l12 12"
										/>
									</svg>
								</button>
							</div>
						</div>

						<!-- Scrollable Body -->
						<div
							:class="[
								'flex flex-col gap-5',
								embedded ? 'py-4' : 'flex-1 overflow-y-auto px-5 py-4',
							]"
						>
							<!-- Customer Card -->
							<div
								class="flex items-center justify-between gap-3 bg-gray-50 border border-gray-100 rounded-lg p-3"
							>
								<div class="flex items-center gap-3 min-w-0">
									<div
										class="w-10 h-10 rounded-full bg-blue-100 text-blue-700 flex items-center justify-center text-sm font-semibold flex-shrink-0"
									>
										{{ customerInitials }}
									</div>
									<div class="min-w-0">
										<div class="text-sm font-semibold text-gray-900 truncate">
											{{ invoiceData.customer_name || invoiceData.customer }}
										</div>
										<div
											v-if="invoiceData.pos_queue_number"
											class="text-xs font-mono font-semibold text-blue-700"
										>
											#{{ formatQueueNumber(invoiceData.pos_queue_number) }}
										</div>
										<div
											v-if="invoiceData.cashier_name || invoiceData.owner"
											class="mt-0.5 flex items-center gap-1 text-xs text-gray-600"
											data-test="invoice-cashier"
										>
											<FeatherIcon name="user" class="w-3.5 h-3.5 text-gray-400" :aria-hidden="true" />
											{{ __("Cashier") }}:
											<span class="font-medium text-gray-900">{{ invoiceData.cashier_name || invoiceData.owner }}</span>
										</div>
										<div v-if="invoiceData.return_against" class="text-xs text-gray-500">
											{{ __("Return Against:") }} {{ invoiceData.return_against }}
										</div>
									</div>
								</div>
								<span
									v-if="invoiceData.is_return"
									class="px-2.5 py-1 text-xs font-semibold rounded-full bg-red-100 text-red-800 flex-shrink-0"
								>
									{{ __("Return Invoice") }}
								</span>
								<span
									v-else
									:class="[
										'px-2.5 py-1 text-xs font-semibold rounded-full flex-shrink-0',
										getInvoiceStatusColor(invoiceData),
									]"
								>
									{{ __(invoiceData.status) }}
								</span>
							</div>

							<!-- Return Type Notice: Added to Customer Credit (no payments, negative outstanding) -->
							<div
								v-if="invoiceData.is_return && isAddedToCustomerCredit"
								class="bg-blue-50 rounded-lg p-3 border border-blue-200"
							>
								<h4 class="text-sm font-semibold text-blue-900">
									{{ __("Added to Customer Credit") }}
								</h4>
								<p class="text-xs text-blue-700 mt-1">
									{{
										__(
											"The return amount was added to the customer credit balance. No cash refund was given."
										)
									}}
								</p>
							</div>

							<!-- Return Type Notice: Cash Refund (has payments) -->
							<div
								v-else-if="invoiceData.is_return && isCashRefund"
								class="bg-green-50 rounded-lg p-3 border border-green-200"
							>
								<h4 class="text-sm font-semibold text-green-900">
									{{ __("Cash Refund") }}
								</h4>
								<p class="text-xs text-green-700 mt-1">
									{{ __("The customer received a cash refund for this return.") }}
								</p>
							</div>

							<!-- Pay on Account Notice (for original credit sales) -->
							<div
								v-else-if="!invoiceData.is_return && isCreditSale"
								class="bg-amber-50 rounded-lg p-3 border border-amber-200"
							>
								<h4 class="text-sm font-semibold text-amber-900">
									{{ __("Pay on Account") }}
								</h4>
								<p class="text-xs text-amber-700 mt-1">
									{{
										__(
											"This invoice was sold on credit. The customer owes the full amount."
										)
									}}
								</p>
							</div>

							<!-- Items Section -->
							<div>
								<h4 class="text-sm font-semibold text-gray-700 mb-3">
									{{ __("Items") }}
								</h4>
								<div class="flex flex-col divide-y divide-gray-100">
									<div
										v-for="(item, idx) in invoiceData.items"
										:key="idx"
										class="py-2.5 flex items-start justify-between gap-3"
									>
										<div class="min-w-0">
											<div class="text-sm font-medium text-gray-900">
												{{ item.item_name }}
											</div>
											<div class="text-xs text-gray-500">
												{{ item.quantity }}{{ item.uom ? " " + item.uom : "" }} ×
												{{ formatCurrency(item.rate) }}
											</div>
											<div
												v-if="item.discount_percentage"
												class="text-xs text-orange-600"
											>
												{{ __("Discount:") }} {{ item.discount_percentage }}%
											</div>
										</div>
										<div class="text-sm font-semibold text-gray-900 text-end">
											{{ formatCurrency(item.amount) }}
										</div>
									</div>
								</div>
							</div>

							<!-- Totals -->
							<div class="flex flex-col gap-2">
								<div class="flex justify-between text-sm">
									<span class="text-gray-600">{{ __("Net Total:") }}</span>
									<span class="font-medium text-gray-900">{{
										formatCurrency(invoiceData.net_total || invoiceData.total)
									}}</span>
								</div>
								<div
									v-if="invoiceData.total_taxes_and_charges"
									class="flex justify-between text-sm"
								>
									<span class="text-gray-600">{{ __("Taxes:") }}</span>
									<span class="font-medium text-gray-900">{{
										formatCurrency(invoiceData.total_taxes_and_charges)
									}}</span>
								</div>
								<div
									v-if="invoiceData.discount_amount"
									class="flex justify-between text-sm"
								>
									<span class="text-gray-600">{{ __("Discount:") }}</span>
									<span class="font-medium text-red-600"
										>-{{ formatCurrency(invoiceData.discount_amount) }}</span
									>
								</div>
								<div class="pt-2 border-t border-gray-200 flex justify-between">
									<span class="font-semibold text-gray-900">{{ __("Grand Total") }}</span>
									<span class="font-bold text-lg text-blue-700">{{
										formatCurrency(invoiceData.grand_total)
									}}</span>
								</div>
							</div>

							<!-- Payment Block -->
							<div
								v-if="invoiceData.payments && invoiceData.payments.length > 0"
								class="flex flex-col gap-2"
							>
								<div
									v-for="(payment, idx) in invoiceData.payments"
									:key="idx"
								>
									<div
										v-if="payment.amount"
										class="flex justify-between items-center text-sm"
									>
										<span class="text-gray-600">{{ payment.mode_of_payment }}</span>
										<span class="font-medium text-gray-900">{{
											formatCurrency(payment.amount)
										}}</span>
									</div>
								</div>
								<div
									v-if="invoiceData.paid_amount"
									class="flex justify-between text-sm"
								>
									<span class="text-gray-600">{{ __("Paid Amount") }}</span>
									<span class="font-semibold text-green-600">{{
										formatCurrency(invoiceData.paid_amount)
									}}</span>
								</div>
								<div
									v-if="invoiceData.change_amount && invoiceData.change_amount > 0"
									class="flex justify-between items-center bg-gray-50 rounded-lg px-3 py-2"
								>
									<span class="text-sm font-semibold text-gray-900">{{ __("Change") }}</span>
									<span class="text-base font-bold text-gray-900">{{
										formatCurrency(invoiceData.change_amount)
									}}</span>
								</div>
								<!-- For return invoices with negative outstanding (credit to customer) -->
								<div
									v-if="invoiceData.is_return && invoiceData.outstanding_amount < 0"
									class="flex justify-between text-sm"
								>
									<span class="text-gray-600">{{ __("Customer Credit:") }}</span>
									<span class="font-semibold text-blue-600">{{
										formatCurrency(Math.abs(invoiceData.outstanding_amount))
									}}</span>
								</div>
								<!-- For regular invoices with outstanding (customer owes) -->
								<div
									v-else-if="
										invoiceData.outstanding_amount &&
										invoiceData.outstanding_amount > 0
									"
									class="flex justify-between text-sm"
								>
									<span class="text-gray-600">{{ __("Outstanding:") }}</span>
									<span class="font-semibold text-orange-600">{{
										formatCurrency(invoiceData.outstanding_amount)
									}}</span>
								</div>
							</div>

							<!-- Remarks -->
							<div v-if="invoiceData.remarks">
								<h4 class="text-sm font-semibold text-gray-700 mb-1">
									{{ __("Remarks") }}
								</h4>
								<p class="text-sm text-gray-600">{{ invoiceData.remarks }}</p>
							</div>
						</div>

						<!-- Sticky Footer -->
						<div
							:class="[
								'border-t border-gray-100 flex items-center gap-3 bg-white',
								embedded ? 'sticky -bottom-3 pt-3 pb-3' : 'px-5 py-4',
							]"
						>
							<Button variant="subtle" @click="show = false">
								{{ embedded ? __("Back") : __("Close") }}
							</Button>
							<Button variant="solid" class="flex-1" @click="handlePrint">
								<template #prefix>
									<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
										<path
											stroke-linecap="round"
											stroke-linejoin="round"
											stroke-width="2"
											d="M17 17h2a2 2 0 002-2v-4a2 2 0 00-2-2H5a2 2 0 00-2 2v4a2 2 0 002 2h2m2 4h6a2 2 0 002-2v-4a2 2 0 00-2-2H9a2 2 0 00-2 2v4a2 2 0 002 2zm8-12V5a2 2 0 00-2-2H9a2 2 0 00-2 2v4h10z"
										/>
									</svg>
								</template>
								{{ __("Print") }}
							</Button>
						</div>
					</template>

					<template v-else>
						<div class="flex-1 flex flex-col items-center justify-center py-12">
							<svg
								class="h-12 w-12 text-gray-400"
								fill="none"
								stroke="currentColor"
								viewBox="0 0 24 24"
							>
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									stroke-width="2"
									d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
								/>
							</svg>
							<p class="mt-2 text-sm text-gray-500">
								{{ __("Failed to load invoice details") }}
							</p>
							<Button v-if="embedded" class="mt-4" variant="subtle" @click="show = false">
								<template #prefix><FeatherIcon name="arrow-left" class="w-4 h-4 rtl:rotate-180" /></template>
								{{ __("Back") }}
							</Button>
						</div>
					</template>
				</div>
			</div>
		</Transition>
	</Teleport>
</template>

<script setup>
import { useFormatters } from "@/composables/useFormatters";
import { DEFAULT_CURRENCY, formatCurrency as formatCurrencyUtil } from "@/utils/currency";
import { getInvoiceStatusColor } from "@/utils/invoice";
import { formatQueueNumber } from "@/utils/queue/queueNumber";
import { logger } from "@/utils/logger";
import { hydrateLocalOnlyInvoice, isLocalOnlyInvoiceName } from "@/utils/printInvoice";
import { Button, FeatherIcon, call } from "frappe-ui";
import { ref, watch, computed, onMounted, onBeforeUnmount } from "vue";

const log = logger.create("InvoiceDetailDialog");
const { formatDate, formatTime } = useFormatters();

const props = defineProps({
	modelValue: Boolean,
	invoiceName: String,
	posProfile: String,
	embedded: Boolean,
	currency: {
		type: String,
		default: DEFAULT_CURRENCY,
	},
});

function formatCurrency(amount) {
	return formatCurrencyUtil(Number.parseFloat(amount || 0), props.currency);
}

const emit = defineEmits(["update:modelValue", "print-invoice"]);

const show = ref(props.modelValue);
const loading = ref(false);
const invoiceData = ref(null);

const customerInitials = computed(() => {
	const name = invoiceData.value?.customer_name || invoiceData.value?.customer || "";
	return (
		name
			.split(/\s+/)
			.filter(Boolean)
			.slice(0, 2)
			.map((w) => w[0].toUpperCase())
			.join("") || "?"
	);
});

// Computed: Check if this is a credit sale (Pay on Account - no payments, full outstanding)
const isCreditSale = computed(() => {
	if (!invoiceData.value) return false;
	const hasNoPayments = !invoiceData.value.payments || invoiceData.value.payments.length === 0;
	const totalPaid =
		invoiceData.value.payments?.reduce((sum, p) => sum + Math.abs(p.amount || 0), 0) || 0;
	const grandTotal = Math.abs(invoiceData.value.grand_total || 0);
	const outstanding = Math.abs(invoiceData.value.outstanding_amount || 0);
	// Credit sale if no payments and outstanding equals grand total
	return hasNoPayments || (totalPaid < 0.01 && Math.abs(outstanding - grandTotal) < 0.01);
});

// Computed: Check if this return was added to customer credit (no payments, negative outstanding)
const isAddedToCustomerCredit = computed(() => {
	if (!invoiceData.value || !invoiceData.value.is_return) return false;
	const hasNoPayments = !invoiceData.value.payments || invoiceData.value.payments.length === 0;
	const totalPaid =
		invoiceData.value.payments?.reduce((sum, p) => sum + Math.abs(p.amount || 0), 0) || 0;
	const hasNegativeOutstanding = (invoiceData.value.outstanding_amount || 0) < 0;
	// Added to customer credit if no payments AND outstanding is negative
	return (hasNoPayments || totalPaid < 0.01) && hasNegativeOutstanding;
});

// Computed: Check if this return was a cash refund (has payments)
const isCashRefund = computed(() => {
	if (!invoiceData.value || !invoiceData.value.is_return) return false;
	const totalPaid =
		invoiceData.value.payments?.reduce((sum, p) => sum + Math.abs(p.amount || 0), 0) || 0;
	// Cash refund if payments were made (refund given)
	return totalPaid >= 0.01;
});

watch(
	() => props.modelValue,
	(val) => {
		show.value = val;
		if (val && props.invoiceName) {
			loadInvoiceDetails();
		}
	},
	// embedded hosts mount this already open; load on mount too
	{ immediate: true }
);

watch(show, (val) => {
	emit("update:modelValue", val);
	if (!val) {
		// Clear data when closing
		invoiceData.value = null;
	}
});

function onKeydown(e) {
	// embedded: the host dialog owns Escape
	if (e.key === "Escape" && show.value && !props.embedded) {
		show.value = false;
	}
}

onMounted(() => {
	window.addEventListener("keydown", onKeydown);
});

onBeforeUnmount(() => {
	window.removeEventListener("keydown", onKeydown);
});

async function loadInvoiceDetails() {
	if (!props.invoiceName) return;

	loading.value = true;
	try {
		if (isLocalOnlyInvoiceName(props.invoiceName)) {
			// Hydrate from sessionStorage first, fall back to IndexedDB so a
			// post-reload detail view still resolves offline receipts.
			const cached = await hydrateLocalOnlyInvoice({ name: props.invoiceName });
			if (cached?.items?.length > 0) {
				const result = JSON.parse(JSON.stringify(cached));
				result.items = result.items.map((item) => ({
					...item,
					quantity: item.quantity ?? item.qty,
				}));
				invoiceData.value = result;
				return;
			}
			invoiceData.value = null;
			return;
		}

		const result = await call("pos_next.api.invoices.get_invoice", {
			invoice_name: props.invoiceName,
		});

		// Map server 'qty' to 'quantity' for internal consistency
		if (result && result.items) {
			result.items = result.items.map((item) => ({
				...item,
				quantity: item.qty,
			}));
		}
		invoiceData.value = result;
	} catch (error) {
		log.error("Error loading invoice details:", error);
		invoiceData.value = null;
	} finally {
		loading.value = false;
	}
}

function handlePrint() {
	if (!invoiceData.value) return;
	emit("print-invoice", invoiceData.value);
}
</script>

<style scoped>
.drawer-enter-active,
.drawer-leave-active {
	transition: opacity 0.2s ease;
}
.drawer-enter-active > div:last-child,
.drawer-leave-active > div:last-child {
	transition: transform 0.25s ease;
}
.drawer-enter-from,
.drawer-leave-to {
	opacity: 0;
}
.drawer-enter-from > div:last-child,
.drawer-leave-to > div:last-child {
	transform: translateX(100%);
}
[dir="rtl"] .drawer-enter-from > div:last-child,
[dir="rtl"] .drawer-leave-to > div:last-child {
	transform: translateX(-100%);
}
</style>
