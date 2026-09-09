<template>
	<div class="flex flex-col gap-4">
		<!-- No active shift -->
		<div v-if="!openingShift" class="text-center py-8">
			<p class="text-sm text-gray-500">{{ __("No open shift for this session.") }}</p>
		</div>

		<!-- Loading (first load only; keep stale data visible on refresh errors) -->
		<div v-else-if="summaryResource.loading && !summary" class="text-center py-8">
			<div class="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500 mx-auto"></div>
			<p class="mt-3 text-xs text-gray-500">{{ __("Loading session summary...") }}</p>
		</div>

		<!-- Error without cached data -->
		<div v-else-if="!summary" class="text-center py-8">
			<p class="text-sm text-gray-500">{{ __("Could not load the session summary.") }}</p>
			<Button variant="subtle" class="mt-3" @click="refresh">
				{{ __("Retry") }}
			</Button>
		</div>

		<template v-else>
			<!-- Header + shift info: name, cashier, opening, closing -->
			<div class="flex items-start justify-between gap-3">
				<div class="min-w-0">
					<h3 class="text-base font-semibold text-gray-900 truncate">
						{{ summary.shift_name || summary.pos_profile }}
					</h3>
					<p class="text-xs text-gray-500 truncate">
						{{ summary.pos_profile }} · ID {{ summary.opening_shift }}
					</p>
				</div>
				<div class="flex-shrink-0 flex flex-col items-end gap-1">
					<Button
						variant="subtle"
						:loading="summaryResource.loading"
						@click="refresh"
						:title="__('Refresh')"
						:aria-label="__('Refresh')"
					>
						<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
						</svg>
					</Button>
					<p class="text-xs text-gray-500">
						{{ __("Updated {0}", [formatDateTime(summary.generated_at)]) }}
					</p>
				</div>
			</div>

			<!-- Stale data notice (offline / refresh failed) -->
			<div
				v-if="stale"
				class="rounded-lg bg-amber-50 border border-amber-300 p-2.5 text-xs text-amber-800"
				role="status"
			>
				{{ __("Showing data from the last successful refresh.") }}
			</div>

			<!-- 1. Shift info -->
			<dl
				class="grid grid-cols-1 sm:grid-cols-3 gap-x-4 gap-y-2 rounded-lg bg-gray-50 border border-gray-200 p-3"
				data-test="shift-info"
				:aria-label="__('Session Information')"
			>
				<div class="min-w-0">
					<dt class="text-xs text-gray-500">{{ __("Cashier") }}</dt>
					<dd class="text-sm font-semibold text-gray-900 truncate">{{ summary.cashier }}</dd>
				</div>
				<div class="min-w-0">
					<dt class="text-xs text-gray-500">{{ __("Opened") }}</dt>
					<dd class="text-sm font-semibold text-gray-900 tabular-nums">{{ formatDateTime(summary.period_start_date) }}</dd>
				</div>
				<div class="min-w-0">
					<dt class="text-xs text-gray-500">{{ __("Closed") }}</dt>
					<dd class="text-sm font-semibold text-gray-900 tabular-nums flex flex-wrap items-center gap-1.5">
						<span v-if="closing.time">{{ closing.time }}</span>
						<span
							v-if="closing.badge"
							class="text-[10px] font-medium px-1.5 py-0.5 rounded-full"
							:class="closing.badgeClass"
						>{{ closing.badge }}</span>
						<span v-if="!closing.time" class="text-gray-400">—</span>
					</dd>
					<dd v-if="closing.note" class="text-xs text-gray-500">{{ closing.note }}</dd>
				</div>
			</dl>

			<p v-if="!summary.counted_invoices" class="text-xs text-gray-500 text-center" role="status">
				{{ __("No sales yet in this session.") }}
			</p>

			<!-- 2. Sales Summary -->
			<section
				class="rounded-lg border border-blue-200 bg-blue-50/60 p-3"
				aria-labelledby="ss-sales-h"
				data-test="sales-summary"
			>
				<h3 id="ss-sales-h" class="text-xs font-semibold text-blue-700 uppercase tracking-wide">{{ __("Sales Summary") }}</h3>
				<div class="mt-2 grid grid-cols-1 sm:grid-cols-3 gap-3">
					<div class="min-w-0" data-test="kpi-total-sales">
						<div class="text-xs text-blue-700">{{ __("Total Sales") }}</div>
						<div class="text-xl sm:text-2xl font-bold text-blue-900 tabular-nums break-words">
							{{ formatMoney(summary.net_sales) }}
						</div>
						<div class="text-xs text-gray-500 mt-0.5">{{ __("After returns, tax included") }}</div>
					</div>
					<div class="min-w-0" data-test="kpi-total-orders">
						<div class="text-xs text-gray-500">{{ __("Total Orders") }}</div>
						<div class="text-lg sm:text-xl font-bold text-gray-900 tabular-nums">{{ summary.sales_count }}</div>
						<div class="text-xs text-gray-500 mt-0.5">{{ __("Submitted sales invoices (returns not counted)") }}</div>
					</div>
					<div class="min-w-0" data-test="kpi-avg-order">
						<div class="text-xs text-gray-500">{{ __("Avg / Order") }}</div>
						<div class="text-lg sm:text-xl font-bold text-gray-900 tabular-nums break-words">
							{{ formatMoney(summary.average_sale) }}
						</div>
						<div class="text-xs text-gray-500 mt-0.5">{{ __("Total Sales ÷ Total Orders") }}</div>
					</div>
				</div>
				<div
					v-if="summary.gross_sales || summary.credit_outstanding"
					class="mt-2 flex flex-wrap gap-x-5 gap-y-1 border-t border-blue-100 pt-2"
				>
					<p class="text-xs text-gray-500">
						{{ __("Gross Sales (before returns)") }}:
						<span class="font-semibold text-gray-700 tabular-nums">{{ formatMoney(summary.gross_sales) }}</span>
					</p>
					<p v-if="summary.credit_outstanding" class="text-xs text-gray-500">
						{{ __("Credit Sales (Outstanding)") }}:
						<span class="font-semibold text-gray-700 tabular-nums">{{ formatMoney(summary.credit_outstanding) }}</span>
					</p>
				</div>
				<div v-if="multiCurrency.length" class="mt-1.5 flex flex-wrap gap-x-5 gap-y-1">
					<p v-for="row in multiCurrency" :key="row.currency" class="text-xs text-gray-500 tabular-nums">
						{{ __("Total Sales ({0})", [row.currency]) }}: {{ formatCurrencyUtil(row.amount, row.currency) }}
					</p>
				</div>
			</section>

			<!-- 3. Cash Summary -->
			<section class="rounded-lg border border-gray-200 p-3" aria-labelledby="ss-cash-h" data-test="cash-summary">
				<h3 id="ss-cash-h" class="text-xs font-semibold text-gray-500 uppercase tracking-wide">{{ __("Cash Summary") }}</h3>
				<dl class="mt-1 flex flex-col">
					<div class="flex items-baseline justify-between gap-3 py-1.5 border-b border-gray-100">
						<dt class="text-sm text-gray-600">{{ __("Opening Balance") }}</dt>
						<dd class="text-sm font-semibold text-gray-900 tabular-nums">{{ formatMoney(summary.opening_cash) }}</dd>
					</div>
					<div class="flex items-baseline justify-between gap-3 py-1.5 border-b border-gray-100">
						<dt class="text-sm text-gray-600">{{ __("Cash Receipts") }}</dt>
						<dd class="text-sm font-semibold text-gray-900 tabular-nums">{{ formatMoney(summary.cash_collected) }}</dd>
					</div>
					<div class="flex items-baseline justify-between gap-3 py-1.5 border-b border-gray-100">
						<dt class="text-sm text-gray-600">{{ __("Cash Expense") }}</dt>
						<dd
							class="text-sm font-semibold tabular-nums"
							:class="summary.expense == null ? 'text-gray-400 font-medium' : 'text-gray-900'"
						>
							{{ expenseDisplay }}
						</dd>
					</div>
					<div class="flex items-baseline justify-between gap-3 py-1.5">
						<dt class="text-sm font-medium text-gray-900">{{ __("Cash in Hand") }}</dt>
						<dd class="text-base font-bold text-gray-900 tabular-nums">{{ formatMoney(summary.cash_in_hand) }}</dd>
					</div>
					<p class="text-xs text-gray-500 -mt-0.5">{{ __("Cash in Hand is the balance before any expenses") }}</p>
				</dl>
			</section>

			<!-- 4. Payment Methods -->
			<section class="rounded-lg border border-gray-200 p-3" aria-labelledby="ss-pay-h" data-test="payments-section">
				<h3 id="ss-pay-h" class="text-xs font-semibold text-gray-500 uppercase tracking-wide">{{ __("Payment Methods") }}</h3>
				<table v-if="summary.payments.length" class="mt-1 w-full text-sm" data-test="payments-table">
					<tbody>
						<tr
							v-for="row in summary.payments"
							:key="row.mode_of_payment"
							class="border-b border-gray-100 last:border-b-0"
						>
							<td class="py-1.5 text-gray-700 min-w-0 break-words">
								{{ row.mode_of_payment }}
								<span
									v-if="!row.amount"
									class="text-xs text-gray-400"
									:title="__('Unused')"
								>{{ __("Unused") }}</span>
								<span
									v-if="row.configured === false"
									class="text-xs text-amber-700"
									:title="__('Not on the POS Profile')"
								>{{ __("Not on the POS Profile") }}</span>
							</td>
							<td
								class="py-1.5 text-end font-semibold tabular-nums whitespace-nowrap"
								:class="row.amount ? 'text-gray-900' : 'text-gray-400'"
							>
								{{ formatMoney(row.amount) }}
							</td>
						</tr>
						<tr class="border-t border-gray-200">
							<td class="py-1.5 text-gray-600">{{ __("Total Cash") }}</td>
							<td class="py-1.5 text-end font-semibold text-gray-900 tabular-nums whitespace-nowrap">{{ formatMoney(summary.total_cash) }}</td>
						</tr>
						<tr class="border-b border-t border-gray-100">
							<td class="py-1.5 text-gray-600">{{ __("Total Non-Cash") }}</td>
							<td class="py-1.5 text-end font-semibold text-gray-900 tabular-nums whitespace-nowrap">{{ formatMoney(summary.total_non_cash) }}</td>
						</tr>
						<tr>
							<td class="py-1.5 font-semibold text-gray-900">{{ __("Grand Total") }}</td>
							<td class="py-1.5 text-end font-bold text-gray-900 tabular-nums whitespace-nowrap">{{ formatMoney(summary.methods_grand_total) }}</td>
						</tr>
					</tbody>
				</table>
				<p v-else class="mt-1 text-xs text-gray-500">{{ __("No payment methods are configured on this POS Profile.") }}</p>
			</section>

			<!-- 5. Charges & Discounts -->
			<section class="rounded-lg border border-gray-200 p-3" aria-labelledby="ss-charges-h" data-test="charges-section">
				<h3 id="ss-charges-h" class="text-xs font-semibold text-gray-500 uppercase tracking-wide">{{ __("Charges & Discounts") }}</h3>
				<dl class="mt-1 flex flex-col">
					<div
						v-for="charge in summary.other_charges"
						:key="charge.account_head"
						class="flex items-baseline justify-between gap-3 py-1.5 border-b border-gray-100"
					>
						<dt class="text-sm text-gray-600 min-w-0 break-words">
							{{ charge.label }}
							<span class="text-xs text-gray-400">{{ __("posted as its own account") }}</span>
						</dt>
						<dd class="text-sm font-semibold text-gray-900 tabular-nums whitespace-nowrap">{{ formatMoney(charge.amount) }}</dd>
					</div>
					<div
						v-if="summary.other_charges.length > 1"
						class="flex items-baseline justify-between gap-3 py-1.5 border-b border-gray-100"
					>
						<dt class="text-sm text-gray-600">{{ __("Other Charges") }}</dt>
						<dd class="text-sm font-semibold text-gray-900 tabular-nums">{{ formatMoney(summary.other_charges_total) }}</dd>
					</div>
					<div class="flex items-baseline justify-between gap-3 py-1.5 border-b border-gray-100">
						<dt class="text-sm text-gray-600">{{ __("Tax / PPN") }}</dt>
						<dd class="text-sm font-semibold text-gray-900 tabular-nums">{{ formatMoney(summary.tax_total) }}</dd>
					</div>
					<div class="flex items-baseline justify-between gap-3 py-1.5">
						<dt class="text-sm text-gray-600">{{ __("Discount") }}</dt>
						<dd class="text-sm font-semibold text-gray-900 tabular-nums">{{ formatMoney(summary.total_discount) }}</dd>
					</div>
				</dl>
				<p class="text-xs text-gray-500">{{ __("Charges are classified by each account's type on the invoice.") }}</p>
			</section>

			<!-- 6. Refund -->
			<section class="rounded-lg border border-gray-200 p-3" aria-labelledby="ss-refund-h" data-test="refund-section">
				<h3 id="ss-refund-h" class="text-xs font-semibold text-gray-500 uppercase tracking-wide">{{ __("Refund") }}</h3>
				<div class="mt-1 flex items-baseline justify-between gap-3">
					<p class="text-sm text-gray-600">
						{{ __("Total Refund") }}
						<span class="text-xs text-gray-500">{{ __("{0} return invoices", [summary.returns_count]) }}</span>
					</p>
					<p
						class="text-base font-bold tabular-nums whitespace-nowrap"
						:class="summary.returns_total ? 'text-red-700' : 'text-gray-900'"
					>
						{{ returnsDisplay }}
					</p>
				</div>
			</section>

			<!-- 7. Sales per Category -->
			<section class="flex flex-col gap-1.5" :aria-labelledby="__('Sales per Category')" data-test="categories-section">
				<h3 id="ss-categories-h" class="text-xs font-semibold text-gray-500 uppercase tracking-wide">
					{{ __("Sales per Category") }}
				</h3>
				<div class="overflow-x-auto rounded-lg border border-gray-200">
					<table class="w-full text-sm" data-test="categories-table">
						<thead>
							<tr class="bg-gray-50 text-xs text-gray-500">
								<th class="px-3 py-2 font-medium text-start">{{ __("Category") }}</th>
								<th class="px-3 py-2 font-medium text-end">{{ __("Qty") }}</th>
								<th class="px-3 py-2 font-medium text-end">{{ __("Net Revenue") }}</th>
							</tr>
						</thead>
						<tbody>
							<tr v-if="!summary.categories.length">
								<td colspan="3" class="px-3 py-3 text-center text-xs text-gray-500">
									{{ __("No sales yet in this session.") }}
								</td>
							</tr>
							<tr v-for="cat in summary.categories" :key="cat.category ?? 'none'" class="border-t border-gray-100">
								<td class="px-3 py-2 text-gray-900 break-words">{{ cat.category || __("No category") }}</td>
								<td class="px-3 py-2 text-end text-gray-700 tabular-nums whitespace-nowrap">{{ formatQty(cat.qty) }}</td>
								<td class="px-3 py-2 text-end font-medium text-gray-900 tabular-nums whitespace-nowrap">
									{{ formatMoney(cat.base_net_amount) }}
								</td>
							</tr>
						</tbody>
					</table>
				</div>
				<p class="text-xs text-gray-500">{{ __("Net revenue is pre-tax; returns are subtracted.") }}</p>
				<p v-if="summary.categories_truncated" class="text-xs text-gray-500">
					{{
						__(
							"Showing top {0} of {1} categories; session totals above cover everything.",
							[summary.categories_shown, summary.categories_total_groups],
						)
					}}
				</p>
			</section>

			<!-- 8. Item & package detail (secondary, collapsible) -->
			<details class="rounded-lg border border-gray-200 px-3 py-2" data-test="items-details">
				<summary class="cursor-pointer select-none text-sm font-medium text-gray-700">
					{{ __("Item & Package Detail") }}
				</summary>
				<div class="mt-3 flex flex-col gap-4">
					<section v-if="summary.packages.length" class="flex flex-col gap-1.5">
						<h4 class="text-xs font-semibold text-gray-500 uppercase">{{ __("Sold Packages") }}</h4>
						<div class="overflow-x-auto rounded-lg border border-gray-200">
							<table class="w-full text-sm">
								<thead>
									<tr class="bg-gray-50 text-xs text-gray-500">
										<th class="px-3 py-2 font-medium text-start">{{ __("Package") }}</th>
										<th class="px-3 py-2 font-medium text-end">{{ __("Qty") }}</th>
										<th class="px-3 py-2 font-medium text-end">{{ __("Revenue") }}</th>
									</tr>
								</thead>
								<tbody>
									<tr v-for="pkg in summary.packages" :key="pkg.item_code" class="border-t border-gray-100">
										<td class="px-3 py-2 text-gray-900 break-words">{{ pkg.item_name }}</td>
										<td class="px-3 py-2 text-end text-gray-700 tabular-nums whitespace-nowrap">{{ formatQty(pkg.qty) }}</td>
										<td class="px-3 py-2 text-end font-medium text-gray-900 tabular-nums whitespace-nowrap">
											{{ formatMoney(pkg.base_net_amount) }}
										</td>
									</tr>
								</tbody>
							</table>
						</div>
					</section>

					<section v-if="summary.items.length" class="flex flex-col gap-1.5" data-test="items-section">
						<h4 class="text-xs font-semibold text-gray-500 uppercase">{{ __("Items Sold") }}</h4>
						<div class="overflow-x-auto max-h-64 overflow-y-auto rounded-lg border border-gray-200">
							<table class="w-full text-sm" data-test="items-table">
								<thead class="sticky top-0">
									<tr class="bg-gray-50 text-xs text-gray-500">
										<th class="px-3 py-2 font-medium text-start">{{ __("Item") }}</th>
										<th class="px-3 py-2 font-medium text-end">{{ __("Qty") }}</th>
										<th class="px-3 py-2 font-medium text-end">{{ __("Revenue") }}</th>
									</tr>
								</thead>
								<tbody>
									<tr v-for="item in summary.items" :key="item.item_code" class="border-t border-gray-100">
										<td class="px-3 py-2 text-gray-900">
											<span
												v-if="item.qty < 0"
												class="me-1.5 inline-block w-1.5 h-1.5 rounded-full bg-red-400 align-middle"
												:title="__('Returned')"
											></span>{{ item.item_name }}
											<span
												v-if="duplicateItemNames.has(item.item_name)"
												class="block text-xs text-gray-500"
											>{{ item.item_code }}</span>
										</td>
										<td class="px-3 py-2 text-end text-gray-700 tabular-nums whitespace-nowrap">{{ formatQty(item.qty) }}</td>
										<td class="px-3 py-2 text-end font-medium text-gray-900 tabular-nums whitespace-nowrap">
											{{ formatMoney(item.base_net_amount) }}
										</td>
									</tr>
								</tbody>
							</table>
						</div>
						<p v-if="summary.items_truncated" class="text-xs text-gray-500">
							{{
								__(
									"Showing top {0} of {1} items; the totals above cover the whole session.",
									[summary.items_shown, summary.items_total_groups],
								)
							}}
						</p>
					</section>

					<p v-if="!summary.items.length && !summary.packages.length" class="text-xs text-gray-500">
						{{ __("No sales yet in this session.") }}
					</p>
				</div>
			</details>
		</template>
	</div>
