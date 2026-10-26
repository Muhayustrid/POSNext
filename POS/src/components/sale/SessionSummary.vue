<template>
	<div class="mx-auto flex w-full max-w-7xl flex-col gap-4">
		<!-- Period selector: chips for every profile member. Mobile folds them
		     into one pill that opens a period sheet; desktop keeps the chip row. -->
		<div
			v-if="openingShift || posProfile"
			class="flex items-center justify-between gap-2 md:hidden"
		>
			<button
				type="button"
				class="flex min-h-[44px] min-w-0 items-center gap-1.5 rounded-full border border-gray-200 bg-white px-3 py-1.5 text-sm text-gray-700"
				data-test="period-filter-button"
				@click="sheetOpen = true"
			>
				<FeatherIcon name="calendar" class="h-4 w-4 shrink-0 text-gray-500" :aria-hidden="true" />
				<span class="min-w-0 truncate">{{ sheetPeriodLabel }}</span>
				<FeatherIcon name="chevron-down" class="h-4 w-4 shrink-0 text-gray-500" :aria-hidden="true" />
			</button>
			<RefreshButton class="pos-touch-target" :loading="loading" @click="refresh" />
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
			<!-- One from–to field, the ERPNext date-range shape: the calendar
			     popover owns ordering, so there is no second input to read. -->
			<DateRangePicker
				v-if="period === 'custom'"
				v-model="customRange"
				:allow-custom="false"
				placement="bottom-start"
				data-test="period-range"
			>
				<template #target="{ togglePopover, isOpen }">
					<button
						type="button"
						class="inline-flex min-h-9 items-center gap-2 rounded-xl border border-gray-200 bg-white px-3 py-1.5 text-sm text-gray-900 hover:border-gray-300"
						:aria-label="__('Custom Range')"
						:aria-expanded="isOpen"
						@click="togglePopover"
					>
						<FeatherIcon name="calendar" class="h-4 w-4 shrink-0 text-gray-500" :aria-hidden="true" />
						<span class="tabular-nums">{{ customRangeLabel }}</span>
						<FeatherIcon name="chevron-down" class="h-4 w-4 shrink-0 text-gray-500" :aria-hidden="true" />
					</button>
				</template>
			</DateRangePicker>
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
					<DateRangePicker
						v-model="customRange"
						:allow-custom="false"
						placement="top-start"
						data-test="sheet-period-range"
					>
						<template #target="{ togglePopover, isOpen }">
							<button
								type="button"
								class="inline-flex min-h-11 w-full items-center justify-between gap-2 rounded-xl border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900"
								:aria-label="__('Custom Range')"
								:aria-expanded="isOpen"
								@click="togglePopover"
							>
								<span class="tabular-nums">{{ customRangeLabel }}</span>
								<FeatherIcon name="calendar" class="h-4 w-4 shrink-0 text-gray-500" :aria-hidden="true" />
							</button>
						</template>
					</DateRangePicker>
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
			<Button variant="subtle" class="mt-3" :loading="loading" @click="refresh">
				{{ __("Retry") }}
			</Button>
		</div>

		<template v-else>
			<!-- Header: shift context as one quiet meta line, like the Dashboard
			     toolbar — the KPI strip below is the only blue surface. -->
			<div class="flex items-start justify-between gap-3">
				<div class="flex min-w-0 flex-wrap items-baseline gap-x-1.5">
					<h3 class="truncate text-xs font-medium text-gray-700">
						{{ isShiftMode ? summary.shift_name || summary.pos_profile : summary.pos_profile }}
					</h3>
					<p
						v-if="isShiftMode"
						class="min-w-0 truncate text-xs text-gray-700 tabular-nums"
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
						<!-- Print-failure panel: Print Ulang / Export PDF / Tutup, no automatic fallback -->
						<div
							v-if="printFailed"
							class="flex items-center gap-1 rounded-lg border border-amber-300 bg-amber-50 px-2 py-1"
						>
							<span class="text-xs text-amber-800">{{ __("Cetak gagal") }}</span>
							<Button
								variant="ghost"
								size="sm"
								:loading="printing"
								@click="print"
							>
								{{ __("Print Ulang") }}
							</Button>
							<Button
								variant="ghost"
								size="sm"
								:loading="exportingPdf"
								@click="exportRecapPDF"
							>
								{{ __("Export PDF") }}
							</Button>
							<Button variant="ghost" size="sm" @click="printFailed = false">
								{{ __("Tutup") }}
							</Button>
						</div>
						<Button
							v-else
							variant="subtle"
							class="pos-touch-target"
							:loading="printing"
							@click="print"
							:title="__('Print')"
							:aria-label="__('Print')"
						>
							<FeatherIcon name="printer" class="w-4 h-4" />
						</Button>
						<Button
							variant="subtle"
							class="pos-touch-target"
							:loading="exporting"
							data-test="recap-export"
							:title="__('Export to Excel')"
							:aria-label="__('Export to Excel')"
							@click="exportExcel"
						>
							<FeatherIcon name="download" class="w-4 h-4" />
						</Button>
						<RefreshButton class="pos-touch-target" :loading="loading" @click="refresh" />
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

			<!-- One KPI strip, same surface as the Dashboard: every figure shares
			     one card so the optional cells can never hang alone. Divided rows
			     on phones; columns only from xl, where the shell sidebar still
			     leaves room for money at 2xl. -->
			<div
				class="divide-y divide-gray-200 overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm xl:grid xl:divide-x xl:divide-y-0"
				:class="kpiColumnsClass"
				data-test="kpi-grid"
			>
				<section
					aria-labelledby="ss-sales-h"
					data-test="sales-summary"
					class="flex min-w-0 items-baseline justify-between gap-3 bg-blue-50 px-4 py-2.5 xl:flex-col-reverse xl:items-start xl:gap-0 xl:py-3.5"
				>
					<h3 id="ss-sales-h" class="sr-only">{{ __("Sales Summary") }}</h3>
					<div class="min-w-0">
						<p class="text-xs font-medium text-blue-700">{{ __("Total Sales") }}</p>
						<p class="mt-0.5 text-xs text-blue-700">{{ __("After returns, tax included") }}</p>
						<div v-if="multiCurrency.length" class="mt-1 flex flex-wrap gap-x-4 gap-y-0.5">
							<p v-for="row in multiCurrency" :key="row.currency" class="text-xs text-blue-700 tabular-nums">
								{{ __("Total Sales ({0})", [row.currency]) }}: {{ formatCurrencyUtil(row.amount, row.currency) }}
							</p>
						</div>
					</div>
					<p data-test="kpi-total-sales" class="shrink-0 text-lg font-bold text-blue-700 tabular-nums xl:text-2xl">
						{{ formatMoney(summary.net_sales) }}
					</p>
				</section>

				<section
					v-if="hasGross"
					aria-labelledby="ss-gross-h"
					data-test="gross-sales"
					class="flex min-w-0 items-baseline justify-between gap-3 px-4 py-2.5 xl:flex-col-reverse xl:items-start xl:gap-0 xl:py-3.5"
				>
					<h3 id="ss-gross-h" class="sr-only">{{ __("Gross Sales") }}</h3>
					<div class="min-w-0">
						<p class="text-xs font-medium text-gray-700">{{ __("Gross Sales") }}</p>
						<p class="mt-0.5 text-xs text-gray-700">{{ __("Before returns, incl. tax") }}</p>
						<p v-if="summary.credit_outstanding" class="mt-0.5 text-xs text-gray-700 tabular-nums">
							{{ __("Credit Sales (Outstanding)") }}: {{ formatMoney(summary.credit_outstanding) }}
						</p>
					</div>
					<p class="shrink-0 text-lg font-bold text-gray-900 tabular-nums xl:text-2xl">
						{{ formatMoney(summary.gross_sales) }}
					</p>
				</section>

				<div
					data-test="kpi-orders"
					class="flex items-baseline justify-between gap-3 px-4 py-2.5 xl:flex-col-reverse xl:items-start xl:gap-0 xl:py-3.5"
				>
					<div class="min-w-0">
						<p class="text-xs font-medium text-gray-700">{{ __("Orders") }}</p>
						<p class="mt-0.5 text-xs text-gray-700">{{ __("Sales invoices") }}</p>
					</div>
					<p class="shrink-0 text-lg font-bold text-gray-900 tabular-nums xl:text-2xl">
						{{ summary.sales_count || 0 }}
					</p>
				</div>

				<div
					data-test="kpi-average"
					class="flex items-baseline justify-between gap-3 px-4 py-2.5 xl:flex-col-reverse xl:items-start xl:gap-0 xl:py-3.5"
				>
					<div class="min-w-0">
						<p class="text-xs font-medium text-gray-700">{{ __("Average Sale") }}</p>
						<p class="mt-0.5 text-xs text-gray-700">{{ __("Per order") }}</p>
					</div>
					<p class="shrink-0 text-lg font-bold text-gray-900 tabular-nums xl:text-2xl">
						{{ formatMoney(summary.average_sale) }}
					</p>
				</div>

				<div
					v-if="hasReturns"
					data-test="kpi-returns"
					class="flex items-baseline justify-between gap-3 bg-red-50 px-4 py-2.5 xl:flex-col-reverse xl:items-start xl:gap-0 xl:py-3.5"
				>
					<div class="min-w-0">
						<p class="text-xs font-medium text-red-700">{{ __("Returns") }}</p>
						<p class="mt-0.5 text-xs text-red-700 tabular-nums">
							{{ __("{0} return invoices", [summary.returns_count]) }}
						</p>
					</div>
					<p class="shrink-0 text-lg font-bold text-red-600 tabular-nums xl:text-2xl">
						{{ returnsDisplay }}
					</p>
				</div>
			</div>

			<!-- Trend: the Dashboard's chart, now owned by the recap. Bars sit
			     against a dashed scale; returns hang below the zero baseline. -->
			<section
				v-if="hourlyBuckets.length"
				class="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm"
				aria-labelledby="ss-trend-h"
				data-test="trend-section"
			>
				<h3 id="ss-trend-h" class="text-base font-semibold text-gray-900">
					{{ __(trendHeading) }}
				</h3>
				<p v-if="bucketGranularity === 'hour'" class="text-xs text-gray-500">
					{{ __("Returns are netted into their hour.") }}
				</p>
				<div class="mt-4 flex flex-col" data-test="hourly-chart">
					<div class="flex gap-2">
						<div
							class="flex w-14 flex-col justify-between whitespace-nowrap text-end text-xs text-gray-600 tabular-nums"
							aria-hidden="true"
							data-test="hourly-y-axis"
						>
							<span v-for="label in chartScale.labels" :key="label">{{ label }}</span>
						</div>
						<div class="relative flex min-w-0 flex-1 flex-col">
							<div class="pointer-events-none absolute inset-0" aria-hidden="true">
								<div
									v-for="pos in [0, 25, 50, 75]"
									:key="pos"
									class="absolute inset-x-0 border-t border-dashed border-gray-200"
									:style="{ top: `${pos}%` }"
								></div>
							</div>
							<div class="relative flex h-40 items-stretch gap-1 sm:h-48 sm:gap-2">
								<div
									v-for="b in hourlyBuckets"
									:key="b.start"
									class="flex min-w-0 flex-1 items-end justify-center"
									data-test="hour-column"
								>
									<div
										v-if="b.net_sales > 0"
										data-test="hour-bar"
										class="w-full max-w-6 rounded-t"
										:class="b.is_current ? 'bg-blue-300' : 'bg-blue-500'"
										:style="{ height: b.posHeight }"
										:title="barTitle(b)"
									></div>
								</div>
							</div>
						</div>
					</div>
					<!-- zero baseline -->
					<div class="ms-16 border-t border-gray-300"></div>
					<!-- returns hang below the baseline -->
					<div class="ms-16 flex h-8 items-stretch gap-1 sm:gap-2" aria-hidden="true">
						<div
							v-for="b in hourlyBuckets"
							:key="`neg-${b.start}`"
							class="flex min-w-0 flex-1 items-start justify-center"
						>
							<div
								v-if="b.net_sales < 0"
								data-test="hour-bar"
								class="w-full max-w-6 rounded-b bg-red-400"
								:style="{ height: b.negHeight }"
								:title="barTitle(b)"
							></div>
						</div>
					</div>
					<div class="ms-16 flex gap-1 sm:gap-2">
						<div
							v-for="b in hourlyBuckets"
							:key="`label-${b.start}`"
							class="mt-1 min-w-0 flex-1 text-center text-xs text-gray-600 tabular-nums"
						>
							<span v-if="b.showLabel" class="hidden sm:inline">{{ b.label }}</span>
							<span v-if="b.showLabelMobile" class="sm:hidden">{{ b.label }}</span>
						</div>
					</div>
				</div>
			</section>

			<!-- Independent columns keep short report sections content-sized. -->
			<div class="grid items-start gap-4 xl:grid-cols-2" data-test="recap-sheet">
				<div class="flex min-w-0 flex-col gap-4">
				<div class="min-w-0 divide-y divide-gray-200 rounded-2xl border border-gray-200 bg-white p-5 shadow-sm" data-test="recap-products">
					<section aria-labelledby="ss-items-h" data-test="items-details" class="min-w-0">
						<h3 id="ss-items-h" class="text-base font-semibold text-gray-900">{{ __("Item Detail") }}</h3>
						<div v-if="summary.items.length" class="mt-2 overflow-x-auto max-h-64 overflow-y-auto" data-test="items-section">
							<table class="w-full text-sm" data-test="items-table">
								<thead class="sticky top-0 bg-white">
									<tr class="bg-gray-50 text-xs font-medium text-gray-500 border-b border-gray-200">
										<th class="py-1.5 text-start font-medium">{{ __("Item") }}</th>
										<th class="px-2 py-1.5 text-end font-medium">{{ __("Qty") }}</th>
										<th class="px-2 py-1.5 text-end font-medium">{{ __("Price") }}</th>
										<th v-if="hasItemDiscounts" class="px-2 py-1.5 text-end font-medium">{{ __("Discount") }}</th>
										<th class="px-2 py-1.5 text-end font-medium">{{ __("Subtotal") }}</th>
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
										<td class="px-2 py-1.5 text-end text-gray-700 tabular-nums whitespace-nowrap">{{ formatQty(item.qty) }}</td>
										<td class="px-2 py-1.5 text-end text-gray-700 tabular-nums whitespace-nowrap">
											{{ formatMoney(item.price_list_rate || 0) }}
										</td>
										<td v-if="hasItemDiscounts" class="px-2 py-1.5 text-end text-gray-700 tabular-nums whitespace-nowrap">
											{{ formatMoney(item.discount_amount || 0) }}
										</td>
										<td class="px-2 py-1.5 text-end font-medium text-gray-900 tabular-nums whitespace-nowrap">
											{{ formatMoney(item.base_net_amount) }}
										</td>
									</tr>
								</tbody>
							</table>
						</div>
						<p v-else class="mt-2 text-xs text-gray-500">{{ emptyText }}</p>
						<p v-if="summary.items_truncated" class="mt-2 text-xs text-gray-500">
							{{
							__(
							"Showing top {0} of {1} items; the totals above cover the whole session.",
							[summary.items_shown, summary.items_total_groups],
							)
							}}
						</p>
						<p v-if="summary.items.length" class="mt-2 text-xs text-gray-500">
							{{ __("Subtotal is net of item and invoice discounts, excluding tax.") }}
						</p>
					</section>
					<div class="mt-4 pt-4">
						<section aria-labelledby="ss-pkg-h" data-test="packages-section" class="min-w-0">
							<h3 id="ss-pkg-h" class="text-base font-semibold text-gray-900">{{ __("Package Detail") }}</h3>
							<div v-if="summary.packages.length" class="overflow-x-auto">
								<table class="mt-2 w-full text-sm" data-test="packages-table">
									<thead>
										<tr class="bg-gray-50 text-xs font-medium text-gray-500 border-b border-gray-200">
											<th class="py-1.5 text-start font-medium">{{ __("Package") }}</th>
											<th class="px-2 py-1.5 text-end font-medium">{{ __("Qty") }}</th>
											<th class="px-2 py-1.5 text-end font-medium">{{ __("Price") }}</th>
											<th v-if="hasItemDiscounts" class="px-2 py-1.5 text-end font-medium">{{ __("Discount") }}</th>
											<th class="px-2 py-1.5 text-end font-medium">{{ __("Subtotal") }}</th>
										</tr>
									</thead>
									<tbody>
										<tr v-for="pkg in summary.packages" :key="pkg.item_code" class="border-t border-gray-100">
											<td class="py-1.5 text-gray-900 min-w-0 break-words">
												{{ pkg.item_name }}
												<span class="block text-xs text-gray-500">{{ pkg.item_code }}</span>
											</td>
											<td class="px-2 py-1.5 text-end text-gray-700 tabular-nums whitespace-nowrap">{{ formatQty(pkg.qty) }}</td>
											<td class="px-2 py-1.5 text-end text-gray-700 tabular-nums whitespace-nowrap">
												{{ formatMoney(pkg.price_list_rate || 0) }}
											</td>
											<td v-if="hasItemDiscounts" class="px-2 py-1.5 text-end text-gray-700 tabular-nums whitespace-nowrap">
												{{ formatMoney(pkg.discount_amount || 0) }}
											</td>
											<td class="px-2 py-1.5 text-end font-medium text-gray-900 tabular-nums whitespace-nowrap">
												{{ formatMoney(pkg.base_net_amount) }}
											</td>
										</tr>
									</tbody>
								</table>
							</div>
							<p v-else class="mt-2 text-xs text-gray-500">{{ emptyText }}</p>
							<p v-if="summary.packages.length" class="mt-2 text-xs text-gray-500">
								{{ __("Subtotal is net of item and invoice discounts, excluding tax.") }}
							</p>
						</section>
					</div>
				</div>
				<div class="min-w-0 divide-y divide-gray-200 rounded-2xl border border-gray-200 bg-white p-5 shadow-sm" data-test="recap-breakdown">
					<section aria-labelledby="ss-categories-h" data-test="categories-section" class="min-w-0">
						<h3 id="ss-categories-h" class="text-base font-semibold text-gray-900">{{ __("Sales per Category") }}</h3>
						<table class="mt-2 w-full table-fixed text-sm" data-test="categories-table">
							<thead>
								<tr class="bg-gray-50 text-xs font-medium text-gray-600 border-b border-gray-200">
									<th class="py-1.5 text-start font-medium">{{ __("Category") }}</th>
									<th class="px-2 py-1.5 text-end font-medium">{{ __("Qty") }}</th>
									<th class="px-2 py-1.5 text-end font-medium">{{ __("Net Revenue") }}</th>
								</tr>
							</thead>
							<tbody>
								<tr v-if="!summary.categories.length">
									<td colspan="3" class="py-3 text-center text-xs text-gray-500">
										{{ emptyText }}
									</td>
								</tr>
								<tr v-for="cat in summary.categories" :key="cat.category ?? 'none'" class="border-t border-gray-100">
									<td class="py-1.5 text-gray-900 break-words [overflow-wrap:anywhere]">{{ cat.category || __("No category") }}</td>
									<td class="px-2 py-1.5 text-end text-gray-700 tabular-nums whitespace-nowrap">{{ formatQty(cat.qty) }}</td>
									<td class="px-2 py-1.5 text-end font-medium text-gray-900 tabular-nums whitespace-nowrap">
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
					<section aria-labelledby="ss-charges-h" data-test="charges-section" class="min-w-0 pt-4">
						<h3 id="ss-charges-h" class="text-base font-semibold text-gray-900">{{ __("Charges & Discounts") }}</h3>
						<dl class="mt-2 flex flex-col">
							<div
								v-for="charge in summary.other_charges"
								:key="charge.account_head"
								class="flex items-baseline justify-between gap-3 py-2 border-b border-gray-100"
								>
								<dt class="text-sm text-gray-600 min-w-0 break-words">
									{{ charge.label }}
									<span class="text-xs text-gray-500">{{ __("posted as its own account") }}</span>
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
					<section aria-labelledby="ss-refund-h" data-test="refund-section" class="min-w-0 pt-4">
						<h3 id="ss-refund-h" class="text-base font-semibold text-gray-900">{{ __("Refunds") }}</h3>
						<div class="mt-2 flex flex-wrap items-start justify-between gap-3">
							<div>
								<p class="text-sm text-gray-600">{{ __("Total Refunds") }}</p>
								<p
									class="mt-1 text-lg font-bold tabular-nums whitespace-nowrap"
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
				</div>
				</div>

				<div class="flex min-w-0 flex-col gap-4" data-test="recap-finances">
					<div class="space-y-4 divide-y divide-gray-200 rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
						<section aria-labelledby="ss-cash-h" data-test="cash-summary" class="min-w-0">
							<h3 id="ss-cash-h" class="text-base font-semibold text-gray-900">{{ __("Cash Summary") }}</h3>
							<dl class="mt-2 flex flex-col">
								<div class="flex items-baseline justify-between gap-3 py-2 border-b border-gray-100">
									<dt class="text-sm text-gray-600">{{ __("Modal Awal") }}</dt>
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
								<!-- Neutral surface: the KPI strip above owns the only blue. -->
								<div class="mt-3 rounded-xl border border-gray-200 bg-gray-50 px-4 py-3">
									<div class="flex items-baseline justify-between gap-3">
										<dt class="text-sm font-semibold text-gray-900">{{ __("Cash in Hand") }}</dt>
										<dd class="text-base font-bold text-gray-900 tabular-nums">{{ formatMoney(summary.cash_in_hand) }}</dd>
									</div>
									<p class="mt-1 text-xs text-gray-600">{{ __("Cash in Hand is the balance before any expenses") }}</p>
								</div>
							</dl>
						</section>
						<div class="pt-4">
							<section aria-labelledby="ss-pay-h" data-test="payments-section" class="min-w-0">
								<h3 id="ss-pay-h" class="text-base font-semibold text-gray-900">{{ __("Payment Methods") }}</h3>
								<div v-if="paymentDisplay.length" class="mt-2">
									<div
										v-for="row in paymentDisplay"
										:key="row.mode"
										class="border-b border-gray-100 py-2 last:border-b-0"
										data-test="payment-row"
									>
										<div class="flex items-baseline justify-between gap-3">
											<span class="min-w-0 break-words text-sm text-gray-700">
												{{ row.mode }}
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
											<span
												class="shrink-0 whitespace-nowrap text-sm font-semibold tabular-nums"
												:class="row.amount ? 'text-gray-900' : 'text-gray-400'"
											>
												{{ formatMoney(row.amount) }}
												<span v-if="row.pct !== null" class="ms-1 text-xs font-normal text-gray-400" data-test="payment-pct">{{ row.pct }}%</span>
											</span>
										</div>
										<div class="mt-1.5 h-1.5 w-full rounded-full bg-gray-100" data-test="payment-bar">
											<div class="h-full rounded-full bg-blue-600" :style="{ width: row.width }"></div>
										</div>
									</div>
								</div>
								<p v-else class="mt-2 text-xs text-gray-500">{{ __("No payment methods are configured on this POS Profile.") }}</p>
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
								<div class="mt-2 flex items-baseline justify-between gap-3 border-t border-gray-200 pt-3">
									<p class="text-sm font-semibold text-gray-900">{{ __("Grand Total") }}</p>
									<p class="font-bold text-gray-900 tabular-nums whitespace-nowrap">{{ formatMoney(summary.methods_grand_total) }}</p>
								</div>
							</section>
						</div>


					</div>

				</div>
			</div>
		</template>
	</div>
