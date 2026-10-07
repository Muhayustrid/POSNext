<template>
	<div class="flex flex-col gap-4">
		<!-- Menu gating hides this view offline; this is only a fallback -->
		<div v-if="!openingShift && !posProfile" class="text-center py-8">
			<p class="text-sm text-gray-500">{{ __("No open shift for this session.") }}</p>
		</div>

		<template v-else>
			<!-- Mode selector: shift lens or a posting-date window over the whole
			     profile. Managers only — cashiers get the shift lens without the
			     profile-wide windows. -->
			<div v-if="isManagement" class="flex flex-wrap items-center gap-2" data-test="mode-chips">
				<label class="text-xs text-gray-500" for="dash-period">{{ __("Period") }}</label>
				<select
					id="dash-period"
					v-model="mode"
					class="rounded-md border border-gray-300 bg-white px-2 py-1 text-sm text-gray-900"
					data-test="period-select"
				>
					<option v-for="preset in presets" :key="preset.value" :value="preset.value">
						{{ preset.label }}
					</option>
				</select>
				<template v-if="mode === 'custom'">
					<input
						v-model="customFrom"
						type="date"
						:max="customTo || undefined"
						class="rounded-md border border-gray-300 px-2 py-1 text-sm text-gray-900"
						:aria-label="__('From Date')"
						data-test="period-from"
					/>
					<span class="text-xs text-gray-500">–</span>
					<input
						v-model="customTo"
						type="date"
						:min="customFrom || undefined"
						class="rounded-md border border-gray-300 px-2 py-1 text-sm text-gray-900"
						:aria-label="__('To Date')"
						data-test="period-to"
					/>
				</template>
			</div>

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

				<!-- Toolbar band: shift context chips + manual refresh -->
				<div class="flex flex-wrap items-center justify-between gap-3">
					<div class="flex min-w-0 flex-wrap items-center gap-1.5" data-test="shift-chips">
						<template v-if="isShiftMode">
							<span :class="chipClass" data-test="chip-shift">{{ dashboard.opening_shift }}</span>
							<span v-if="dashboard.period_start_date" :class="chipClass">
								{{ __("Opened") }} {{ formatDateTime(dashboard.period_start_date) }}
							</span>
							<span v-if="shiftStore.shiftDuration" :class="chipClass" data-test="chip-duration">
								{{ shiftStore.shiftDuration }}
							</span>
							<span v-if="dashboard.cashier" :class="chipClass">
								{{ __("Cashier") }}: {{ dashboard.cashier }}
							</span>
						</template>
						<template v-else>
							<span :class="chipClass" data-test="chip-period">{{ presetLabel }}</span>
							<span v-if="dashboard.period" :class="chipClass" data-test="chip-period-range">
								{{ formatDate(dashboard.period.from_date) }} – {{ formatDate(dashboard.period.to_date) }}
							</span>
						</template>
						<span v-if="dashboard.pos_profile" :class="chipClass">{{ dashboard.pos_profile }}</span>
					</div>
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

				<!-- Ringkasan: satu angka utama (penjualan bersih), sisanya statistik
				     pendamping tenang. Info tunai hidup di Metode Pembayaran — bukan
				     kartu terpisah — supaya "kas di laci" terbaca di satu tempat. -->
				<div
					class="flex flex-wrap items-end justify-between gap-x-8 gap-y-3 rounded-lg border border-gray-200 p-4"
					data-test="kpi-grid"
				>
					<div data-test="kpi-net-sales">
						<div class="text-xs text-gray-500">{{ __("Net Sales") }}</div>
						<div class="mt-1 text-2xl font-bold text-gray-900 tabular-nums sm:text-3xl">
							{{ formatMoney(dashboard.net_sales) }}
						</div>
						<div class="mt-1 text-xs text-gray-500 tabular-nums">
							{{ __("Transactions") }} {{ dashboard.sales_count || 0 }}
							· {{ __("Average Transaction") }} {{ formatMoney(dashboard.average_sale) }}
						</div>
					</div>
					<div class="flex flex-wrap gap-x-8 gap-y-2" data-test="kpi-strip">
						<div data-test="kpi-items-sold">
							<div class="text-xs text-gray-500">{{ __("Items Sold") }}</div>
							<div class="font-semibold text-gray-900 tabular-nums">
								{{ dashboard.total_qty || 0 }}
							</div>
						</div>
						<div data-test="kpi-discounts">
							<div class="text-xs text-gray-500">{{ __("Discounts") }}</div>
							<div class="font-semibold text-gray-900 tabular-nums">
								{{ formatMoney(dashboard.total_discount) }}
							</div>
						</div>
						<div v-if="(dashboard.returns_count || 0) > 0" data-test="kpi-returns">
							<div class="text-xs text-gray-500">{{ __("Returns") }}</div>
							<div class="font-semibold text-red-600 tabular-nums">
								{{ returnsTotalLabel }}
							</div>
							<div class="mt-1 text-xs text-gray-500 tabular-nums">
								{{ returnsCountLabel }}
							</div>
						</div>
					</div>
				</div>

				<!-- Sales by hour: CSS bars around a zero baseline, returns hang below -->
				<section
					class="rounded-lg border border-gray-200 p-3"
					aria-labelledby="sd-hourly-h"
					data-test="hourly-section"
				>
					<h3 id="sd-hourly-h" class="text-xs font-semibold text-gray-500 uppercase tracking-wide">
						{{ __(trendHeading) }}
					</h3>
					<div v-if="hourlyBuckets.length" class="mt-3" data-test="hourly-chart">
						<div class="flex items-stretch gap-1 sm:gap-2">
							<div
								v-for="b in hourlyBuckets"
								:key="b.start"
								class="flex min-w-0 flex-1 flex-col"
								data-test="hour-column"
							>
								<div class="flex h-28 items-end justify-center" aria-hidden="true">
									<div
										v-if="b.net_sales > 0"
										data-test="hour-bar"
										class="w-full max-w-6 rounded-t"
										:class="b.is_current ? 'bg-blue-300' : 'bg-blue-500'"
										:style="{ height: b.posHeight }"
										:title="barTitle(b)"
									></div>
								</div>
								<!-- zero baseline -->
								<div class="border-t border-gray-300"></div>
								<div class="flex h-8 items-start justify-center" aria-hidden="true">
									<div
										v-if="b.net_sales < 0"
										data-test="hour-bar"
										class="w-full max-w-6 rounded-b bg-red-400"
										:style="{ height: b.negHeight }"
										:title="barTitle(b)"
									></div>
								</div>
								<div class="mt-1 text-center text-[10px] text-gray-400 tabular-nums">
									<span v-if="b.showLabel">{{ b.label }}</span>
								</div>
							</div>
						</div>
					</div>
					<p v-else class="mt-2 text-xs text-gray-500">{{ emptyText }}</p>
					<p v-if="bucketGranularity === 'hour'" class="mt-2 text-xs text-gray-500">{{ __("Returns are netted into their hour.") }}</p>
				</section>

				<!-- Payment methods -->
				<section
					class="rounded-lg border border-gray-200 p-3"
					aria-labelledby="sd-pay-h"
					data-test="payments-section"
				>
					<h3 id="sd-pay-h" class="text-xs font-semibold text-gray-500 uppercase tracking-wide">
						{{ __("Payment Methods") }}
					</h3>
					<!-- Baris tenang: nama mode — jumlah. Tanpa persen & bar: persen
					     terhadap total bruto menyesatkan saat ada retur/pengeluaran. -->
					<div v-if="paymentRows.length" data-test="payments-list">
						<div
							v-for="row in paymentRows"
							:key="row.mode_of_payment"
							class="flex items-baseline justify-between gap-3 border-b border-gray-100 py-1.5 last:border-b-0"
							data-test="payment-row"
						>
							<span class="min-w-0 text-sm text-gray-700">
								{{ row.mode_of_payment }}
							</span>
							<span
								class="shrink-0 whitespace-nowrap text-sm font-semibold tabular-nums"
								:class="(Number.parseFloat(row.amount) || 0) < 0 ? 'text-red-600' : 'text-gray-900'"
							>
								{{ formatMoney(row.amount) }}
							</span>
						</div>
					</div>
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
					<p v-else class="mt-1 text-xs text-gray-500">
						{{ __("No payment methods are configured on this POS Profile.") }}
					</p>
				</section>

				<!-- Details: top items + recent transactions -->
				<div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
					<section
						class="rounded-lg border border-gray-200 p-3"
						aria-labelledby="sd-items-h"
						data-test="top-items-section"
					>
						<h3 id="sd-items-h" class="text-xs font-semibold text-gray-500 uppercase tracking-wide">
							{{ __("Top Items") }}
						</h3>
						<ul v-if="topItems.length" class="mt-1" data-test="top-items-list">
							<li
								v-for="item in topItems"
								:key="item.item_code"
								class="flex items-baseline justify-between gap-3 py-1.5 border-b border-gray-100 last:border-b-0"
								data-test="top-item"
							>
								<span class="min-w-0 truncate text-sm text-gray-900">{{ item.item_name }}</span>
								<span class="flex shrink-0 items-baseline gap-3 whitespace-nowrap">
									<span class="text-xs text-gray-500 tabular-nums">{{ formatQty(item.qty) }}</span>
									<span class="text-sm font-medium text-gray-900 tabular-nums">
										{{ formatMoney(item.base_net_amount) }}
									</span>
								</span>
							</li>
						</ul>
						<p v-else class="mt-1 text-xs text-gray-500">{{ emptyText }}</p>
					</section>

					<section
						class="rounded-lg border border-gray-200 p-3"
						aria-labelledby="sd-recent-h"
						data-test="recent-section"
					>
						<h3 id="sd-recent-h" class="text-xs font-semibold text-gray-500 uppercase tracking-wide">
							{{ __("Recent Transactions") }}
						</h3>
						<ul v-if="recentRows.length" class="mt-1" data-test="recent-list">
							<li
								v-for="row in recentRows"
								:key="row.name"
								class="flex flex-wrap items-center gap-x-2 gap-y-1 py-1.5 border-b border-gray-100 last:border-b-0"
								data-test="recent-row"
							>
								<span class="text-xs text-gray-500 tabular-nums">{{ formatTime(row.posting_dt) }}</span>
								<span class="min-w-0 truncate text-sm text-gray-900">{{ row.name }}</span>
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
								<span class="ms-auto flex shrink-0 items-baseline gap-2 whitespace-nowrap">
									<span class="text-xs text-gray-400">{{ row.payment_mode || "—" }}</span>
									<span
										class="text-sm font-semibold tabular-nums"
										:class="row.amount < 0 ? 'text-red-600' : 'text-gray-900'"
										data-test="recent-amount"
									>
										{{ formatMoney(row.amount) }}
									</span>
								</span>
							</li>
						</ul>
						<p v-else class="mt-1 text-xs text-gray-500">{{ emptyText }}</p>
					</section>
				</div>
			</template>
		</template>
	</div>
