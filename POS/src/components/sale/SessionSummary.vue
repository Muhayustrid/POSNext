<template>
	<div class="flex flex-col gap-5">
		<!-- Period selector: chips for every profile member. Mobile folds them
		     into one pill that opens a period sheet; desktop keeps the chip row. -->
		<div
			v-if="openingShift || posProfile"
			class="flex items-center justify-between gap-2 md:hidden"
		>
			<button
				type="button"
				class="flex min-w-0 items-center gap-1.5 rounded-full border border-gray-200 bg-white px-3 py-1.5 text-sm text-gray-700"
				data-test="period-filter-button"
				@click="sheetOpen = true"
			>
				<FeatherIcon name="calendar" class="h-4 w-4 shrink-0 text-gray-500" :aria-hidden="true" />
				<span class="min-w-0 truncate">{{ sheetPeriodLabel }}</span>
				<FeatherIcon name="chevron-down" class="h-4 w-4 shrink-0 text-gray-400" :aria-hidden="true" />
			</button>
			<Button
				variant="subtle"
				:loading="loading"
				@click="refresh"
				:title="__('Refresh')"
				:aria-label="__('Refresh')"
			>
				<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
				</svg>
			</Button>
		</div>
		<div
			v-if="openingShift || posProfile"
			class="hidden flex-wrap items-center gap-3 md:flex"
			data-test="period-bar"
		>
			<div class="inline-flex rounded-xl bg-gray-100 p-1" role="group" :aria-label="__('Period')">
				<button
					v-for="opt in periodOptions"
					:key="opt.value"
					type="button"
					class="rounded-lg px-3.5 py-1.5 text-sm font-medium transition-colors"
					:class="
						period === opt.value
							? 'bg-white text-blue-700 shadow-sm'
							: 'text-gray-600 hover:text-gray-900'
					"
					:data-test="`period-chip-${opt.value}`"
					:aria-pressed="period === opt.value"
					@click="period = opt.value"
				>
					{{ opt.label }}
				</button>
			</div>
			<div
				v-if="period === 'custom'"
				class="inline-flex items-center gap-2 rounded-xl border border-gray-200 bg-white px-3 py-1.5 focus-within:border-blue-400"
			>
				<FeatherIcon name="calendar" class="h-4 w-4 shrink-0 text-gray-400" :aria-hidden="true" />
				<input
					v-model="customFrom"
					type="date"
					:max="customTo || undefined"
					class="border-0 bg-transparent p-0 text-sm text-gray-900 focus:ring-0"
					:aria-label="__('From Date')"
					data-test="period-from"
				/>
				<span class="text-gray-400">→</span>
				<input
					v-model="customTo"
					type="date"
					:min="customFrom || undefined"
					class="border-0 bg-transparent p-0 text-sm text-gray-900 focus:ring-0"
					:aria-label="__('To Date')"
					data-test="period-to"
				/>
			</div>
		</div>

		<!-- Mobile period sheet: presets as full-width rows; custom keeps the
		     sheet open for its date inputs and Apply. -->
		<BottomSheet :open="sheetOpen" :title="__('Period')" @update:open="sheetOpen = $event">
			<template #default="{ close }">
				<button
					v-for="opt in periodOptions"
					:key="opt.value"
					type="button"
					class="flex w-full items-center justify-between rounded-xl px-3 py-2.5 text-sm"
					:class="
						period === opt.value
							? 'bg-blue-50 font-semibold text-blue-700'
							: 'text-gray-700'
					"
					:data-test="`period-option-${opt.value}`"
					@click="selectPeriod(opt.value)"
				>
					{{ opt.label }}
					<FeatherIcon
						v-if="period === opt.value"
						name="check"
						class="h-4 w-4"
						:aria-hidden="true"
					/>
				</button>
				<div
					v-if="period === 'custom'"
					class="mt-2 flex flex-col gap-2 border-t border-gray-100 px-1 pt-3"
				>
					<div class="flex items-center gap-2">
						<input
							v-model="customFrom"
							type="date"
							:max="customTo || undefined"
							class="min-w-0 flex-1 rounded-md border border-gray-300 px-2 py-1.5 text-sm text-gray-900"
							:aria-label="__('From Date')"
							data-test="sheet-period-from"
						/>
						<span class="text-xs text-gray-500">–</span>
						<input
							v-model="customTo"
							type="date"
							:min="customFrom || undefined"
							class="min-w-0 flex-1 rounded-md border border-gray-300 px-2 py-1.5 text-sm text-gray-900"
							:aria-label="__('To Date')"
							data-test="sheet-period-to"
						/>
					</div>
					<Button variant="solid" class="w-full" data-test="period-apply" @click="close">
						{{ __("Apply") }}
					</Button>
				</div>
			</template>
		</BottomSheet>

		<!-- Nothing to summarise: neither an open shift nor a profile -->
		<div v-if="!openingShift && !posProfile" class="text-center py-8">
			<p class="text-sm text-gray-500">{{ __("No open shift for this session.") }}</p>
		</div>

		<!-- Loading (first load only; keep stale data visible on refresh errors) -->
		<div v-else-if="loading && !summary" class="text-center py-8">
			<div class="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500 mx-auto"></div>
			<p class="mt-3 text-xs text-gray-500">{{ __("Loading session summary...") }}</p>
		</div>

		<!-- Custom range not complete yet -->
		<div v-else-if="!canLoad" class="text-center py-8" role="status">
			<p class="text-sm text-gray-500">{{ __("Pick a from and to date to load the recap.") }}</p>
		</div>

		<!-- Error without cached data -->
		<div v-else-if="!summary" class="text-center py-8">
			<p class="text-sm text-gray-500">{{ __("Could not load the session summary.") }}</p>
			<Button variant="subtle" class="mt-3" @click="refresh">
				{{ __("Retry") }}
			</Button>
		</div>

		<template v-else>
			<!-- Header: clean band — bold title over one quiet meta line. -->
			<div class="flex items-start justify-between gap-3">
				<div class="min-w-0">
					<h3 class="text-lg font-bold text-gray-900 truncate">
						{{ isShiftMode ? summary.shift_name || summary.pos_profile : summary.pos_profile }}
					</h3>
					<p
						v-if="isShiftMode"
						class="truncate text-xs text-gray-500 tabular-nums"
						data-test="shift-info"
					>
						{{ summary.opening_shift }} · {{ __("Cashier") }}: {{ summary.cashier }}
						· {{ __("Opened") }} {{ formatDateTime(summary.period_start_date) }}
						· <template v-if="closing.time">{{ __("Closed") }} {{ closing.time }}<template v-if="closing.badge"> ({{ closing.badge }})</template></template><template v-else>{{ closing.note }}</template>
					</p>
					<p
						v-else
						class="truncate text-xs text-gray-500 tabular-nums"
						data-test="period-info"
					>
						{{ periodLabel }} · {{ __("Shifts") }} {{ summary.shift_count ?? 0 }} ·
						{{ __("All cashiers") }}
					</p>
				</div>
				<div class="flex-shrink-0 flex flex-col items-end gap-1">
					<div class="flex items-center gap-1">
						<Button
							variant="subtle"
							:loading="printing"
							@click="print"
							:title="__('Print')"
							:aria-label="__('Print')"
						>
							<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 17h2a2 2 0 002-2v-4a2 2 0 00-2-2H5a2 2 0 00-2 2v4a2 2 0 002 2h2m2 4h6a2 2 0 002-2v-4a2 2 0 00-2-2H9a2 2 0 00-2 2v4a2 2 0 002 2zm8-12V5a2 2 0 00-2-2H9a2 2 0 00-2 2v4h10z"/>
							</svg>
						</Button>
						<Button
							variant="subtle"
							:loading="loading"
							@click="refresh"
							:title="__('Refresh')"
							:aria-label="__('Refresh')"
						>
							<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
							</svg>
						</Button>
					</div>
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

			<p v-if="!summary.counted_invoices" class="text-xs text-gray-500 text-center" role="status">
				{{ emptyText }}
			</p>

			<!-- Top row: the hero the shift is judged on, gross beside it. -->
			<div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
				<section
					aria-labelledby="ss-sales-h"
					data-test="sales-summary"
					class="relative overflow-hidden rounded-2xl bg-blue-600 p-6 text-white shadow-sm"
					:class="summary.gross_sales || summary.credit_outstanding ? '' : 'lg:col-span-2'"
				>
					<h3 id="ss-sales-h" class="sr-only">{{ __("Sales Summary") }}</h3>
					<div
						class="pointer-events-none absolute -bottom-12 -right-12 h-36 w-36 rounded-full border-[14px] border-white/10"
						aria-hidden="true"
					></div>
					<div data-test="kpi-total-sales" class="relative">
						<div class="text-xs font-medium text-blue-100">{{ __("Total Sales") }}</div>
						<div class="mt-1 text-3xl font-bold text-white tabular-nums">
							{{ formatMoney(summary.net_sales) }}
						</div>
						<div class="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm text-blue-100 tabular-nums">
							<span>{{ __("{0} orders", [summary.sales_count]) }}</span>
							<span>{{ __("Avg {0} / order", [formatMoney(summary.average_sale)]) }}</span>
						</div>
						<div class="mt-1 text-xs text-blue-100/80">{{ __("After returns, tax included") }}</div>
						<div v-if="multiCurrency.length" class="mt-2 flex flex-wrap gap-x-5 gap-y-1">
							<p v-for="row in multiCurrency" :key="row.currency" class="text-xs text-blue-100 tabular-nums">
								{{ __("Total Sales ({0})", [row.currency]) }}: {{ formatCurrencyUtil(row.amount, row.currency) }}
							</p>
						</div>
					</div>
				</section>

				<section
					v-if="summary.gross_sales || summary.credit_outstanding"
					aria-labelledby="ss-gross-h"
					data-test="gross-sales"
					class="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm"
				>
					<h3 id="ss-gross-h" class="sr-only">{{ __("Gross Sales") }}</h3>
					<div class="text-xs font-medium text-gray-500">{{ __("Gross Sales") }}</div>
					<div class="mt-1 text-3xl font-bold text-gray-900 tabular-nums">
						{{ formatMoney(summary.gross_sales) }}
					</div>
					<div class="mt-2 flex items-center gap-1.5 text-xs text-gray-500">
						<span class="h-1.5 w-1.5 rounded-full bg-blue-500" aria-hidden="true"></span>
						{{ __("Before returns, incl. tax") }}
					</div>
					<p v-if="summary.credit_outstanding" class="mt-2 text-xs text-gray-500">
						{{ __("Credit Sales (Outstanding)") }}:
						<span class="font-semibold text-gray-700 tabular-nums">{{ formatMoney(summary.credit_outstanding) }}</span>
					</p>
				</section>
			</div>

			<!-- Report body: white cards on the page background. -->
			<div class="grid grid-cols-1 gap-4 lg:grid-cols-2" data-test="recap-sheet">
				<!-- Cash Summary -->
				<section aria-labelledby="ss-cash-h" data-test="cash-summary" class="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm">
					<div class="flex items-center gap-3">
						<div class="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl bg-gray-100">
							<span class="text-sm font-bold text-gray-600" aria-hidden="true">Rp</span>
						</div>
						<div class="min-w-0">
							<p class="text-xs uppercase tracking-widest text-gray-500">{{ __("Cash in & out") }}</p>
							<h3 id="ss-cash-h" class="text-base font-semibold text-gray-900">{{ __("Cash Summary") }}</h3>
						</div>
					</div>
					<dl class="mt-4 flex flex-col">
						<div class="flex items-baseline justify-between gap-3 py-2 border-b border-gray-100">
							<dt class="text-sm text-gray-600">{{ __("Opening Balance") }}</dt>
							<dd class="text-sm font-semibold text-gray-900 tabular-nums">{{ formatMoney(summary.opening_cash) }}</dd>
						</div>
						<div class="flex items-baseline justify-between gap-3 py-2 border-b border-gray-100">
							<dt class="text-sm text-gray-600">{{ __("Cash Receipts") }}</dt>
							<dd class="text-sm font-semibold text-gray-900 tabular-nums">{{ formatMoney(summary.cash_collected) }}</dd>
						</div>
						<div class="flex items-baseline justify-between gap-3 py-2 border-b border-gray-100">
							<dt class="text-sm text-gray-600">{{ __("Cash Expense") }}</dt>
							<dd
								class="text-sm font-semibold tabular-nums"
								:class="summary.expense == null ? 'text-gray-400 font-medium' : 'text-gray-900'"
							>
								{{ expenseDisplay }}
							</dd>
						</div>
						<div class="mt-3 rounded-xl bg-blue-50 px-4 py-3">
							<div class="flex items-baseline justify-between gap-3">
								<dt class="text-sm font-semibold text-gray-900">{{ __("Cash in Hand") }}</dt>
								<dd class="text-base font-bold text-blue-700 tabular-nums">{{ formatMoney(summary.cash_in_hand) }}</dd>
							</div>
							<p class="mt-1 text-xs text-blue-700/80">{{ __("Cash in Hand is the balance before any expenses") }}</p>
						</div>
					</dl>
				</section>

				<!-- Payment Methods -->
				<section aria-labelledby="ss-pay-h" data-test="payments-section" class="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm">
					<div class="flex items-center gap-3">
						<div class="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl bg-gray-100">
							<FeatherIcon name="credit-card" class="h-5 w-5 text-gray-600" :aria-hidden="true" />
						</div>
						<div class="min-w-0">
							<p class="text-xs uppercase tracking-widest text-gray-500">
								{{ __("{0} transactions", [summary.sales_count ?? 0]) }}
							</p>
							<h3 id="ss-pay-h" class="text-base font-semibold text-gray-900">{{ __("Payment Methods") }}</h3>
						</div>
					</div>
					<table v-if="summary.payments.length" class="mt-4 w-full text-sm" data-test="payments-table">
						<tbody>
							<tr
								v-for="row in summary.payments"
								:key="row.mode_of_payment"
								class="border-b border-gray-100"
							>
								<td class="py-2 min-w-0">
									<div class="flex items-center gap-2.5">
										<div class="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg bg-gray-100">
											<span v-if="row.is_cash" class="text-[10px] font-bold text-gray-500" aria-hidden="true">Rp</span>
											<FeatherIcon v-else name="credit-card" class="h-3.5 w-3.5 text-gray-500" :aria-hidden="true" />
										</div>
										<span class="min-w-0 break-words text-gray-700">
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
										</span>
									</div>
								</td>
								<td
									class="py-2 text-end font-semibold tabular-nums whitespace-nowrap"
									:class="row.amount ? 'text-gray-900' : 'text-gray-400'"
								>
									{{ formatMoney(row.amount) }}
								</td>
							</tr>
						</tbody>
					</table>
					<p v-else class="mt-4 text-xs text-gray-500">{{ __("No payment methods are configured on this POS Profile.") }}</p>
					<div class="mt-3 grid grid-cols-2 gap-4">
						<div>
							<p class="text-xs text-gray-500">{{ __("Total Cash") }}</p>
							<p class="font-bold text-gray-900 tabular-nums">{{ formatMoney(summary.total_cash) }}</p>
						</div>
						<div>
							<p class="text-xs text-gray-500">{{ __("Total Non-Cash") }}</p>
							<p class="font-bold text-gray-900 tabular-nums">{{ formatMoney(summary.total_non_cash) }}</p>
						</div>
					</div>
					<div class="mt-3 flex items-center justify-between gap-3 rounded-xl bg-gray-50 px-4 py-3">
						<p class="text-sm font-semibold text-gray-900">{{ __("Grand Total") }}</p>
						<p class="font-bold text-gray-900 tabular-nums whitespace-nowrap">{{ formatMoney(summary.methods_grand_total) }}</p>
					</div>
				</section>

				<!-- Charges & Discounts -->
				<section aria-labelledby="ss-charges-h" data-test="charges-section" class="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm">
					<div class="flex items-center gap-3">
						<div class="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl bg-gray-100">
							<FeatherIcon name="file-text" class="h-5 w-5 text-gray-600" :aria-hidden="true" />
						</div>
						<div class="min-w-0">
							<p class="text-xs uppercase tracking-widest text-gray-500">{{ __("Transaction adjustments") }}</p>
							<h3 id="ss-charges-h" class="text-base font-semibold text-gray-900">{{ __("Charges & Discounts") }}</h3>
						</div>
					</div>
					<dl class="mt-4 flex flex-col">
						<div
							v-for="charge in summary.other_charges"
							:key="charge.account_head"
							class="flex items-baseline justify-between gap-3 py-2 border-b border-gray-100"
						>
							<dt class="text-sm text-gray-600 min-w-0 break-words">
								{{ charge.label }}
								<span class="text-xs text-gray-400">{{ __("posted as its own account") }}</span>
							</dt>
							<dd class="text-sm font-semibold text-gray-900 tabular-nums whitespace-nowrap">{{ formatMoney(charge.amount) }}</dd>
						</div>
						<div
							v-if="summary.other_charges.length > 1"
							class="flex items-baseline justify-between gap-3 py-2 border-b border-gray-100"
						>
							<dt class="text-sm text-gray-600">{{ __("Other Charges") }}</dt>
							<dd class="text-sm font-semibold text-gray-900 tabular-nums">{{ formatMoney(summary.other_charges_total) }}</dd>
						</div>
						<div class="flex items-baseline justify-between gap-3 py-2 border-b border-gray-100">
							<dt class="text-sm text-gray-600">{{ __("Tax / PPN") }}</dt>
							<dd class="text-sm font-semibold text-gray-900 tabular-nums">{{ formatMoney(summary.tax_total) }}</dd>
						</div>
						<div class="flex items-baseline justify-between gap-3 py-2">
							<dt class="text-sm text-gray-600">{{ __("Discount") }}</dt>
							<dd class="text-sm font-semibold text-gray-900 tabular-nums">{{ formatMoney(summary.total_discount) }}</dd>
						</div>
					</dl>
					<p class="text-xs text-gray-500">{{ __("Charges are classified by each account's type on the invoice.") }}</p>
				</section>

				<!-- Refunds -->
				<section aria-labelledby="ss-refund-h" data-test="refund-section" class="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm">
					<div class="flex items-center gap-3">
						<div class="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl bg-gray-100">
							<FeatherIcon name="alert-circle" class="h-5 w-5 text-gray-600" :aria-hidden="true" />
						</div>
						<div class="min-w-0">
							<p class="text-xs uppercase tracking-widest text-gray-500">{{ __("Return status") }}</p>
							<h3 id="ss-refund-h" class="text-base font-semibold text-gray-900">{{ __("Refunds") }}</h3>
						</div>
					</div>
					<div class="mt-4 flex items-start justify-between gap-3">
						<div>
							<p class="text-sm text-gray-500">{{ __("Total Refunds") }}</p>
							<p
								class="mt-1 text-3xl font-bold tabular-nums whitespace-nowrap"
								:class="summary.returns_total ? 'text-red-700' : 'text-gray-900'"
							>
								{{ returnsDisplay }}
							</p>
						</div>
						<span
							class="rounded-full px-3 py-1 text-xs font-medium whitespace-nowrap"
							:class="summary.returns_count ? 'bg-red-50 text-red-700' : 'bg-gray-100 text-gray-600'"
						>
							{{ summary.returns_count ? __("{0} return invoices", [summary.returns_count]) : __("No refunds") }}
						</span>
					</div>
				</section>

				<!-- Sales per Category -->
				<section aria-labelledby="ss-categories-h" data-test="categories-section" class="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm lg:col-span-2">
					<div class="flex items-center gap-3">
						<div class="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl bg-gray-100">
							<FeatherIcon name="shopping-bag" class="h-5 w-5 text-gray-600" :aria-hidden="true" />
						</div>
						<div class="min-w-0">
							<p class="text-xs uppercase tracking-widest text-gray-500">{{ __("Product performance") }}</p>
							<h3 id="ss-categories-h" class="text-base font-semibold text-gray-900">{{ __("Sales per Category") }}</h3>
						</div>
					</div>
					<table class="mt-4 w-full text-sm" data-test="categories-table">
						<thead>
							<tr class="text-xs uppercase text-gray-500">
								<th class="py-1.5 text-start font-medium">{{ __("Category") }}</th>
								<th class="py-1.5 text-end font-medium">{{ __("Qty") }}</th>
								<th class="py-1.5 text-end font-medium">{{ __("Net Revenue") }}</th>
							</tr>
						</thead>
						<tbody>
							<tr v-if="!summary.categories.length">
								<td colspan="3" class="py-3 text-center text-xs text-gray-500">
									{{ emptyText }}
								</td>
							</tr>
							<tr v-for="cat in summary.categories" :key="cat.category ?? 'none'" class="border-t border-gray-100">
								<td class="py-1.5 text-gray-900 break-words">{{ cat.category || __("No category") }}</td>
								<td class="py-1.5 text-end text-gray-700 tabular-nums whitespace-nowrap">{{ formatQty(cat.qty) }}</td>
								<td class="py-1.5 text-end font-medium text-gray-900 tabular-nums whitespace-nowrap">
									{{ formatMoney(cat.base_net_amount) }}
								</td>
							</tr>
						</tbody>
					</table>
					<p class="mt-2 text-xs text-gray-500">{{ __("Net revenue is pre-tax; returns are subtracted.") }}</p>
					<p v-if="summary.categories_truncated" class="text-xs text-gray-500">
						{{
							__(
								"Showing top {0} of {1} categories; session totals above cover everything.",
								[summary.categories_shown, summary.categories_total_groups],
							)
						}}
					</p>
				</section>

				<!-- Package and item breakdowns: separate cards so a bundle never
				     reads as a loose item -->
				<section aria-labelledby="ss-pkg-h" data-test="packages-section" class="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm lg:col-span-2">
					<div class="flex items-center gap-3">
						<div class="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl bg-gray-100">
							<FeatherIcon name="gift" class="h-5 w-5 text-gray-600" :aria-hidden="true" />
						</div>
						<div class="min-w-0">
							<p class="text-xs uppercase tracking-widest text-gray-500">{{ __("Transaction breakdown") }}</p>
							<h3 id="ss-pkg-h" class="text-base font-semibold text-gray-900">{{ __("Package Detail") }}</h3>
						</div>
					</div>
					<table v-if="summary.packages.length" class="mt-4 w-full text-sm" data-test="packages-table">
						<thead>
							<tr class="text-xs uppercase text-gray-500">
								<th class="py-1.5 text-start font-medium">{{ __("Package") }}</th>
								<th class="py-1.5 text-end font-medium">{{ __("Qty") }}</th>
								<th class="py-1.5 text-end font-medium">{{ __("Price") }}</th>
								<th v-if="hasItemDiscounts" class="py-1.5 text-end font-medium">{{ __("Discount") }}</th>
								<th class="py-1.5 text-end font-medium">{{ __("Subtotal") }}</th>
							</tr>
						</thead>
						<tbody>
							<tr v-for="pkg in summary.packages" :key="pkg.item_code" class="border-t border-gray-100">
								<td class="py-1.5 text-gray-900 min-w-0 break-words">
									{{ pkg.item_name }}
									<span class="block text-xs text-gray-500">{{ pkg.item_code }}</span>
								</td>
								<td class="py-1.5 text-end text-gray-700 tabular-nums whitespace-nowrap">{{ formatQty(pkg.qty) }}</td>
								<td class="py-1.5 text-end text-gray-700 tabular-nums whitespace-nowrap">
									{{ formatMoney(pkg.price_list_rate || 0) }}
								</td>
								<td v-if="hasItemDiscounts" class="py-1.5 text-end text-gray-700 tabular-nums whitespace-nowrap">
									{{ formatMoney(pkg.discount_amount || 0) }}
								</td>
								<td class="py-1.5 text-end font-medium text-gray-900 tabular-nums whitespace-nowrap">
									{{ formatMoney(pkg.base_net_amount) }}
								</td>
							</tr>
						</tbody>
					</table>
					<p v-else class="mt-4 text-xs text-gray-500">{{ emptyText }}</p>
					<p class="mt-2 text-xs text-gray-500">
						{{ __("Subtotal is net of item and invoice discounts, excluding tax.") }}
					</p>
				</section>

				<section aria-labelledby="ss-items-h" data-test="items-details" class="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm lg:col-span-2">
					<div class="flex items-center gap-3">
						<div class="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl bg-gray-100">
							<FeatherIcon name="list" class="h-5 w-5 text-gray-600" :aria-hidden="true" />
						</div>
						<div class="min-w-0">
							<p class="text-xs uppercase tracking-widest text-gray-500">{{ __("Transaction breakdown") }}</p>
							<h3 id="ss-items-h" class="text-base font-semibold text-gray-900">{{ __("Item Detail") }}</h3>
						</div>
					</div>
					<div v-if="summary.items.length" class="mt-4 overflow-x-auto max-h-64 overflow-y-auto" data-test="items-section">
						<table class="w-full text-sm" data-test="items-table">
							<thead class="sticky top-0 bg-white">
								<tr class="text-xs uppercase text-gray-500">
									<th class="py-1.5 text-start font-medium">{{ __("Item") }}</th>
									<th class="py-1.5 text-end font-medium">{{ __("Qty") }}</th>
									<th class="py-1.5 text-end font-medium">{{ __("Price") }}</th>
									<th v-if="hasItemDiscounts" class="py-1.5 text-end font-medium">{{ __("Discount") }}</th>
									<th class="py-1.5 text-end font-medium">{{ __("Subtotal") }}</th>
								</tr>
							</thead>
							<tbody>
								<tr v-for="item in summary.items" :key="item.item_code" class="border-t border-gray-100">
									<td class="py-1.5 text-gray-900 min-w-0 break-words">
										<span
											v-if="item.qty < 0"
											class="me-1.5 inline-block w-1.5 h-1.5 rounded-full bg-red-400 align-middle"
											:title="__('Returned')"
										></span>{{ item.item_name }}
										<span class="block text-xs text-gray-500">{{ item.item_code }}</span>
									</td>
									<td class="py-1.5 text-end text-gray-700 tabular-nums whitespace-nowrap">{{ formatQty(item.qty) }}</td>
									<td class="py-1.5 text-end text-gray-700 tabular-nums whitespace-nowrap">
										{{ formatMoney(item.price_list_rate || 0) }}
									</td>
									<td v-if="hasItemDiscounts" class="py-1.5 text-end text-gray-700 tabular-nums whitespace-nowrap">
										{{ formatMoney(item.discount_amount || 0) }}
									</td>
									<td class="py-1.5 text-end font-medium text-gray-900 tabular-nums whitespace-nowrap">
										{{ formatMoney(item.base_net_amount) }}
									</td>
								</tr>
							</tbody>
						</table>
					</div>
					<p v-else class="mt-4 text-xs text-gray-500">{{ emptyText }}</p>
					<p v-if="summary.items_truncated" class="mt-2 text-xs text-gray-500">
						{{
							__(
								"Showing top {0} of {1} items; the totals above cover the whole session.",
								[summary.items_shown, summary.items_total_groups],
							)
						}}
					</p>
					<p class="mt-2 text-xs text-gray-500">
						{{ __("Subtotal is net of item and invoice discounts, excluding tax.") }}
					</p>
				</section>
			</div>
		</template>
	</div>
