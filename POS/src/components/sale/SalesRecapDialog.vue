<template>
	<Dialog v-model="show" :options="{ title: __('Sales Recap'), size: '2xl' }">
		<template #body>
			<div class="flex flex-col max-h-[calc(100dvh-6rem)] text-start">
				<!-- Fixed header: period chips -->
				<div class="shrink-0 border-b border-gray-200 px-4 pt-4 sm:px-5" data-test="dialog-header">
					<div class="flex items-center justify-between gap-3">
						<DialogTitle class="text-lg font-semibold leading-6 text-gray-900">
							{{ __("Sales Recap") }}
						</DialogTitle>
						<Button
							variant="ghost"
							@click="show = false"
							:aria-label="__('Close')"
							:title="__('Close')"
						>
							<svg class="w-4 h-4 text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
							</svg>
						</Button>
					</div>
					<div class="mt-3 flex flex-wrap gap-1.5 pb-3" role="tablist" :aria-label="__('Period')">
						<button
							v-for="chip in chips"
							:key="chip.mode"
							@click="selectPeriod(chip.mode)"
							role="tab"
							:aria-selected="period === chip.mode"
							:class="[
								'px-3 py-1.5 text-sm font-medium rounded-full transition-all duration-200',
								period === chip.mode
									? 'bg-gray-900 text-white shadow-sm'
									: 'bg-gray-100 text-gray-600 hover:text-gray-900 hover:bg-gray-200/50',
							]"
						>
							{{ __(chip.label) }}
						</button>
					</div>
					<!-- Custom range inputs -->
					<div v-if="period === 'custom'" class="flex flex-wrap items-center gap-2 pb-3">
						<input
							v-model="customFrom"
							type="date"
							class="rounded border border-gray-300 px-2 py-1.5 text-sm text-gray-900"
							:aria-label="__('From Date')"
						/>
						<span class="text-sm text-gray-500">{{ __("to") }}</span>
						<input
							v-model="customTo"
							type="date"
							class="rounded border border-gray-300 px-2 py-1.5 text-sm text-gray-900"
							:aria-label="__('To Date')"
						/>
						<Button variant="subtle" theme="blue" @click="applyCustom">
							{{ __("Apply") }}
						</Button>
					</div>
				</div>

				<!-- Scrollable body -->
				<div class="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-5" data-test="dialog-body">
					<!-- This Shift: the existing full session summary (fetches its own data) -->
					<SessionSummary v-if="period === 'shift'" :opening-shift="openingShift" />

					<template v-else>
						<div v-if="recapResource.loading" class="text-center py-8">
							<div class="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500 mx-auto"></div>
							<p class="mt-3 text-xs text-gray-500">{{ __("Loading recap...") }}</p>
						</div>

						<div v-else-if="!recap" class="text-center py-8">
							<p class="text-sm text-gray-500">{{ __("Could not load the sales recap.") }}</p>
							<Button variant="subtle" class="mt-3" @click="recapResource.reload()">
								{{ __("Retry") }}
							</Button>
						</div>

						<template v-else>
							<p v-if="!recap.invoice_count" class="mb-2 text-center text-xs text-gray-500" role="status">
								{{ __("No sales in this period.") }}
							</p>

							<!-- Totals -->
							<dl class="rounded-lg border border-gray-200 p-3" data-test="recap-totals" :aria-label="__('Totals')">
								<div
									v-for="row in totalsRows"
									:key="row[0]"
									class="flex items-baseline justify-between gap-3 py-1.5 border-b border-gray-100 last:border-b-0"
								>
									<dt class="text-sm" :class="row[0] === 'grand' ? 'font-medium text-gray-900' : 'text-gray-600'">
										{{ row[1] }}
									</dt>
									<dd
										class="text-sm tabular-nums whitespace-nowrap"
										:class="row[0] === 'grand' ? 'text-base font-bold text-gray-900' : 'font-semibold text-gray-900'"
									>
										{{ row[2] }}
									</dd>
								</div>
							</dl>

							<!-- Payments -->
							<section class="mt-3 rounded-lg border border-gray-200 p-3" aria-labelledby="sr-pay-h" data-test="recap-payments">
								<h3 id="sr-pay-h" class="text-xs font-semibold text-gray-500 uppercase tracking-wide">{{ __("Payment Methods") }}</h3>
								<table v-if="recap.payments.length" class="mt-1 w-full text-sm">
									<thead>
										<tr class="text-xs text-gray-500">
											<th class="py-1 font-medium text-start">{{ __("Mode") }}</th>
											<th class="py-1 font-medium text-end">{{ __("Amount") }}</th>
										</tr>
									</thead>
									<tbody>
										<tr v-for="row in recap.payments" :key="row.mode_of_payment" class="border-t border-gray-100">
											<td class="py-1.5 text-gray-700 min-w-0 break-words">{{ row.mode_of_payment }}</td>
											<td class="py-1.5 text-end font-semibold text-gray-900 tabular-nums whitespace-nowrap">
												{{ formatMoney(row.amount) }}
											</td>
										</tr>
									</tbody>
								</table>
								<p v-else class="mt-1 text-xs text-gray-500">{{ __("No payments recorded in this period.") }}</p>
							</section>
						</template>
					</template>
				</div>

				<!-- Footer -->
				<div
					class="flex shrink-0 items-center justify-between border-t border-gray-200 px-4 py-2.5 sm:px-5"
					data-test="dialog-footer"
				>
					<Button variant="subtle" theme="blue" :loading="printing" :disabled="!canPrint" @click="print">
						{{ __("Print") }}
					</Button>
					<Button variant="subtle" @click="show = false">
						{{ __("Close") }}
					</Button>
				</div>
			</div>
		</template>
	</Dialog>
