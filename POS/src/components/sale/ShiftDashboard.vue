<template>
	<div class="flex flex-col gap-4">
		<!-- Menu gating hides this view offline; this is only a fallback -->
		<div v-if="!openingShift && !posProfile" class="text-center py-8">
			<p class="text-sm text-gray-500">{{ __("No open shift for this session.") }}</p>
		</div>

		<template v-else>
			<!-- Mode selector: shift lens or a posting-date window over the whole
			     profile, for every cashier on it. Mobile folds the chips into one
			     pill that opens a period sheet; desktop keeps the touch-friendly
			     chip row. -->
			<div class="flex items-center justify-between gap-2 md:hidden">
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
				<RefreshButton :loading="loading" @click="refresh" />
			</div>
			<div
				class="hidden flex-wrap items-center gap-3 md:flex"
				data-test="mode-chips"
			>
				<div class="inline-flex rounded-xl bg-gray-100 p-1" role="group" :aria-label="__('Period')">
					<button
						v-for="preset in presets"
						:key="preset.value"
						type="button"
						class="rounded-lg px-3.5 py-1.5 text-sm font-medium transition-colors"
						:class="
							mode === preset.value
								? 'bg-white text-blue-700 shadow-sm'
								: 'text-gray-600 hover:text-gray-900'
						"
						:data-test="`period-chip-${preset.value}`"
						:aria-pressed="mode === preset.value"
						@click="mode = preset.value"
					>
						{{ preset.label }}
					</button>
				</div>
				<div
					v-if="mode === 'custom'"
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
						v-for="preset in presets"
						:key="preset.value"
						type="button"
						class="flex w-full items-center justify-between rounded-xl px-3 py-2.5 text-sm"
						:class="
							mode === preset.value
								? 'bg-blue-50 font-semibold text-blue-700'
								: 'text-gray-700'
						"
						:data-test="`period-option-${preset.value}`"
						@click="selectPeriod(preset.value)"
					>
						{{ preset.label }}
						<FeatherIcon
							v-if="mode === preset.value"
							name="check"
							class="h-4 w-4"
							:aria-hidden="true"
						/>
					</button>
					<div
						v-if="mode === 'custom'"
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
						<Button variant="solid" class="w-full" @click="close">
							{{ __("Apply") }}
						</Button>
					</div>
				</template>
			</BottomSheet>

			<!-- First-load skeleton: one full panel, no per-widget spinners -->
			<div
				v-if="loading && !dashboard"
				class="flex flex-col gap-4"
				data-test="dashboard-skeleton"
			>
				<div class="h-24 rounded-lg bg-gray-100 animate-pulse"></div>
				<div class="h-44 rounded-lg bg-gray-100 animate-pulse"></div>
				<div class="h-36 rounded-lg bg-gray-100 animate-pulse"></div>
				<div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
					<div class="h-48 rounded-lg bg-gray-100 animate-pulse"></div>
					<div class="h-48 rounded-lg bg-gray-100 animate-pulse"></div>
				</div>
			</div>

			<!-- Error without cached data -->
			<div
				v-else-if="error && !dashboard"
				class="text-center py-8"
				data-test="error-state"
			>
				<p class="text-sm text-gray-500">{{ __("Could not load the dashboard.") }}</p>
				<Button variant="subtle" class="mt-3" @click="refresh">
					{{ __("Retry") }}
				</Button>
			</div>

			<!-- Custom range not complete yet -->
			<p
				v-else-if="!canLoad"
				class="text-center py-8 text-sm text-gray-500"
				role="status"
				data-test="pick-dates-notice"
			>
				{{ __("Pick a from and to date to load the recap.") }}
			</p>

			<template v-else-if="dashboard">
				<!-- Refresh failed but a snapshot exists: flag it, keep the numbers -->
				<div
					v-if="error"
					class="flex items-center justify-between gap-3 rounded-lg bg-amber-50 border border-amber-300 p-2.5 text-xs text-amber-800"
					role="status"
					data-test="error-alert"
				>
					<span>{{ __("Could not load the dashboard.") }}</span>
					<Button variant="subtle" @click="refresh">{{ __("Retry") }}</Button>
				</div>

				<!-- Toolbar band: shift context as one quiet text line + refresh -->
				<div class="flex flex-wrap items-center justify-between gap-3">
					<p
						class="min-w-0 flex-1 truncate text-xs text-gray-500 tabular-nums md:whitespace-normal"
						data-test="shift-chips"
					>
						<template v-if="isShiftMode">
							<span data-test="chip-shift">{{ dashboard.opening_shift }}</span>
							<template v-if="dashboard.period_start_date">
								· {{ __("Opened") }} {{ formatDateTime(dashboard.period_start_date) }}
							</template>
							<template v-if="shiftStore.shiftDuration">
								· <span data-test="chip-duration">{{ shiftStore.shiftDuration }}</span>
							</template>
							<template v-if="dashboard.cashier">
								· {{ __("Cashier") }}: {{ dashboard.cashier }}
							</template>
						</template>
						<template v-else>
							<span data-test="chip-period">{{ presetLabel }}</span>
							<template v-if="dashboard.period">
								· <span data-test="chip-period-range">
									{{ formatDate(dashboard.period.from_date) }} – {{ formatDate(dashboard.period.to_date) }}
								</span>
							</template>
						</template>
						<template v-if="dashboard.pos_profile"> · {{ dashboard.pos_profile }}</template>
					</p>
					<div class="flex shrink-0 items-center gap-2">
						<span
							v-if="minutesAgo !== null"
							class="text-xs tabular-nums"
							:class="minutesAgo > 10 ? 'text-amber-600' : 'text-gray-500'"
							data-test="updated-label"
						>
							{{ __("Updated {0} min ago", [minutesAgo]) }}
						</span>
						<RefreshButton :loading="loading" @click="refresh" />
					</div>
				</div>

				<p
					v-if="isEmpty"
					class="text-center text-xs text-gray-500"
					role="status"
					data-test="empty-notice"
				>
					{{ emptyText }}
				</p>

				<!-- KPI cards: Net Sales is the hero; the rest are quiet companions.
				     Cash reads once, in Payment Methods — not here. -->
				<div class="grid grid-cols-2 gap-3 lg:grid-cols-4" data-test="kpi-grid">
					<div
						class="relative col-span-2 overflow-hidden rounded-2xl bg-blue-600 p-5 text-white shadow-sm lg:col-span-1"
						data-test="kpi-net-sales"
					>
						<div
							class="pointer-events-none absolute -bottom-12 -right-12 h-36 w-36 rounded-full border-[14px] border-white/10"
							aria-hidden="true"
						></div>
						<div class="relative flex items-start justify-between gap-3">
							<div class="text-xs font-medium text-blue-100">{{ __("Net Sales") }}</div>
							<div class="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-white/15">
								<span class="text-sm font-bold" aria-hidden="true">Rp</span>
							</div>
						</div>
						<div class="relative mt-1 text-2xl font-bold tabular-nums sm:text-4xl">
							{{ formatMoney(dashboard.net_sales) }}
						</div>
						<div class="relative mt-1 text-xs text-blue-100 tabular-nums" data-test="kpi-strip">
							<span data-test="kpi-discounts">
								{{ __("Discount {0}", [formatMoney(dashboard.total_discount)]) }}</span
							>
						</div>
					</div>
					<div
						v-for="stat in kpiStats"
						:key="stat.key"
						class="rounded-2xl border border-gray-200 bg-white p-4 shadow-sm"
						:data-test="stat.test"
					>
						<div class="flex items-start justify-between gap-3">
							<div class="text-xs text-gray-500">{{ stat.label }}</div>
							<div class="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-blue-50">
								<FeatherIcon :name="stat.icon" class="h-5 w-5 text-blue-600" :aria-hidden="true" />
							</div>
						</div>
						<div class="mt-1 text-2xl font-bold text-gray-900 tabular-nums sm:text-3xl">{{ stat.value }}</div>
						<div class="mt-1 text-xs text-gray-400">{{ stat.caption }}</div>
					</div>
					<div
						v-if="(dashboard.returns_count || 0) > 0"
						class="rounded-2xl border border-red-200 bg-red-50 p-4 shadow-sm"
						data-test="kpi-returns"
					>
						<div class="flex items-start justify-between gap-3">
							<div class="text-xs text-red-500">{{ __("Returns") }}</div>
							<div class="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-red-100">
								<FeatherIcon name="rotate-ccw" class="h-5 w-5 text-red-600" :aria-hidden="true" />
							</div>
						</div>
						<div class="mt-1 text-2xl font-bold text-red-600 tabular-nums sm:text-3xl">
							{{ returnsTotalLabel }}
						</div>
						<div class="mt-1 text-xs text-red-500 tabular-nums">
							{{ returnsCountLabel }}
						</div>
					</div>
				</div>

				<!-- Middle row: trend chart spans 2 of 3 columns -->
				<div class="grid grid-cols-1 gap-4 lg:grid-cols-3">
					<!-- Sales by hour: CSS bars against a dashed gridline scale,
					     returns hang below the baseline -->
					<section
						class="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm lg:col-span-2"
						aria-labelledby="sd-hourly-h"
						data-test="hourly-section"
					>
						<h3 id="sd-hourly-h" class="text-base font-semibold text-gray-900">
							{{ __(trendHeading) }}
						</h3>
						<p v-if="bucketGranularity === 'hour'" class="text-xs text-gray-500">
							{{ __("Returns are netted into their hour.") }}
						</p>
						<div v-if="hourlyBuckets.length" class="mt-4" data-test="hourly-chart">
							<div class="flex gap-2">
								<div
									class="flex w-12 flex-col justify-between text-end text-[10px] text-gray-400 tabular-nums"
									aria-hidden="true"
									data-test="hourly-y-axis"
								>
									<span v-for="label in chartScale.labels" :key="label">{{ label }}</span>
								</div>
								<div class="relative min-w-0 flex-1">
									<div class="pointer-events-none absolute inset-0" aria-hidden="true">
										<div
											v-for="pos in [0, 25, 50, 75]"
											:key="pos"
											class="absolute inset-x-0 border-t border-dashed border-gray-200"
											:style="{ top: `${pos}%` }"
										></div>
									</div>
									<div class="relative flex h-28 items-end gap-1 sm:gap-2">
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
							<div class="ms-14 border-t border-gray-300"></div>
							<!-- returns hang below the baseline -->
							<div class="ms-14 flex h-8 items-start gap-1 sm:gap-2" aria-hidden="true">
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
							<div class="ms-14 flex gap-1 sm:gap-2">
								<div
									v-for="b in hourlyBuckets"
									:key="`label-${b.start}`"
									class="mt-1 min-w-0 flex-1 text-center text-[10px] text-gray-400 tabular-nums"
								>
									<span v-if="b.showLabel">{{ b.label }}</span>
								</div>
							</div>
						</div>
						<p v-else class="mt-2 text-xs text-gray-500">{{ emptyText }}</p>
					</section>

					<!-- Payment methods -->
					<section
						class="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm"
						aria-labelledby="sd-pay-h"
						data-test="payments-section"
					>
						<h3 id="sd-pay-h" class="text-base font-semibold text-gray-900">
							{{ __("Payment Methods") }}
						</h3>
						<p class="text-xs text-gray-500">{{ __("Payment distribution this session") }}</p>
						<div v-if="paymentRows.length" class="mt-4" data-test="payments-list">
							<div
								v-for="row in paymentRows"
								:key="row.mode_of_payment"
								class="border-b border-gray-100 py-2 last:border-b-0"
								data-test="payment-row"
							>
								<div class="flex items-baseline justify-between gap-3">
									<span class="min-w-0 text-sm text-gray-700">
										{{ row.mode_of_payment }}
									</span>
									<span
										class="shrink-0 whitespace-nowrap text-sm font-semibold tabular-nums"
										:class="(Number.parseFloat(row.amount) || 0) < 0 ? 'text-red-600' : 'text-gray-900'"
									>
										{{ formatMoney(row.amount) }}
										<span
											v-if="paymentPct(row.amount) !== null"
											class="ms-1 text-xs font-normal text-gray-400"
											data-test="payment-pct"
										>{{ paymentPct(row.amount) }}%</span>
									</span>
								</div>
								<div class="mt-1.5 h-1.5 w-full rounded-full bg-gray-100" data-test="payment-bar">
									<div
										class="h-full rounded-full bg-blue-600"
										:style="{ width: paymentBarWidth(row.amount) }"
									></div>
								</div>
							</div>
							<div class="mt-2 flex items-baseline justify-between gap-3 border-t border-gray-200 pt-3">
								<span class="text-sm font-semibold text-gray-900">{{ __("Total Payments") }}</span>
								<span class="font-bold text-gray-900 tabular-nums">{{ formatMoney(totalPayments) }}</span>
							</div>
						</div>
						<p v-else class="mt-2 text-xs text-gray-500">
							{{ __("No payment methods are configured on this POS Profile.") }}
						</p>
						<!-- Kas di laci dibaca sekali di sini — milik seksi, bukan baris -->
						<p
							v-if="isShiftMode && dashboard.cash_expected != null"
							class="mt-2 text-xs text-gray-500"
							data-test="drawer-note"
						>
							{{ __("in drawer") }} {{ formatMoney(dashboard.cash_expected) }}
							<span v-if="!dashboard.expense_supported">
								· {{ __("Before expenses") }}</span
							>
						</p>
					</section>
				</div>

				<!-- Details: top items + recent transactions -->
				<div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
					<section
						class="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm"
						aria-labelledby="sd-items-h"
						data-test="top-items-section"
					>
						<div class="flex items-center justify-between gap-3">
							<h3 id="sd-items-h" class="text-base font-semibold text-gray-900">
								{{ __("Top Items") }}
							</h3>
							<button
								type="button"
								class="text-sm font-medium text-blue-600 hover:text-blue-700"
								data-test="view-all-items"
								@click="$emit('navigate', 'sales-recap')"
							>
								{{ __("View all →") }}
							</button>
						</div>
						<table v-if="topItems.length" class="mt-3 w-full text-sm" data-test="top-items-list">
							<thead>
								<tr class="text-xs uppercase tracking-widest text-gray-500">
									<th class="w-8 py-1.5 text-start font-medium">#</th>
									<th class="py-1.5 text-start font-medium">{{ __("Item Name") }}</th>
									<th class="py-1.5 text-end font-medium">{{ __("Qty") }}</th>
									<th class="py-1.5 text-end font-medium">{{ __("Revenue") }}</th>
								</tr>
							</thead>
							<tbody>
								<tr
									v-for="(item, i) in topItems"
									:key="item.item_code"
									class="border-t border-gray-100"
									data-test="top-item"
								>
									<td class="py-2 text-xs text-gray-400 tabular-nums">{{ i + 1 }}</td>
									<td class="py-2">
										<div class="flex items-center gap-2.5">
											<div
												class="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl"
												:class="ITEM_TILES[i % ITEM_TILES.length]"
											>
												<FeatherIcon name="shopping-bag" class="h-4 w-4" :aria-hidden="true" />
											</div>
											<span class="min-w-0 truncate text-gray-900">{{ item.item_name }}</span>
										</div>
									</td>
									<td class="py-2 text-end text-gray-500 tabular-nums">{{ formatQty(item.qty) }}</td>
									<td class="py-2 text-end font-bold text-gray-900 tabular-nums">
										{{ formatMoney(item.base_net_amount) }}
									</td>
								</tr>
							</tbody>
						</table>
						<p v-else class="mt-3 text-xs text-gray-500">{{ emptyText }}</p>
					</section>

					<section
						class="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm"
						aria-labelledby="sd-recent-h"
						data-test="recent-section"
					>
						<div class="flex items-center justify-between gap-3">
							<h3 id="sd-recent-h" class="text-base font-semibold text-gray-900">
								{{ __("Recent Transactions") }}
							</h3>
							<button
								type="button"
								class="text-sm font-medium text-blue-600 hover:text-blue-700"
								data-test="view-all-recent"
								@click="$emit('navigate', 'invoices')"
							>
								{{ __("View all →") }}
							</button>
						</div>
						<table v-if="recentRows.length" class="mt-3 w-full text-sm" data-test="recent-list">
							<thead>
								<tr class="text-xs uppercase tracking-widest text-gray-500">
									<th class="py-1.5 text-start font-medium">{{ __("Time") }}</th>
									<th class="py-1.5 text-start font-medium">{{ __("Invoice No.") }}</th>
									<th class="py-1.5 text-start font-medium">{{ __("Method") }}</th>
									<th class="py-1.5 text-end font-medium">{{ __("Total") }}</th>
								</tr>
							</thead>
							<tbody>
								<tr
									v-for="row in recentRows"
									:key="row.name"
									class="cursor-pointer border-t border-gray-100 hover:bg-gray-50"
									data-test="recent-row"
									@click="$emit('view-invoice', { name: row.name })"
								>
									<td class="py-2 text-xs text-gray-500 tabular-nums">
										{{ formatTime(row.posting_dt) }}
									</td>
									<td class="py-2 font-mono text-sm text-gray-900">
										{{ row.name }}
										<StatusBadge
											v-if="row.is_return"
											variant="orange"
											size="xs"
											:text="__('Return')"
											data-test="return-badge"
										/>
										<StatusBadge
											v-if="row.outstanding_amount > 0"
											variant="red"
											size="xs"
											:text="__('Credit')"
											data-test="credit-badge"
										/>
									</td>
									<td class="py-2 text-xs text-gray-500">
										<span v-if="row.payment_mode" class="flex items-center gap-1.5">
											<span v-if="isCashMode(row.payment_mode)" class="text-[10px] font-bold text-gray-400" aria-hidden="true">Rp</span>
											<FeatherIcon v-else name="credit-card" class="h-3.5 w-3.5 text-gray-400" :aria-hidden="true" />
											{{ row.payment_mode }}
										</span>
										<template v-else>—</template>
									</td>
									<td
										class="py-2 text-end font-bold tabular-nums"
										:class="row.amount < 0 ? 'text-red-600' : 'text-gray-900'"
										data-test="recent-amount"
									>
										{{ formatMoney(row.amount) }}
									</td>
								</tr>
							</tbody>
						</table>
						<p v-else class="mt-3 text-xs text-gray-500">{{ emptyText }}</p>
					</section>
				</div>
			</template>
		</template>
	</div>
