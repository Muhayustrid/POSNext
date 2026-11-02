<template>
	<div class="h-full overflow-y-auto bg-white px-4 py-4 text-start sm:px-6">
		<div class="mx-auto flex w-full max-w-6xl flex-col gap-4">
			<p v-if="isOffline" role="status" class="text-sm text-amber-800">{{ __("You are offline") }}</p>
			<div class="flex gap-2 border-b border-gray-200 pb-3">
				<button type="button" class="spend-tab" :aria-pressed="tab === 'new'" data-test="spend-new-tab" @click="tab = 'new'">{{ __("New") }}</button>
				<button type="button" class="spend-tab" :aria-pressed="tab === 'history'" data-test="spend-history-tab" @click="showHistory">{{ __("History") }}</button>
			</div>
			<p v-if="error" role="alert" class="rounded-lg bg-red-50 p-3 text-sm text-red-800" data-test="spend-error">{{ error }}</p>
			<p v-if="success" role="status" class="text-sm text-green-800">{{ success }}</p>
			<p v-if="!company || !posProfile" class="text-sm text-gray-700">{{ __("Select a company with a POS Profile to record spending.") }}</p>
			<template v-if="tab === 'new'">
				<p v-if="loading" role="status" class="text-sm text-gray-700">{{ __("Loading...") }}</p>
				<p v-else-if="!templates.length && company && posProfile" class="text-sm text-gray-700">{{ __("No spend templates are ready for this company. Contact HQ to check the configuration.") }}</p>
				<form v-if="templates.length" class="flex flex-col gap-4" data-test="spend-form" @submit.prevent="submit">
					<div class="flex flex-wrap items-center justify-between gap-3">
						<h2 class="text-lg font-semibold text-gray-900">{{ __("Purchases & Expenses") }}</h2>
						<div class="flex gap-2">
							<Button type="button" :disabled="busy" class="min-h-11" @click="resetForm">{{ __("Reset") }}</Button>
							<Button type="submit" variant="solid" :disabled="isOffline || busy || loading || !selected || !company || !posProfile" class="min-h-11" data-test="spend-submit">{{ busy ? __("Submitting...") : __("Submit") }}</Button>
						</div>
					</div>
					<p v-if="attempt" class="text-sm text-amber-800">{{ __("Retry sends the same spending details. Reset only to start a separate transaction; check History first if the result is uncertain.") }}</p>
					<fieldset :disabled="busy || !!attempt" class="flex min-w-0 flex-col gap-4">
						<label class="spend-label">{{ __("Spend template") }}
							<select v-model="templateName" required class="spend-input" data-test="spend-template">
								<option value="" disabled>{{ __("Select a template") }}</option>
								<optgroup v-for="group in groups" :key="group.category" :label="group.category">
									<option v-for="item in group.templates" :key="item.name" :value="item.name">{{ item.title || item.name }}</option>
								</optgroup>
							</select>
						</label>
						<template v-if="selected">
							<div class="grid gap-4 sm:grid-cols-2">
								<label class="spend-label">{{ __("Payment source") }}
									<select v-model="modeOfPayment" required class="spend-input" data-test="spend-payment-source">
										<option v-for="source in selected.payment_sources" :key="source.mode_of_payment" :value="source.mode_of_payment">{{ source.mode_of_payment }}</option>
									</select>
									<span v-if="shiftSource" class="inline-flex w-fit rounded-full bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-800 ring-1 ring-inset ring-amber-200" data-test="spend-shift-source">{{ __("Shift drawer") }}</span>
								</label>
								<label v-if="!isJE && selected.allow_supplier_change" class="spend-label">{{ __("Supplier") }}
									<input v-model="supplier" required class="spend-input" data-test="spend-supplier" />
								</label>
								<label class="spend-label">{{ __("Vendor name") }}
									<input v-model="vendorName" :required="!!selected.require_vendor_name" class="spend-input" />
								</label>
								<label class="spend-label">{{ __("Bill No") }}<input v-model="billNo" class="spend-input" /></label>
								<label class="spend-label">{{ __("Bill Date") }}<input v-model="billDate" type="date" class="spend-input" /></label>
							</div>
							<section class="rounded-xl border border-gray-200 bg-white">
								<h3 class="border-b border-gray-200 px-3 py-3 font-semibold text-gray-900">{{ __("Items") }}</h3>
								<div v-for="(line, index) in lines" :key="line.id" class="flex flex-wrap items-end gap-3 border-b border-gray-100 p-3">
									<template v-if="isJE">
										<label class="spend-label min-w-0 flex-[2_1_12rem]">{{ __("Description") }}<input v-model="line.description" required class="spend-input" data-test="spend-description" /></label>
										<label class="spend-label flex-[1_1_8rem]">{{ __("Amount") }}<input v-model.number="line.amount" type="number" min="0.01" step="any" required class="spend-input text-end tabular-nums" data-test="spend-amount" /></label>
									</template>
									<template v-else>
										<label class="spend-label min-w-0 flex-[2_1_12rem]">{{ __("Item") }}
											<select v-model="line.item_code" required class="spend-input" data-test="spend-item">
												<option value="" disabled>{{ __("Select an item") }}</option>
												<option v-for="item in selected.items" :key="item" :value="item">{{ item }}</option>
											</select>
										</label>
										<label class="spend-label flex-[1_1_5rem]">{{ __("Qty") }}<input v-model.number="line.qty" type="number" min="0.000001" step="any" required class="spend-input text-end tabular-nums" data-test="spend-qty" /></label>
										<label class="spend-label flex-[1_1_8rem]">{{ __("Rate") }}<input v-model.number="line.rate" type="number" min="0.01" step="any" required class="spend-input text-end tabular-nums" data-test="spend-rate" /></label>
									</template>
									<button type="button" class="pos-icon-btn text-gray-600" :disabled="lines.length === 1" :aria-label="__('Remove row')" @click="lines.splice(index, 1)"><FeatherIcon name="trash-2" class="h-4 w-4" /></button>
								</div>
								<div class="flex flex-wrap items-center justify-between gap-3 rounded-b-xl bg-gray-50 px-3 py-2">
									<Button type="button" class="min-h-11" @click="addLine">{{ __("Add Row") }}</Button>
									<p class="text-base font-semibold tabular-nums text-gray-900" data-test="spend-total">{{ __("Total") }} {{ money(total) }}</p>
								</div>
							</section>
							<label class="spend-label">{{ __("Remarks") }}<textarea v-model="remarks" rows="2" class="spend-input" /></label>
							<p v-if="selected.requires_attachment" class="text-sm text-gray-700">{{ __("An attachment is required for this spend template.") }}</p>
							<PurchaseAttachments :pending="files" editable @add="addFiles" @remove="removeFile" />
						</template>
					</fieldset>
				</form>
			</template>
			<template v-else>
				<div class="flex justify-end"><Button type="button" :disabled="isOffline || historyLoading || busy" class="min-h-11" @click="loadHistory">{{ __("Refresh") }}</Button></div>
				<p v-if="historyLoading" role="status" class="text-sm text-gray-700">{{ __("Loading...") }}</p>
				<p v-else-if="!history.length" class="text-sm text-gray-700">{{ __("No purchases or expenses recorded yet.") }}</p>
				<div v-if="history.length" class="rounded-xl border border-gray-200">
					<div class="hidden gap-3 border-b border-gray-200 bg-gray-50 px-3 py-2 text-xs font-medium text-gray-500 md:grid md:grid-cols-[minmax(0,1.3fr)_minmax(0,1.5fr)_7rem_8rem_8rem_5rem]">
						<span>{{ __("ID") }}</span><span>{{ __("Spend template") }}</span><span>{{ __("Date") }}</span><span class="text-end">{{ __("Amount") }}</span><span>{{ __("Status") }}</span><span class="text-end">{{ __("Actions") }}</span>
					</div>
					<div v-for="row in history" :key="`${row.doctype}:${row.name}`" class="grid grid-cols-2 items-center gap-3 border-b border-gray-100 p-3 last:border-0 md:grid-cols-[minmax(0,1.3fr)_minmax(0,1.5fr)_7rem_8rem_8rem_5rem]" data-test="spend-history-row">
						<div class="order-1 min-w-0"><p class="break-words font-semibold text-gray-900">{{ row.name }}</p><p class="text-xs text-gray-600">{{ __(row.doctype) }}</p></div>
						<div class="order-3 col-span-2 min-w-0 md:order-2 md:col-span-1"><p class="text-sm text-gray-900">{{ row.template }}</p><p class="text-xs text-gray-700">{{ row.vendor_name }} {{ row.bill_no }}</p><p class="text-xs text-gray-700">{{ row.mode_of_payment }}<span v-if="row.shift"> · {{ row.shift }}</span></p><PurchaseAttachments v-if="row.attachments?.length" :attached="row.attachments" /></div>
						<p class="order-4 text-sm text-gray-700 md:order-3">{{ row.posting_date }}</p>
						<p class="order-5 text-end text-sm font-medium tabular-nums text-gray-900 md:order-4">{{ money(row.amount) }}</p>
						<div class="order-2 text-end md:order-5 md:text-start"><span class="inline-flex shrink-0 items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset" :class="statusTone(row)">{{ __(row.status) }}</span></div>
						<div class="order-6 col-span-2 text-end md:col-span-1"><Button v-if="row.can_cancel" type="button" :disabled="isOffline || busy" class="min-h-11" data-test="spend-cancel" @click="cancelRow = row; cancelReason = ''">{{ __("Cancel") }}</Button></div>
					</div>
				</div>
				<p v-if="history.length >= 50" class="text-sm text-gray-700">{{ __("Showing the 50 most recent transactions.") }}</p>
				<form v-if="cancelRow" class="flex flex-col gap-3 rounded-xl border border-red-200 bg-red-50 p-4" data-test="spend-cancel-form" @submit.prevent="cancelSpend">
					<h3 class="font-semibold text-gray-900">{{ __("Cancel") }} {{ cancelRow.name }}</h3>
					<label class="spend-label">{{ __("Cancellation reason") }}<textarea v-model="cancelReason" required :disabled="busy" class="spend-input" data-test="spend-cancel-reason" /></label>
					<div class="flex gap-2"><Button type="button" :disabled="busy" class="min-h-11" @click="cancelRow = null">{{ __("Back") }}</Button><Button type="submit" variant="solid" theme="red" :disabled="isOffline || busy || !cancelReason.trim()" class="min-h-11" data-test="spend-cancel-confirm">{{ __("Confirm cancellation") }}</Button></div>
				</form>
			</template>
		</div>
	</div>