</template>

<script>
const compactFormatter = new Intl.NumberFormat("en", {
	notation: "compact",
	maximumFractionDigits: 1,
})
const qtyFormatter = new Intl.NumberFormat(undefined, {
	maximumFractionDigits: 3,
})
</script>

<script setup>
import RefreshButton from "@/components/common/RefreshButton.vue"
import { useFormatters } from "@/composables/useFormatters"
import { useToast } from "@/composables/useToast"
import BottomSheet from "@/components/common/BottomSheet.vue"
import {
	DEFAULT_CURRENCY,
	formatCurrency as formatCurrencyUtil,
	getCurrencySymbol,
} from "@/utils/currency"
import {
	periodRange,
	printSalesRecap,
	buildRecapHTML,
	downloadRecapPDF,
	recapPDFFilename,
} from "@/utils/salesRecap"
import { downloadXlsx } from "@/utils/downloadXlsx"
import { Button, createResource, DateRangePicker, FeatherIcon } from "frappe-ui"
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
// DateRangePicker emits the pair as "from,to"; keep the two refs as the single
// source of truth so range -> makeParams and the canLoad gate stay unchanged.
const customRange = computed({
	// Keep a half-picked range so closing and reopening the calendar resumes it.
	get: () =>
		customFrom.value || customTo.value ? `${customFrom.value},${customTo.value}` : "",
	set: (value) => {
		const [from = "", to = ""] = String(value || "").split(",")
		customFrom.value = from
		customTo.value = to
	},
})
const customRangeLabel = computed(() =>
	customFrom.value && customTo.value
		? `${formatDate(customFrom.value)} – ${formatDate(customTo.value)}`
		: __("Select dates"),
)
const isShiftMode = computed(() => period.value === "shift")
// Mobile period sheet (filter pill)
const sheetOpen = ref(false)
const sheetPeriodLabel = computed(() => {
	if (period.value === "custom" && range.value) return customRangeLabel.value
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
	...(props.openingShift ? [{ value: "shift", label: __("This Shift") }] : []),
	{ value: "today", label: __("Today") },
	{ value: "yesterday", label: __("Yesterday") },
	{ value: "last7", label: __("Last 7 Days") },
	{ value: "month", label: __("This Month") },
	{ value: "year", label: __("This Year") },
	{ value: "custom", label: __("Custom Range") },
])