</template>

<script setup>
import { useFormatters } from "@/composables/useFormatters"
import {
	DEFAULT_CURRENCY,
	formatCurrency as formatCurrencyUtil,
} from "@/utils/currency"
import { Button, createResource } from "frappe-ui"
import { computed, ref, watch } from "vue"

const { formatDate, formatTime } = useFormatters()

const props = defineProps({
	openingShift: { type: String, default: "" },
})

const stale = ref(false)

const summaryResource = createResource({
	url: "pos_next.api.shifts.get_session_summary",
	makeParams() {
		return { opening_shift: props.openingShift }
	},
	auto: false,
	onError() {
		// Keep the last successful snapshot on screen and flag it as stale
		stale.value = true
	},
})

const summary = computed(() => summaryResource.data || null)

function refresh() {
	if (!props.openingShift) return
	if (summary.value) stale.value = false
	summaryResource.reload()
}

watch(
	() => props.openingShift,
	(val) => {
		stale.value = false
		if (val) summaryResource.reload()
	},
	{ immediate: true },
)

const formatMoney = (amount) =>
	formatCurrencyUtil(
		Number.parseFloat(amount || 0),
		summary.value?.company_currency || DEFAULT_CURRENCY,
	)

// Closing time: actual from the linked closing shift, else the frozen
// schedule deadline clearly marked as an estimate, else nothing.
const closing = computed(() => {
	const s = summary.value
	if (!s?.closing_time) {
		return { time: "", badge: "", badgeClass: "", note: __("Not closed yet") }
	}
	if (s.closing_source === "actual") {
		return {
			time: formatDateTime(s.closing_time),
			badge: __("Actual"),
			badgeClass: "bg-green-100 text-green-800",
			note: "",
		}
	}
	return {
		time: formatDateTime(s.closing_time),
		badge: __("estimate"),
		badgeClass: "bg-amber-100 text-amber-800",
		note: "",
	}
})