</template>

<script setup>
import BottomSheet from "@/components/common/BottomSheet.vue"
import RefreshButton from "@/components/common/RefreshButton.vue"
import StatusBadge from "@/components/common/StatusBadge.vue"
import { useFormatters } from "@/composables/useFormatters"
import { usePOSShiftStore } from "@/stores/posShift"
import {
	DEFAULT_CURRENCY,
	formatCurrency as formatCurrencyUtil,
	getCurrencySymbol,
} from "@/utils/currency"
import { periodRange } from "@/utils/salesRecap"
import { Button, createResource, FeatherIcon } from "frappe-ui"
import { computed, ref, watch } from "vue"

const { formatDate, formatTime } = useFormatters()

// Read-only: live strings from the shell's 1s tick (duration chip + the
// clock that keeps the "updated N min ago" label aging).
const shiftStore = usePOSShiftStore()


const props = defineProps({
	embedded: { type: Boolean, default: false },
	openingShift: { type: String, default: "" },
	posProfile: { type: String, default: "" },
	currency: { type: String, default: "" },
})

const emit = defineEmits(["view-invoice", "navigate"])

// Two lenses on one dataset: the open shift (default) or a posting-date
// window over the whole profile, every cashier included. Presets resolve to
// explicit dates here; the server only ever sees from/to.
const presets = computed(() => [
	...(props.openingShift ? [{ value: "shift", label: __("This Shift") }] : []),
	{ value: "today", label: __("Today") },
	{ value: "yesterday", label: __("Yesterday") },
	{ value: "last7", label: __("Last 7 Days") },
	{ value: "month", label: __("This Month") },
	{ value: "year", label: __("This Year") },
	{ value: "custom", label: __("Custom Range") },
])