const stale = ref(false)
const printing = ref(false)
const exporting = ref(false)
const printFailed = ref(false)
const exportingPdf = ref(false)

async function exportRecapPDF() {
	if (!summary.value || exportingPdf.value) return
	exportingPdf.value = true
	try {
		const html = buildRecapHTML(summary.value)
		await downloadRecapPDF(html, recapPDFFilename(summary.value))
	} catch (error) {
		showError(error?.message || __("Could not create the sales recap PDF"))
	} finally {
		exportingPdf.value = false
	}
}

async function exportExcel() {
	if (exporting.value) return
	exporting.value = true
	try {
		await downloadXlsx(
			"pos_next.api.shifts.export_sales_recap",
			isShiftMode.value
				? { opening_shift: props.openingShift }
				: {
						pos_profile: props.posProfile,
						from_date: range.value?.from,
						to_date: range.value?.to,
					},
		)
	} catch (error) {
		showError(error?.message || __("Export failed"))
	} finally {
		exporting.value = false
	}
}

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
		if (via === "failed") {
			// no automatic fallback: offer the retry panel instead
			printFailed.value = true
			showError(__("Cetak gagal. Gunakan Print Ulang atau Export PDF."))
		} else {
			printFailed.value = false
			showSuccess(__("Sales recap sent to the printer"))
		}
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