</template>

<script>
// Pure helper exported for tests (exports are not allowed in <script setup>).
/**
 * Local-date range for a recap chip. `to` is inclusive. "shift" and
 * "custom" have no fixed range → null.
 */
export function recapPeriodRange(mode, now = new Date()) {
	const iso = (d) =>
		`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`

	if (mode === "today") {
		const s = iso(now)
		return { from: s, to: s }
	}
	if (mode === "yesterday") {
		const s = iso(new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1))
		return { from: s, to: s }
	}
	if (mode === "this_month") {
		return { from: iso(new Date(now.getFullYear(), now.getMonth(), 1)), to: iso(now) }
	}
	if (mode === "last_month") {
		return {
			from: iso(new Date(now.getFullYear(), now.getMonth() - 1, 1)),
			to: iso(new Date(now.getFullYear(), now.getMonth(), 0)),
		}
	}
	return null
}
</script>

<script setup>
import { useToast } from "@/composables/useToast"
import { call } from "@/utils/apiWrapper"
import {
	DEFAULT_CURRENCY,
	formatCurrency as formatCurrencyUtil,
} from "@/utils/currency"
import { printHTML } from "@/utils/print/transport"
import { Button, Dialog, createResource } from "frappe-ui"
import { DialogTitle } from "reka-ui"
import { computed, ref, watch } from "vue"
import SessionSummary from "./SessionSummary.vue"

const { showError, showSuccess } = useToast()

const props = defineProps({
	modelValue: Boolean,
	posProfile: { type: String, default: null },
	openingShift: { type: String, default: null },
})

const emit = defineEmits(["update:modelValue"])

const chips = [
	{ mode: "shift", label: "This Shift" },
	{ mode: "today", label: "Today" },
	{ mode: "yesterday", label: "Yesterday" },
	{ mode: "this_month", label: "This Month" },
	{ mode: "last_month", label: "Last Month" },
	{ mode: "custom", label: "Custom" },
]

const show = ref(props.modelValue)
const period = ref("shift")
const customFrom = ref("")
const customTo = ref("")
const range = ref(null)
const printing = ref(false)

const recapResource = createResource({
	url: "pos_next.api.shifts.get_sales_recap",
	makeParams() {
		return {
			pos_profile: props.posProfile || undefined,
			from_date: range.value?.from,
			to_date: range.value?.to,
		}
	},
	auto: false,
	onError(error) {
		console.error("Error loading sales recap:", error)
		showError(__("Failed to load sales recap"))
	},
})

const recap = computed(() => recapResource.data || null)

watch(
	() => props.modelValue,
	(val) => {
		show.value = val
		if (val) {
			customFrom.value = ""
			customTo.value = ""
			if (props.openingShift) {
				period.value = "shift"
				range.value = null
			} else {
				period.value = "today"
				range.value = recapPeriodRange("today")
				recapResource.reload()
			}
		}
	},
)

watch(show, (val) => {
	emit("update:modelValue", val)
})

function selectPeriod(mode) {
	if (period.value === mode) return
	period.value = mode
	if (mode === "shift" || mode === "custom") return
	range.value = recapPeriodRange(mode)
	recapResource.reload()
}

function applyCustom() {
	if (!customFrom.value || !customTo.value) {
		showError(__("Select both dates"))
		return
	}
	if (customFrom.value > customTo.value) {
		showError(__("From date cannot be after to date"))
		return
	}
	range.value = { from: customFrom.value, to: customTo.value }
	recapResource.reload()
}