const mode = ref(props.openingShift ? "shift" : "today")
const customFrom = ref("")
const customTo = ref("")
const isShiftMode = computed(() => mode.value === "shift")
// Mobile period sheet (filter pill)
const sheetOpen = ref(false)
// Custom range is picked inline in the sheet; the pill shows it once active
const sheetPeriodLabel = computed(() => {
	if (mode.value === "custom" && range.value)
		return `${customFrom.value} – ${customTo.value}`
	return presetLabel.value
})
function selectPeriod(value) {
	mode.value = value
	if (value !== "custom") sheetOpen.value = false
}
const presetLabel = computed(
	() =>
		presets.value.find((preset) => preset.value === mode.value)?.label || "",
)
const range = computed(() =>
	isShiftMode.value
		? null
		: periodRange(mode.value, { from: customFrom.value, to: customTo.value }),
)

const shiftResource = createResource({
	url: "pos_next.api.shifts.get_shift_dashboard",
	makeParams() {
		return { opening_shift: props.openingShift }
	},
	auto: false,
})

const periodResource = createResource({
	url: "pos_next.api.shifts.get_period_dashboard",
	makeParams() {
		return {
			pos_profile: props.posProfile,
			from_date: range.value?.from,
			to_date: range.value?.to,
		}
	},
	auto: false,
})