</template>

<script setup>
import { Button, FeatherIcon } from "frappe-ui"
import { computed, nextTick, ref, watch } from "vue"
import PurchaseAttachments from "@/components/purchase/PurchaseAttachments.vue"
import { call } from "@/utils/apiWrapper"
import { fileToBase64, sanitizePickedFiles } from "@/utils/attachments"
import { compressImage } from "@/utils/compressImage"
import { humanizeError } from "@/utils/errorHandler"
import { generateUUID } from "@/utils/offline/uuid"
import { __ } from "@/utils/translation"

const props = defineProps({
	company: { type: String, default: null },
	posProfile: { type: String, default: null },
	isOffline: { type: Boolean, default: false },
})
const API = "pos_next.api.spend"
const tab = ref("new")
const templates = ref([])
const loading = ref(false)
const busy = ref(false)
const error = ref("")
const success = ref("")
const templateName = ref("")
const modeOfPayment = ref("")
const supplier = ref("")
const vendorName = ref("")
const billNo = ref("")
const billDate = ref("")
const remarks = ref("")
const lines = ref([])
const files = ref([])
const requestId = ref(generateUUID())
// A lost response can mean success: retries must send the original transaction.
const attempt = defineModel("attempt", { default: null })
const history = ref([])
const historyLoading = ref(false)
const cancelRow = ref(null)
const cancelReason = ref("")
let scopeVersion = 0
let historyVersion = 0
const selected = computed(() =>
	templates.value.find((t) => t.name === templateName.value),
)
const isJE = computed(() => selected.value?.document_type === "Journal Entry")
const shiftSource = computed(
	() =>
		selected.value?.payment_sources?.find(
			(s) => s.mode_of_payment === modeOfPayment.value,
		)?.is_shift_source,
)
const groups = computed(() => {
	const grouped = new Map()
	for (const template of templates.value) {
		const category = template.category || __("Other")
		if (!grouped.has(category)) grouped.set(category, [])
		grouped.get(category).push(template)
	}
	return Array.from(grouped, ([category, templates]) => ({
		category,
		templates,
	}))
})
const total = computed(() =>
	lines.value.reduce(
		(sum, line) =>
			sum +
			(isJE.value
				? Number(line.amount || 0)
				: Number(line.qty || 0) * Number(line.rate || 0)),
		0,
	),
)
const money = (value) =>
	`Rp ${new Intl.NumberFormat("id-ID", { maximumFractionDigits: 2 }).format(value || 0)}`