// Optional cells join the strip only when they carry information; the
// column count follows so no cell stretches across a half-empty row.
const hasGross = computed(() => Boolean(summary.value?.gross_sales || summary.value?.credit_outstanding))
const hasReturns = computed(() => Boolean(summary.value?.returns_count || summary.value?.returns_total))
const kpiColumnsClass = computed(() => {
	const columns = 1 + (hasGross.value ? 1 : 0) + 2 + (hasReturns.value ? 1 : 0)
	// literal class names so Tailwind keeps them in the build
	if (columns >= 5) return "xl:grid-cols-5"
	if (columns === 4) return "xl:grid-cols-4"
	return "xl:grid-cols-3"
})

// Payment rows mirror the Dashboard: amount plus its share of the positive
// total, drawn as a progress bar. Zero and negative rows carry no bar.
const paymentDisplay = computed(() => {
	const rows = summary.value?.payments || []
	const positiveTotal = rows.reduce(
		(sum, row) => sum + Math.max(0, Number.parseFloat(row.amount) || 0),
		0,
	)
	return rows.map((row) => {
		const amount = Number.parseFloat(row.amount) || 0
		const pct = amount > 0 && positiveTotal > 0 ? Math.round((amount / positiveTotal) * 100) : null
		const width = pct == null ? "0" : `${Math.max(0, Math.min(100, pct))}%`
		return {
			mode: row.mode_of_payment,
			amount: row.amount,
			configured: row.configured,
			pct,
			width,
		}
	})
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

const formatQty = (qty) => qtyFormatter.format(Number.parseFloat(qty || 0))

// Bucket type follows the window, same rule as the backend: hours on a single
// day, days up to 62, calendar months beyond.
const bucketGranularity = computed(() => {
	const s = summary.value
	if (!s?.hourly?.length) return "hour"
	const span =
		isShiftMode.value || !s.period_from
			? 0
			: Math.round((new Date(s.period_to) - new Date(s.period_from)) / 86400000)
	return span === 0 ? "hour" : span <= 62 ? "day" : "month"
})

const trendHeading = computed(() => {
	if (bucketGranularity.value === "day") return "Sales by Day"
	if (bucketGranularity.value === "month") return "Sales by Month"
	return "Sales by Hour"
})

function bucketLabel(start) {
	const s = String(start || "")
	if (bucketGranularity.value === "day") return `${s.slice(8, 10)}/${s.slice(5, 7)}`
	if (bucketGranularity.value === "month") return `${s.slice(5, 7)}/${s.slice(2, 4)}`
	return `${s.slice(11, 13)}:00`
}

const currencySymbol = computed(() =>
	getCurrencySymbol(summary.value?.company_currency || DEFAULT_CURRENCY),
)

function formatCompact(value) {
	return `${currencySymbol.value} ${compactFormatter.format(value)}`
}

// Y-axis scale: four steps up to a "nice" maximum (1/2/2.5/5 × 10^n) so the
// dashed gridlines land on round figures.
const chartScale = computed(() => {
	const values = (summary.value?.hourly || []).map(
		(b) => Number.parseFloat(b.net_sales) || 0,
	)
	const max = Math.max(0, ...values)
	if (max <= 0) return { niceMax: 0, labels: ["0"] }
	const raw = max / 4
	const mag = 10 ** Math.floor(Math.log10(raw))
	const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((n) => n >= raw) || 10 * mag
	return {
		niceMax: step * 4,
		labels: [4, 3, 2, 1, 0].map((i) => (i === 0 ? "0" : formatCompact(step * i))),
	}
})

const hourlyBuckets = computed(() => {
	const buckets = summary.value?.hourly || []
	const values = buckets.map((b) => Number.parseFloat(b.net_sales) || 0)
	const maxPos = Math.max(0, ...values)
	const maxNeg = Math.max(0, ...values.map((v) => -v))
	const niceMax = chartScale.value.niceMax || maxPos
	// Thin the x labels on wide windows so they never collide. Phones get a
	// sparser set: an hour label needs ~2 columns at 390px, so every other
	// label still collides there even when the desktop set fits.
	const thin = buckets.length > 12
	return buckets.map((b, i) => {
		const value = values[i]
		return {
			...b,
			label: bucketLabel(b.start),
			showLabel: !thin || i % 2 === 0,
			showLabelMobile: thin ? i % 4 === 0 : i % 2 === 0,
			posHeight:
				value > 0 && niceMax > 0
					? `${Math.max(3, Math.round((value / niceMax) * 100))}%`
					: "0",
			negHeight:
				value < 0 && maxNeg > 0
					? `${Math.max(4, Math.round((-value / maxNeg) * 100))}%`
					: "0",
		}
	})
})

function barTitle(bucket) {
	const count = Number.parseFloat(bucket.sales_count) || 0
	return `${bucket.label} · ${formatMoney(bucket.net_sales)} · ${count}`
}

function formatDateTime(date) {
	const dateStr = formatDate(date)
	const timeStr = formatTime(date)
	return [dateStr, timeStr].filter(Boolean).join(" ")
}
</script>