const activeResource = computed(() =>
	isShiftMode.value ? shiftResource : periodResource,
)
const dashboard = computed(() => activeResource.value.data || null)
const loading = computed(() => activeResource.value.loading)
const error = computed(() => activeResource.value.error)
// A custom window without both dates has nothing to fetch yet
const canLoad = computed(() =>
	isShiftMode.value
		? Boolean(props.openingShift)
		: Boolean(props.posProfile && range.value),
)

// Captured on every successful load (via a watch, so it also works when the
// resource is filled outside .reload()); drives the "updated N min ago" label.
const lastFetchedAt = ref(0)
watch(
	() => activeResource.value.data,
	(data) => {
		if (data) lastFetchedAt.value = Date.now()
	},
)

const minutesAgo = computed(() => {
	if (!lastFetchedAt.value) return null
	// Date.now() alone is non-reactive, so reference the store's 1s clock
	// tick too — that is what makes this label re-evaluate (and the amber
	// >10 min state fire) without a timer of its own.
	void shiftStore.currentTime
	return Math.max(0, Math.floor((Date.now() - lastFetchedAt.value) / 60000))
})

// Manual refresh only — the dashboard never polls on its own. Switching mode,
// preset or custom dates refetches through the same watch.
watch(
	[() => props.openingShift, mode, range],
	([shift]) => {
		if (!shift && isShiftMode.value) {
			// the shift closed underneath us: fall back to the profile's day
			mode.value = "today"
			return
		}
		if (canLoad.value) activeResource.value.reload()
	},
	{ immediate: true },
)