// No shift-scoped expense ledger exists yet: show an explicit "not recorded"
// instead of a fake zero.
const expenseDisplay = computed(() => {
	const s = summary.value
	if (s?.expense == null) return __("Not recorded")
	return formatMoney(s.expense)
})

// Display only: never render "-0" — the sign appears only for real values.
const returnsDisplay = computed(() => {
	const value = Number.parseFloat(summary.value?.returns_total || 0)
	if (!value) return formatMoney(0)
	return value < 0 ? formatMoney(value) : `-${formatMoney(value)}`
})

// Only meaningful when a session mixes currencies; single-currency sessions
// would just repeat the hero number.
const multiCurrency = computed(() => {
	const rows = summary.value?.currency_breakdown || []
	return rows.length > 1 ? rows : []
})

// Show the item code under names that appear more than once, so duplicate
// rows stay distinguishable (display only; aggregation is untouched).
const duplicateItemNames = computed(() => {
	const names = (summary.value?.items || []).map((i) => i.item_name)
	return new Set(names.filter((name, i) => names.indexOf(name) !== i))
})

const formatQty = (qty) =>
	new Intl.NumberFormat(undefined, { maximumFractionDigits: 3 }).format(
		Number.parseFloat(qty || 0),
	)

function formatDateTime(date) {
	const dateStr = formatDate(date)
	const timeStr = formatTime(date)
	return [dateStr, timeStr].filter(Boolean).join(" ")
}
</script>