const message = (err) =>
	humanizeError(err)?.message ||
	__("Could not complete the spending request. Please try again.")

function addLine() {
	lines.value.push({
		id: generateUUID(),
		description: "",
		amount: "",
		item_code: "",
		qty: 1,
		rate: "",
	})
}
function resetForm() {
	requestId.value = generateUUID()
	attempt.value = null
	templateName.value = ""
	modeOfPayment.value = ""
	supplier.value = ""
	vendorName.value = ""
	billNo.value = ""
	billDate.value = ""
	remarks.value = ""
	files.value = []
	lines.value = []
	error.value = ""
}
watch(templateName, () => {
	lines.value = []
	addLine()
	modeOfPayment.value =
		selected.value?.payment_sources?.find((s) => s.is_default)
			?.mode_of_payment ||
		selected.value?.payment_sources?.[0]?.mode_of_payment ||
		""
	supplier.value = selected.value?.default_supplier || ""
})
watch(
	() => [props.company, props.posProfile],
	async () => {
		const version = ++scopeVersion
		++historyVersion
		if (!attempt.value) resetForm()
		success.value = ""
		templates.value = []
		history.value = []
		cancelRow.value = null
		historyLoading.value = false
		loading.value = false
		if (!props.company || !props.posProfile) return
		loading.value = true
		try {
			const rows = await call(`${API}.get_spend_templates`, {
				company: props.company,
				pos_profile: props.posProfile,
			})
			if (version === scopeVersion) {
				templates.value = rows || []
				if (attempt.value) {
					templateName.value = attempt.value.template
					await nextTick()
					modeOfPayment.value = attempt.value.mode_of_payment
					supplier.value = attempt.value.supplier
					vendorName.value = attempt.value.vendor_name
					billNo.value = attempt.value.bill_no
					billDate.value = attempt.value.bill_date || ""
					remarks.value = attempt.value.remarks
					lines.value = attempt.value.lines.map((line) => ({
						...line,
						id: generateUUID(),
					}))
				}
			}
		} catch (err) {
			if (version === scopeVersion) error.value = message(err)
		} finally {
			if (version === scopeVersion) loading.value = false
		}
		if (version === scopeVersion && tab.value === "history") loadHistory()
	},
	{ immediate: true },
)