function refresh() {
	if (canLoad.value) activeResource.value.reload()
}

const emptyText = computed(() =>
	isShiftMode.value
		? __(
				"No sales yet in this shift. Sales appear here after the first invoice.",
			)
		: __("No sales in this period."),
)

const isEmpty = computed(
	() =>
		(dashboard.value?.sales_count || 0) === 0 &&
		!(dashboard.value?.recent || []).length,
)

// Display only: never render "-0" — the sign appears only for real values.
// Nominal is the value line; the count drops to the caption so the strip
// keeps one figure per stat (no "1 · -Rp 50.000" pile-up).
const returnsTotalLabel = computed(() => {
	const value = Number.parseFloat(dashboard.value?.returns_total || 0)
	if (value > 0) return `-${formatMoney(value)}`
	if (value < 0) return formatMoney(value)
	return formatMoney(0)
})

const returnsCountLabel = computed(() =>
	__("{0} return invoices", [dashboard.value?.returns_count || 0]),
)

// Companion KPI cards behind the Net Sales hero.
const kpiStats = computed(() => [
	{
		key: "transactions",
		label: __("Transactions"),
		caption: __("Orders"),
		icon: "file-text",
		value: dashboard.value?.sales_count || 0,
	},
	{
		key: "average",
		label: __("Average Transaction"),
		caption: __("Per order"),
		icon: "trending-up",
		value: formatMoney(dashboard.value?.average_sale),
	},
	{
		key: "items",
		label: __("Items Sold"),
		caption: __("Items"),
		icon: "package",
		value: dashboard.value?.total_qty || 0,
		test: "kpi-items-sold",
	},
])