const currency = computed(
	() =>
		(period.value === "shift" ? null : recap.value?.company_currency) ||
		DEFAULT_CURRENCY,
)

function formatMoney(amount) {
	return formatCurrencyUtil(Number.parseFloat(amount || 0), currency.value)
}

// label key doubles as the highlight flag for the grand-total row
const totalsRows = computed(() => {
	const r = recap.value
	if (!r) return []
	return [
		["invoices", __("Invoices"), String(r.invoice_count ?? 0)],
		["returns", __("Returns"), String(r.returned_count ?? 0)],
		["gross", __("Gross"), formatMoney(r.gross_total)],
		["discount", __("Discount"), formatMoney(r.total_discount)],
		["tax", __("Tax"), formatMoney(r.total_taxes)],
		["net", __("Net"), formatMoney(r.net_total)],
		["grand", __("Grand Total"), formatMoney(r.grand_total)],
	]
})

const canPrint = computed(() =>
	period.value === "shift" ? Boolean(props.openingShift) : Boolean(recap.value),
)

const periodLabel = computed(() => {
	if (period.value === "shift") return __("This Shift")
	if (period.value === "custom") return `${range.value?.from} → ${range.value?.to}`
	const chip = chips.find((c) => c.mode === period.value)
	const r = recapPeriodRange(period.value)
	return `${__(chip?.label || period.value)} (${r ? `${r.from} → ${r.to}` : ""})`
})

// Escape text going into the receipt HTML (mode names are user-defined).
function esc(value) {
	return String(value ?? "")
		.replace(/&/g, "&amp;")
		.replace(/</g, "&lt;")
		.replace(/>/g, "&gt;")
}

function buildRecapHTML(data, label) {
	const money = (v) => esc(formatCurrencyUtil(Number.parseFloat(v || 0), data.currency || DEFAULT_CURRENCY))
	const rows = [
		[__("Invoices"), esc(data.invoice_count ?? 0)],
		[__("Returns"), esc(data.returned_count ?? 0)],
		[__("Gross"), money(data.gross_total)],
		[__("Discount"), money(data.total_discount)],
		[__("Tax"), money(data.total_taxes)],
		[__("Net"), money(data.net_total)],
		[__("Grand Total"), money(data.grand_total)],
	]
	const payments = (data.payments || []).filter((p) => p.amount)
	const paymentRows = payments
		.map(
			(p) =>
				`<tr><td style="padding:2px 0">${esc(p.mode_of_payment)}</td><td style="padding:2px 0;text-align:right">${money(p.amount)}</td></tr>`,
		)
		.join("")
	return `<div style="font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;color:#000;max-width:300px;margin:0 auto">
<h3 style="margin:0 0 4px;font-size:14px;text-align:center">${esc(__("Sales Recap"))}</h3>
<p style="margin:0 0 8px;text-align:center">${esc(label)}</p>
<table style="width:100%;border-collapse:collapse">
${rows
	.map(
		([k, v]) =>
			`<tr><td style="padding:2px 0">${k}</td><td style="padding:2px 0;text-align:right">${v}</td></tr>`,
	)
	.join("")}
</table>
${paymentRows ? `<p style="margin:8px 0 2px;font-weight:bold">${esc(__("Payment Methods"))}</p>
<table style="width:100%;border-collapse:collapse">${paymentRows}</table>` : ""}
</div>`
}

// Shift summary payload → the same shape the date recap prints.
function shiftRecapData(s) {
	return {
		currency: s.company_currency,
		invoice_count: s.invoice_count,
		returned_count: s.returns_count,
		gross_total: s.gross_sales,
		total_discount: s.total_discount,
		total_taxes: s.tax_total,
		net_total: s.net_total,
		grand_total: s.net_sales,
		payments: s.payments || [],
	}
}

async function print() {
	printing.value = true
	try {
		let data
		let label
		if (period.value === "shift") {
			data = shiftRecapData(
				await call("pos_next.api.shifts.get_session_summary", {
					opening_shift: props.openingShift,
				}),
			)
			label = periodLabel.value
		} else {
			data = recap.value
			label = periodLabel.value
		}
		await printHTML(buildRecapHTML(data, label), {
			logContext: {
				reference_doctype: "Sales Invoice",
				reference_name: "SALES-RECAP",
			},
		})
		showSuccess(__("Recap sent to printer."))
	} catch (error) {
		console.error("Error printing sales recap:", error)
		showError(__("Failed to print sales recap"))
	} finally {
		printing.value = false
	}
}
</script>