</template>

<script setup>
import { useFormatters } from "@/composables/useFormatters"
import { useToast } from "@/composables/useToast"
import BottomSheet from "@/components/common/BottomSheet.vue"
import {
	DEFAULT_CURRENCY,
	formatCurrency as formatCurrencyUtil,
} from "@/utils/currency"
import { periodRange, printSalesRecap } from "@/utils/salesRecap"
import { Button, createResource, FeatherIcon } from "frappe-ui"
import { computed, ref, watch } from "vue"

const { formatDate, formatTime } = useFormatters()
const { showSuccess, showError } = useToast()


const props = defineProps({
	openingShift: { type: String, default: "" },
	posProfile: { type: String, default: "" },
})

// Two lenses on one dataset: the open shift (default) or a posting-date
// window over the whole profile, every cashier included. Presets resolve to
// explicit dates here; the server only ever sees from/to.
const period = ref(props.openingShift ? "shift" : "today")
const customFrom = ref("")
const customTo = ref("")
const isShiftMode = computed(() => period.value === "shift")
// Mobile period sheet (filter pill)
const sheetOpen = ref(false)
const sheetPeriodLabel = computed(() => {
	if (period.value === "custom" && range.value)
		return `${customFrom.value} – ${customTo.value}`
	return periodOptions.value.find((o) => o.value === period.value)?.label || ""
})
function selectPeriod(value) {
	period.value = value
	if (value !== "custom") sheetOpen.value = false
}
const range = computed(() =>
	isShiftMode.value
		? null
		: periodRange(period.value, { from: customFrom.value, to: customTo.value }),
)