// Pastel icon tiles for the Top Items rows, cycled by rank.
const ITEM_TILES = [
	"bg-amber-100 text-amber-600",
	"bg-orange-100 text-orange-600",
	"bg-yellow-100 text-yellow-600",
	"bg-pink-100 text-pink-600",
	"bg-blue-100 text-blue-600",
]

// Bucket type follows the window, same rule as the backend: hours on a
// single day, days up to 62, calendar months beyond.
const bucketGranularity = computed(() => {
	const period = dashboard.value?.period
	if (!period) return "hour"
	const span = Math.round(
		(new Date(period.to_date) - new Date(period.from_date)) / 86400000,
	)
	return span === 0 ? "hour" : span <= 62 ? "day" : "month"
})

const trendHeading = computed(() => {
	if (bucketGranularity.value === "day") return "Sales by Day"
	if (bucketGranularity.value === "month") return "Sales by Month"
	return "Sales by Hour"
})

function bucketLabel(start) {
	const s = String(start || "")
	if (bucketGranularity.value === "day")
		return `${s.slice(8, 10)}/${s.slice(5, 7)}`
	if (bucketGranularity.value === "month")
		return `${s.slice(5, 7)}/${s.slice(2, 4)}`
	return `${s.slice(11, 13)}:00`
}