function addFiles(picked) {
	if (busy.value || attempt.value) return
	const { ok, tooBig } = sanitizePickedFiles(picked)
	files.value = [...files.value, ...ok]
	if (tooBig.length) error.value = __("Attachments must be 10 MB or smaller.")
}
function removeFile(file) {
	if (!busy.value && !attempt.value)
		files.value = files.value.filter((picked) => picked !== file)
}
async function submit() {
	if (
		props.isOffline ||
		busy.value ||
		!selected.value ||
		!props.company ||
		!props.posProfile
	)
		return
	if (
		!attempt.value &&
		selected.value.requires_attachment &&
		!files.value.length
	) {
		error.value = __("An attachment is required for this spend template.")
		return
	}
	const version = scopeVersion
	busy.value = true
	error.value = ""
	success.value = ""
	try {
		if (!attempt.value) {
			const data = {
				client_request_id: requestId.value,
				company: props.company,
				pos_profile: props.posProfile,
				template: templateName.value,
				mode_of_payment: modeOfPayment.value,
				supplier: supplier.value,
				vendor_name: vendorName.value,
				bill_no: billNo.value,
				bill_date: billDate.value || null,
				remarks: remarks.value,
				lines: lines.value.map((line) =>
					isJE.value
						? { description: line.description, amount: line.amount }
						: { item_code: line.item_code, qty: line.qty, rate: line.rate },
				),
				files: [],
			}
			for (const file of files.value) {
				const compressed = await compressImage(file)
				data.files.push({
					file_name: compressed.name,
					filedata: await fileToBase64(compressed),
				})
			}
			if (version !== scopeVersion) return
			attempt.value = data
			await nextTick()
		}
		const result = await call(`${API}.submit_spend`, {
			data: JSON.stringify(attempt.value),
		})
		if (version !== scopeVersion) return
		resetForm()
		success.value = __("Spending recorded: {0}", [result.name])
		if (tab.value === "history") loadHistory()
	} catch (err) {
		if (version === scopeVersion) error.value = message(err)
	} finally {
		busy.value = false
	}
}
async function showHistory() {
	tab.value = "history"
	await loadHistory()
}
async function loadHistory() {
	const version = ++historyVersion
	if (props.isOffline || !props.company) return
	historyLoading.value = true
	try {
		const rows = await call(`${API}.get_spend_history`, {
			company: props.company,
			pos_profile: props.posProfile,
		})
		if (version === historyVersion) history.value = rows || []
	} catch (err) {
		if (version === historyVersion) error.value = message(err)
	} finally {
		if (version === historyVersion) historyLoading.value = false
	}
}
async function cancelSpend() {
	if (
		props.isOffline ||
		busy.value ||
		!cancelRow.value?.can_cancel ||
		!cancelReason.value.trim()
	)
		return
	const version = scopeVersion
	busy.value = true
	error.value = ""
	try {
		await call(`${API}.cancel_spend`, {
			doctype: cancelRow.value.doctype,
			name: cancelRow.value.name,
			reason: cancelReason.value.trim(),
		})
		if (version !== scopeVersion) return
		cancelRow.value = null
		await loadHistory()
	} catch (err) {
		if (version === scopeVersion) error.value = message(err)
	} finally {
		busy.value = false
	}
}
function statusTone(row) {
	if (row.docstatus === 2) return "bg-gray-100 text-gray-700 ring-gray-200"
	if (row.docstatus === 0) return "bg-orange-50 text-orange-800 ring-orange-200"
	return "bg-green-50 text-green-800 ring-green-200"
}
</script>

<style scoped>
.spend-label { @apply flex flex-col gap-1.5 text-sm font-medium text-gray-700; }
.spend-input { @apply min-h-11 w-full min-w-0 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:border-gray-500 focus:ring-1 focus:ring-gray-500 disabled:bg-gray-50; }
.spend-tab { @apply min-h-11 rounded-lg px-4 py-2 text-sm font-medium text-gray-600; }
.spend-tab[aria-pressed="true"] { @apply bg-gray-900 text-white; }
</style>