</template>

<script setup>
import RefreshButton from "@/components/common/RefreshButton.vue"
import StatusBadge from "@/components/common/StatusBadge.vue"
import { useFormatters } from "@/composables/useFormatters"
import { useBootstrapStore } from "@/stores/bootstrap"
import { usePOSShiftStore } from "@/stores/posShift"
import {
	DEFAULT_CURRENCY,
	formatCurrency as formatCurrencyUtil,
} from "@/utils/currency"
import { periodRange } from "@/utils/salesRecap"
import { Button, createResource } from "frappe-ui"
import { computed, ref, watch } from "vue"

const { formatDate, formatTime } = useFormatters()

// Read-only: live strings from the shell's 1s tick (duration chip + the
// clock that keeps the "updated N min ago" label aging).
const shiftStore = usePOSShiftStore()

// Period lens is a manager view; the bootstrap flag lives next to
// can_switch_to_desk (see POSSale).
const bootstrapStore = useBootstrapStore()
const isManagement = computed(() => Boolean(bootstrapStore.data?.is_management))

const props = defineProps({
	embedded: { type: Boolean, default: false },
	openingShift: { type: String, default: "" },
	posProfile: { type: String, default: "" },
	currency: { type: String, default: "" },
})

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

const chipClass = "rounded-full bg-gray-100 px-2.5 py-1 text-xs text-gray-700"

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

const hourlyBuckets = computed(() => {
	const buckets = dashboard.value?.hourly || []
	const values = buckets.map((b) => Number.parseFloat(b.net_sales) || 0)
	const maxPos = Math.max(0, ...values)
	const maxNeg = Math.max(0, ...values.map((v) => -v))
	// Thin the x labels on wide shifts so they never collide
	const thin = buckets.length > 12
	return buckets.map((b, i) => {
		const value = values[i]
		return {
			...b,
			label: bucketLabel(b.start),
			showLabel: !thin || i % 2 === 0,
			posHeight:
				value > 0 && maxPos > 0
					? `${Math.max(4, Math.round((value / maxPos) * 100))}%`
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

// Payment rows render plainly (mode — amount); no share percentages: with
// returns/payouts in the mix, a share of the gross total reads as nonsense.
const paymentRows = computed(() => dashboard.value?.payments || [])

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