// Y-axis scale: four steps up to a "nice" maximum (1/2/2.5/5 × 10^n) so the
// dashed gridlines land on round figures.
const currencySymbol = computed(() =>
	getCurrencySymbol(
		dashboard.value?.company_currency || props.currency || DEFAULT_CURRENCY,
	),
)

function formatCompact(value) {
	const compact = new Intl.NumberFormat("en", {
		notation: "compact",
		maximumFractionDigits: 1,
	}).format(value)
	return `${currencySymbol.value} ${compact}`
}

const chartScale = computed(() => {
	const values = (dashboard.value?.hourly || []).map(
		(b) => Number.parseFloat(b.net_sales) || 0,
	)
	const max = Math.max(0, ...values)
	if (max <= 0) return { niceMax: 0, labels: ["0"] }
	const raw = max / 4
	const mag = 10 ** Math.floor(Math.log10(raw))
	const step =
		[1, 2, 2.5, 5, 10].map((m) => m * mag).find((n) => n >= raw) || 10 * mag
	return {
		niceMax: step * 4,
		labels: [4, 3, 2, 1, 0].map((i) => (i === 0 ? "0" : formatCompact(step * i))),
	}
})

const hourlyBuckets = computed(() => {
	const buckets = dashboard.value?.hourly || []
	const values = buckets.map((b) => Number.parseFloat(b.net_sales) || 0)
	const maxPos = Math.max(0, ...values)
	const maxNeg = Math.max(0, ...values.map((v) => -v))
	const niceMax = chartScale.value.niceMax || maxPos
	// Thin the x labels on wide shifts so they never collide
	const thin = buckets.length > 12
	return buckets.map((b, i) => {
		const value = values[i]
		return {
			...b,
			label: bucketLabel(b.start),
			showLabel: !thin || i % 2 === 0,
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
	return `${bucket.label} · ${formatMoney(bucket.net_sales)} · ${Number.parseFloat(bucket.sales_count) || 0}`
}

// Payment rows: amount + share of the positive total, with a progress bar.
// Shares are taken against the sum of positive amounts only — returns and
// payouts would drag a gross-total share below what the bar can show.
const paymentRows = computed(() => dashboard.value?.payments || [])

const positivePaymentTotal = computed(() =>
	paymentRows.value.reduce(
		(sum, row) => sum + Math.max(0, Number.parseFloat(row.amount) || 0),
		0,
	),
)

const totalPayments = computed(() =>
	paymentRows.value.reduce(
		(sum, row) => sum + (Number.parseFloat(row.amount) || 0),
		0,
	),
)

function paymentPct(amount) {
	if (positivePaymentTotal.value <= 0) return null
	return Math.round(
		((Number.parseFloat(amount) || 0) / positivePaymentTotal.value) * 100,
	)
}

function paymentBarWidth(amount) {
	const pct = paymentPct(amount)
	return pct == null ? "0" : `${Math.max(0, Math.min(100, pct))}%`
}

function isCashMode(modeOfPayment) {
	return Boolean(
		paymentRows.value.find(
			(row) => row.mode_of_payment === modeOfPayment,
		)?.is_cash,
	)
}

// Backend already orders by base_net_amount desc; cap the display at five.
const topItems = computed(() => (dashboard.value?.items || []).slice(0, 5))
const recentRows = computed(() => (dashboard.value?.recent || []).slice(0, 10))

const formatMoney = (amount) =>
	formatCurrencyUtil(
		Number.parseFloat(amount || 0),
		dashboard.value?.company_currency || props.currency || DEFAULT_CURRENCY,
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