// Chip options mirror the old select: shift only when there is one open.
const periodOptions = computed(() => [
	...(props.openingShift
		? [{ value: "shift", label: __("This Shift") }]
		: []),
	{ value: "today", label: __("Today") },
	{ value: "yesterday", label: __("Yesterday") },
	{ value: "last7", label: __("Last 7 Days") },
	{ value: "month", label: __("This Month") },
	{ value: "year", label: __("This Year") },
	{ value: "custom", label: __("Custom Range") },
])

const stale = ref(false)
const printing = ref(false)

const sessionResource = createResource({
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

const periodResource = createResource({
	url: "pos_next.api.shifts.get_period_summary",
	makeParams() {
		return {
			pos_profile: props.posProfile,
			from_date: range.value?.from,
			to_date: range.value?.to,
		}
	},
	auto: false,
	onError() {
		stale.value = true
	},
})

const activeResource = computed(() =>
	isShiftMode.value ? sessionResource : periodResource,
)
const summary = computed(() => activeResource.value.data || null)
const loading = computed(() => activeResource.value.loading)
// A custom window without both dates has nothing to fetch yet
const canLoad = computed(() =>
	isShiftMode.value
		? Boolean(props.openingShift)
		: Boolean(props.posProfile && range.value),
)

function refresh() {
	if (!canLoad.value) return
	if (summary.value) stale.value = false
	activeResource.value.reload()
}

watch(
	[() => props.openingShift, period, range],
	([shift]) => {
		stale.value = false
		if (!shift && isShiftMode.value) {
			// the shift closed underneath us: fall back to the profile's day
			period.value = "today"
			return
		}
		if (canLoad.value) activeResource.value.reload()
	},
	{ immediate: true },
)

async function print() {
	if (!summary.value || printing.value) return
	printing.value = true
	try {
		const via = await printSalesRecap(summary.value, {
			posProfile: props.posProfile || summary.value.pos_profile,
		})
		showSuccess(
			via === "pdf"
				? __("Printer unavailable — sales recap downloaded as PDF")
				: __("Sales recap sent to the printer"),
		)
	} catch (error) {
		showError(error?.message || __("Could not print the sales recap"))
	} finally {
		printing.value = false
	}
}


// Label and numbers come from the same response, so a window that is still
// loading never shows the new dates over the old figures.
const periodLabel = computed(() => {
	const s = summary.value
	if (!s?.period_from) return ""
	return `${formatDate(s.period_from)} – ${formatDate(s.period_to)}`
})

const emptyText = computed(() =>
	isShiftMode.value
		? __("No sales yet in this session.")
		: __("No sales in this period."),
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

// Old servers omit these; missing fields read as 0. The column is hidden
// entirely when nothing in the breakdown was discounted.
const hasItemDiscounts = computed(() =>
	[...(summary.value?.items || []), ...(summary.value?.packages || [])].some(
		(row) => Number.parseFloat(row.discount_amount || 0) !== 0,
	),
)

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
