<template>
	<div class="h-full min-w-0 max-w-full overflow-y-auto bg-white px-4 py-4 text-start sm:px-6">
		<div class="mx-auto flex w-full min-w-0 max-w-6xl flex-col gap-4">
			<p v-if="isOffline" role="status" class="text-sm text-amber-800">{{ __("You are offline") }}</p>

			<p v-if="error" role="alert" class="rounded-lg bg-red-50 p-3 text-sm text-red-800" data-test="spend-error">{{ error }}</p>
			<p v-if="success" role="status" class="text-sm text-green-800">{{ success }}</p>
			<p v-if="!company || !posProfile" class="text-sm text-gray-700">{{ __("Select a company with a POS Profile to record spending.") }}</p>
            <template v-if="tab === 'form'">
                <div class="flex flex-wrap items-center gap-2">
                    <button type="button" class="-ms-1 flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-gray-500 hover:bg-gray-100" :disabled="busy || !!attempt" :aria-label="__('Back')" data-test="spend-back" @click="leaveForm"><FeatherIcon name="arrow-left" class="h-5 w-5" /></button>
                    <nav class="flex min-w-0 flex-1 items-center gap-1.5 text-sm"><span class="hidden text-gray-500 sm:inline">{{ __('Purchases & Expenses') }}</span><span class="hidden text-gray-300 sm:inline">/</span><span class="truncate font-semibold text-gray-900">{{ __('New purchase or expense') }}</span></nav>
                    <Button type="submit" form="spend-entry-form" variant="solid" :disabled="isOffline || busy || loading || !selected || !company || !posProfile" class="h-11 shrink-0 touch-manipulation" data-test="spend-submit">{{ busy ? __('Submitting...') : __('Submit') }}</Button>
                </div>
                <p v-if="loading" role="status" class="text-sm text-gray-600">{{ __('Loading...') }}</p>
                <p v-else-if="!templates.length && company && posProfile" class="text-sm text-gray-600">{{ __('No spend templates are ready for this company. Contact HQ to check the configuration.') }}</p>
                <form v-if="templates.length" id="spend-entry-form" class="flex min-w-0 flex-col gap-4" data-test="spend-form" @submit.prevent="submit">
                    <p v-if="attempt" class="rounded-lg bg-amber-50 p-3 text-sm text-amber-800">{{ __('The result is uncertain. Retry sends the original transaction; do not record it again.') }}</p>
                    <fieldset :disabled="busy || !!attempt" class="flex min-w-0 flex-col gap-4">
                        <section class="rounded-xl border border-gray-200 bg-white p-4" data-test="spend-information">
                            <h3 class="mb-3 text-xs font-semibold uppercase tracking-wide text-gray-500">{{ __('Transaction Information') }}</h3>
                            <div class="grid min-w-0 gap-3 sm:grid-cols-2">
                                <label class="block min-w-0 sm:col-span-2"><span class="mb-1 block text-xs font-medium text-gray-700">{{ __('Spend template') }} <span class="text-red-500">*</span></span><AutocompleteSelect v-model="templateName" :options="templateOptions" :placeholder="__('Select a template')" required data-test="spend-template" /></label>
                                <template v-if="selected">
                                    <label class="block min-w-0"><span class="mb-1 block text-xs font-medium text-gray-700">{{ __('Payment source') }} <span class="text-red-500">*</span></span><AutocompleteSelect v-model="modeOfPayment" :options="paymentOptions" :placeholder="__('Payment source')" required data-test="spend-payment-source" /></label>
                                    <label v-if="!isJE" class="block min-w-0"><span class="mb-1 block text-xs font-medium text-gray-700">{{ __('Supplier') }} <span class="text-red-500">*</span></span><AutocompleteSelect v-if="selected.allow_supplier_change" v-model="supplier" :options="supplierOptions" :loading="supplierLoading" :placeholder="__('Search supplier...')" required data-test="spend-supplier" @search="searchSuppliers" /><p v-else class="spend-input bg-gray-50">{{ selected.default_supplier_name || supplier }}</p></label>
                                    <label class="block min-w-0"><span class="mb-1 block text-xs font-medium text-gray-700">{{ __('Store or vendor name') }}<span v-if="selected.require_vendor_name" class="text-red-500"> *</span></span><input v-model="vendorName" :required="!!selected.require_vendor_name" class="spend-input" /></label>
                                    <label class="block min-w-0"><span class="mb-1 block text-xs font-medium text-gray-700">{{ __('Bill No') }}</span><input v-model="billNo" class="spend-input" /></label>
                                    <label class="block min-w-0"><span class="mb-1 block text-xs font-medium text-gray-700">{{ __('Bill Date') }}</span><input v-model="billDate" type="date" class="spend-input" /></label>
                                </template>
                            </div>
                            <div class="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-gray-100 pt-3 text-xs text-gray-500"><span>{{ __('Company') }}: <b class="font-medium text-gray-700">{{ company }}</b></span><span v-if="shiftSource" class="inline-flex rounded-full bg-amber-50 px-2 py-0.5 font-medium text-amber-800 ring-1 ring-inset ring-amber-200" data-test="spend-shift-source">{{ __('Shift drawer') }}</span></div>
                        </section>
                        <p v-if="!selected" class="text-sm text-gray-600">{{ __('Choose a template to see the required details.') }}</p>
                        <template v-else>
                            <section class="min-w-0 rounded-xl border border-gray-200 bg-white">
                                <div class="px-4 pb-2 pt-4"><h3 class="text-xs font-semibold uppercase tracking-wide text-gray-500">{{ isJE ? __('Expense lines') : __('Items') }} <span class="text-red-500">*</span></h3></div>
                                <div class="spend-grid-header hidden items-center gap-x-3 border-y border-gray-200 bg-gray-50 px-3 py-2 text-xs font-medium text-gray-500 xl:grid" :class="isJE ? 'spend-grid-expense' : 'spend-grid-purchase'"><span>{{ __('No.') }}</span><span>{{ isJE ? __('Description') : __('Item') }}</span><template v-if="!isJE"><span>{{ __('Qty') }}</span><span>{{ __('Unit price') }}</span><span class="text-end">{{ __('Subtotal') }}</span></template><span v-else class="text-end">{{ __('Amount') }}</span><span /></div>
                                <div class="p-2 xl:p-0">
                                    <div v-for="(line, index) in lines" :key="line.id" class="spend-grid-row mb-3 grid min-w-0 grid-cols-2 items-center gap-x-3 gap-y-2.5 rounded-lg border border-gray-200 bg-white p-3.5 last:mb-0 xl:mb-0 xl:gap-y-0 xl:rounded-none xl:border-0 xl:border-b xl:border-gray-100 xl:px-3 xl:py-1.5" :class="isJE ? 'spend-grid-expense' : 'spend-grid-purchase'">
                                        <span class="col-span-2 border-b border-gray-100 pb-2 text-xs font-semibold text-gray-600 xl:col-span-1 xl:border-0 xl:pb-0 xl:font-normal">{{ index + 1 }}</span>
                                        <label v-if="isJE" class="col-span-2 block min-w-0 xl:col-span-1"><span class="mb-1 block text-xs text-gray-500 xl:sr-only">{{ __('Description') }}</span><input v-model="line.description" required class="spend-cell" :aria-label="__('Description')" data-test="spend-description" /></label>
                                        <div v-else class="col-span-2 min-w-0 xl:col-span-1"><span class="mb-1 block text-xs text-gray-500 xl:hidden">{{ __('Item') }}</span>
                                            <button v-if="line.item_code && !line.editing" type="button" class="flex min-h-11 w-full min-w-0 items-center justify-between rounded-md border border-gray-200 bg-gray-50 px-2.5 py-1.5 text-start" :title="__('Click to change item')" @click="line.editing = true"><span class="min-w-0"><span class="block truncate text-sm font-medium text-gray-900">{{ itemName(line.item_code) }}</span><span class="block truncate text-xs text-gray-500">{{ line.item_code }} · {{ itemUnit(line.item_code) }}</span></span><FeatherIcon name="edit-2" class="ms-2 h-3.5 w-3.5 shrink-0 text-gray-500" /></button>
                                            <label v-else class="block min-w-0"><span class="sr-only">{{ __('Item') }}</span><AutocompleteSelect :model-value="line.item_code" :options="itemOptions" :placeholder="__('Search item by name or code...')" required data-test="spend-item" @update:model-value="(code) => { line.item_code = code; line.editing = false }" /></label>
                                        </div>
                                        <template v-if="!isJE"><label class="block min-w-0"><span class="mb-1 block text-xs text-gray-500 xl:sr-only">{{ __('Qty') }}</span><input v-model.number="line.qty" type="number" inputmode="decimal" min="0.000001" step="any" required class="spend-cell" :aria-label="__('Qty')" data-test="spend-qty" /></label><label class="block min-w-0"><span class="mb-1 block text-xs text-gray-500 xl:sr-only">{{ __('Unit price') }}</span><input v-model.number="line.rate" type="number" inputmode="decimal" min="0.01" step="any" required class="spend-cell" :aria-label="__('Unit price')" data-test="spend-rate" /></label><p class="text-end text-sm tabular-nums text-gray-900" data-test="spend-subtotal"><span class="me-2 text-xs text-gray-500 xl:hidden">{{ __('Subtotal') }}</span>{{ money(Number(line.qty || 0) * Number(line.rate || 0)) }}</p></template>
                                        <label v-else class="block min-w-0"><span class="mb-1 block text-xs text-gray-500 xl:sr-only">{{ __('Amount') }}</span><input v-model.number="line.amount" type="number" inputmode="decimal" min="0.01" step="any" required class="spend-cell text-end" :aria-label="__('Amount')" data-test="spend-amount" /></label>
                                        <button type="button" class="flex h-11 min-w-0 items-center justify-center gap-1.5 rounded-md border border-gray-200 text-sm text-gray-500 hover:bg-red-50 hover:text-red-600 xl:border-0" :disabled="lines.length === 1" :aria-label="__('Remove row')" @click="lines.splice(index, 1)"><FeatherIcon name="trash-2" class="h-4 w-4" /><span class="xl:hidden">{{ __('Remove') }}</span></button>
                                    </div>
                                </div>
                                <div class="rounded-b-xl border-t border-gray-200 bg-gray-50/40 px-4 py-2.5"><Button type="button" variant="subtle" class="h-11" @click="addLine"><span class="inline-flex items-center gap-2"><FeatherIcon name="plus" class="h-4 w-4" />{{ __('Add Row') }}</span></Button></div>
                            </section>
                            <div class="grid min-w-0 gap-4 lg:grid-cols-[minmax(0,1fr)_18rem]">
                                <section class="min-w-0 rounded-xl border border-gray-200 bg-white p-4" data-test="spend-notes"><h3 class="mb-3 text-xs font-semibold uppercase tracking-wide text-gray-500">{{ __('Notes & Attachments') }}</h3><label class="block"><span class="sr-only">{{ __('Remarks') }}</span><textarea v-model="remarks" rows="2" :placeholder="__('Remarks')" class="spend-input mb-3 resize-y" /></label><p v-if="selected.requires_attachment" class="mb-3 text-xs text-gray-600">{{ __('An attachment is required for this spend template.') }}</p><PurchaseAttachments :pending="files" editable @add="addFiles" @remove="removeFile" /></section>
                                <section class="rounded-xl border border-gray-200 bg-gray-50 p-4" data-test="spend-summary"><h3 class="mb-3 text-xs font-semibold uppercase tracking-wide text-gray-500">{{ __('Summary') }}</h3><dl class="space-y-2 text-sm"><div class="flex justify-between gap-3"><dt class="text-gray-500">{{ isJE ? __('Expense lines') : __('Total Items') }}</dt><dd class="font-semibold tabular-nums text-gray-900">{{ lines.filter((line) => isJE ? line.description || Number(line.amount) : line.item_code).length }}</dd></div><div class="flex justify-between gap-3 border-t border-gray-200 pt-2"><dt class="font-medium text-gray-700">{{ __('Total') }}</dt><dd class="text-end font-semibold tabular-nums text-gray-900" data-test="spend-total">{{ money(total) }}</dd></div></dl></section>
                            </div>
                        </template>
                    </fieldset>
                </form>
            </template>
            <template v-else-if="tab === 'detail'">
                <div data-test="spend-detail" class="flex flex-col gap-4">
                    <div class="flex flex-wrap items-center gap-3">
                        <button type="button" class="pos-icon-btn" :aria-label="__('Back')" @click="closeDetail"><FeatherIcon name="arrow-left" class="h-5 w-5" /></button>
                        <div class="min-w-0 flex-1"><h2 class="break-words text-lg font-semibold text-gray-900">{{ detail?.name || __('Purchases & Expenses') }}</h2><p class="text-sm text-gray-600">{{ detail?.template }}</p></div>
                        <span v-if="detail" class="inline-flex whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset" :class="statusTone(detail)">{{ __(detail.status) }}</span>
                        <Button v-if="detail?.can_cancel" :disabled="isOffline || busy || detailLoading" class="h-11" data-test="spend-cancel" @click="cancelRow = detail; cancelReason = ''">{{ __('Cancel') }}</Button>
                    </div>
                    <p v-if="detailLoading" role="status" class="py-8 text-center text-sm text-gray-600">{{ __('Loading...') }}</p>
                    <template v-else-if="detail">
                        <dl class="grid gap-x-8 gap-y-4 rounded-xl border border-gray-200 bg-white p-4 sm:grid-cols-2 lg:grid-cols-3">
                            <div><dt class="text-xs text-gray-600">{{ __('Supplier') }}</dt><dd class="mt-1 text-sm font-medium text-gray-900">{{ detail.supplier_name || detail.vendor_name || '—' }}</dd></div>
                            <div><dt class="text-xs text-gray-600">{{ __('Date') }}</dt><dd class="mt-1 text-sm text-gray-900">{{ detail.posting_date }}</dd></div>
                            <div><dt class="text-xs text-gray-600">{{ __('Payment source') }}</dt><dd class="mt-1 text-sm text-gray-900">{{ detail.mode_of_payment || '—' }}</dd></div>
                            <div><dt class="text-xs text-gray-600">{{ __('Bill No') }}</dt><dd class="mt-1 text-sm text-gray-900">{{ detail.bill_no || '—' }}</dd></div>
                            <div v-if="detail.shift"><dt class="text-xs text-gray-600">{{ __('Shift') }}</dt><dd class="mt-1 break-words text-sm text-gray-900">{{ detail.shift }}</dd></div>
                        </dl>
                        <section class="rounded-xl border border-gray-200 bg-white">
                            <h3 class="border-b border-gray-200 px-4 py-3 text-sm font-semibold text-gray-900">{{ detail.doctype === 'Purchase Invoice' ? __('Items') : __('Expense lines') }}</h3>
                            <div v-for="(line, index) in detail.items" :key="index" class="flex items-start justify-between gap-4 border-b border-gray-100 px-4 py-3">
                                <div class="min-w-0"><p class="break-words text-sm font-medium text-gray-900">{{ line.item_name || line.description }}</p><p v-if="line.item_code" class="mt-1 text-xs text-gray-600">{{ line.item_code }} · {{ line.qty }} {{ line.uom }} × {{ money(line.rate) }}</p></div>
                                <p class="shrink-0 text-sm tabular-nums text-gray-900">{{ money(line.amount) }}</p>
                            </div>
                            <div class="flex justify-end gap-8 rounded-b-xl bg-gray-50 px-4 py-3 text-sm font-semibold text-gray-900"><span>{{ __('Total') }}</span><span class="tabular-nums">{{ money(detail.amount) }}</span></div>
                        </section>
                        <section v-if="detail.remarks" class="text-sm text-gray-700"><h3 class="mb-2 font-semibold text-gray-900">{{ __('Remarks') }}</h3><p class="whitespace-pre-wrap">{{ detail.remarks }}</p></section>
                        <section v-if="detail.attachments?.length" class="rounded-xl border border-gray-200 bg-white p-4"><h3 class="mb-3 text-sm font-semibold text-gray-900">{{ __('Attachments') }}</h3><PurchaseAttachments :attached="detail.attachments" /></section>
                    </template>
                    <form v-if="cancelRow" class="flex flex-col gap-3 rounded-xl border border-red-200 bg-red-50 p-4" data-test="spend-cancel-form" @submit.prevent="cancelSpend">
                        <h3 class="font-semibold text-gray-900">{{ __('Cancel') }} {{ cancelRow.name }}</h3>
                        <label class="spend-label">{{ __('Cancellation reason') }}<textarea v-model="cancelReason" required :disabled="busy" class="spend-input" data-test="spend-cancel-reason" /></label>
                        <div class="flex gap-2"><Button type="button" :disabled="busy" class="h-11" @click="cancelRow = null">{{ __('Back') }}</Button><Button type="submit" variant="solid" theme="red" :disabled="isOffline || busy || !cancelReason.trim()" class="h-11" data-test="spend-cancel-confirm">{{ __('Confirm cancellation') }}</Button></div>
                    </form>
                </div>
            </template>
            <template v-else>
                <div class="flex min-w-0 flex-wrap items-center gap-2">
                    <input v-model="historySearch" class="spend-input !w-auto min-w-0 flex-[1_1_12rem]" :placeholder="__('Search recent transactions...')" :aria-label="__('Search recent transactions')" />
                    <RefreshButton :loading="historyLoading" :disabled="isOffline || busy" data-test="spend-history-tab" @click="loadHistory" />
                    <Button variant="solid" :disabled="isOffline || busy || loading || !company || !posProfile" class="h-11 shrink-0 whitespace-nowrap" data-test="spend-new-tab" @click="openNew"><span class="inline-flex items-center gap-2"><FeatherIcon name="plus" class="h-4 w-4" />{{ __('New') }}</span></Button>
                </div>
                <p v-if="historyLoading" role="status" class="py-8 text-center text-sm text-gray-600">{{ __('Loading...') }}</p>
                <p v-else-if="!history.length" class="py-8 text-center text-sm text-gray-600">{{ __('No purchases or expenses recorded yet.') }}</p>
                <p v-if="history.length && !filteredHistory.length" class="py-8 text-center text-sm text-gray-600">{{ __('No matching transactions.') }}</p>
                <div v-if="filteredHistory.length" class="rounded-xl border border-gray-200 bg-white">
                    <div class="hidden gap-4 border-b border-gray-200 bg-gray-50 px-4 py-2 text-xs font-medium text-gray-600 xl:grid xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1.5fr)_7rem_8rem_7rem_1.5rem]">
                        <span>{{ __('ID') }}</span><span>{{ __('Spend template') }}</span><span>{{ __('Date') }}</span><span class="text-end">{{ __('Amount') }}</span><span>{{ __('Status') }}</span><span />
                    </div>
                    <button v-for="row in filteredHistory" :key="`${row.doctype}:${row.name}`" type="button" class="grid w-full grid-cols-2 items-center gap-x-4 gap-y-2 border-b border-gray-100 px-4 py-3 text-start transition-colors last:rounded-b-xl last:border-0 hover:bg-gray-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1.5fr)_7rem_8rem_7rem_1.5rem]" data-test="spend-history-row" @click="openDetail(row)">
                        <div class="order-1 min-w-0"><p class="truncate text-sm font-semibold text-gray-900">{{ row.name }}</p><p class="mt-0.5 text-xs text-gray-600">{{ __(row.doctype) }}</p></div>
                        <div class="order-3 col-span-2 min-w-0 xl:order-2 xl:col-span-1"><p class="truncate text-sm text-gray-900">{{ row.template }}</p><p class="mt-0.5 truncate text-xs text-gray-600">{{ row.vendor_name || row.mode_of_payment }}<span v-if="row.bill_no"> · {{ row.bill_no }}</span><span v-if="row.attachments?.length" class="ms-2 inline-flex items-center gap-1"><FeatherIcon name="paperclip" class="h-3 w-3" />{{ row.attachments.length }}</span></p></div>
                        <p class="order-4 text-sm text-gray-600 xl:order-3">{{ row.posting_date }}</p>
                        <p class="order-5 text-end text-sm tabular-nums text-gray-900 xl:order-4">{{ money(row.amount) }}</p>
                        <div class="order-2 text-end xl:order-5 xl:text-start"><span class="inline-flex whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset" :class="statusTone(row)">{{ __(row.status) }}</span></div>
                        <FeatherIcon name="chevron-right" class="order-6 hidden h-4 w-4 justify-self-end text-gray-500 xl:block" />
                    </button>
                </div>
                <p v-if="history.length >= 50" class="text-sm text-gray-600">{{ __('Showing the 50 most recent transactions.') }}</p>
			</template>
		</div>
	</div>
<ConfirmDialog v-model="discardOpen" :title="__('Discard changes?')" :message="__('The unsaved spending details will be discarded.')" :confirm-label="__('Discard')" destructive @confirm="discardForm" />
</template>

<script setup>
import { Button, FeatherIcon } from "frappe-ui";
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import AutocompleteSelect from "@/components/common/AutocompleteSelect.vue";
import ConfirmDialog from "@/components/common/ConfirmDialog.vue";
import RefreshButton from "@/components/common/RefreshButton.vue";
import PurchaseAttachments from "@/components/purchase/PurchaseAttachments.vue";
import { call } from "@/utils/apiWrapper";
import { fileToBase64, sanitizePickedFiles } from "@/utils/attachments";
import { compressImage } from "@/utils/compressImage";
import { humanizeError } from "@/utils/errorHandler";
import { generateUUID } from "@/utils/offline/uuid";
import { __ } from "@/utils/translation";

const props = defineProps({
	company: { type: String, default: null },
	posProfile: { type: String, default: null },
	isOffline: { type: Boolean, default: false },
});
const API = "pos_next.api.spend";
const tab = ref("history");
const historySearch = ref("");
const detail = ref(null);
const detailLoading = ref(false);
let detailVersion = 0;
function closeDetail() { ++detailVersion; detail.value = null; cancelRow.value = null; tab.value = "history"; }
async function openDetail(row) {
 const version = ++detailVersion; tab.value = "detail"; detail.value = null; detailLoading.value = true; error.value = "";
 try { const data = await call(`${API}.get_spend_detail`, { doctype: row.doctype, name: row.name }); if (version === detailVersion) detail.value = data; }
 catch (err) { if (version === detailVersion) error.value = message(err); }
 finally { if (version === detailVersion) detailLoading.value = false; }
}
const discardOpen = ref(false);
const discardDestination = ref("history");
const supplierOptions = ref([]);
const supplierLoading = ref(false);
let supplierTimer;
let supplierVersion = 0;
const templates = ref([]);
const loading = ref(false);
const busy = ref(false);
const error = ref("");
const success = ref("");
const templateName = ref("");
const modeOfPayment = ref("");
const supplier = ref("");
const vendorName = ref("");
const billNo = ref("");
const billDate = ref("");
const remarks = ref("");
const lines = ref([]);
const files = ref([]);
const requestId = ref(generateUUID());
// A lost response can mean success: retries must send the original transaction.
const attempt = defineModel("attempt", { default: null });
const history = ref([]);
const historyLoading = ref(false);
const cancelRow = ref(null);
const cancelReason = ref("");
let scopeVersion = 0;
let historyVersion = 0;
const selected = computed(() =>
	templates.value.find((t) => t.name === templateName.value),
);
const isJE = computed(() => selected.value?.document_type === "Journal Entry");
const shiftSource = computed(
	() =>
		selected.value?.payment_sources?.find(
			(s) => s.mode_of_payment === modeOfPayment.value,
		)?.is_shift_source,
);
const templateOptions = computed(() => templates.value.map((template) => ({ value: template.name, label: template.title || template.name, subtitle: template.category || __("Other") })));
const paymentOptions = computed(() => (selected.value?.payment_sources || []).map((source) => ({ value: source.mode_of_payment, label: source.mode_of_payment })));
const itemName = (code) => selected.value?.item_details?.find((item) => item.item_code === code)?.item_name || code;
const itemOptions = computed(() =>
	(selected.value?.items || []).map((code) => {
		const detail = selected.value?.item_details?.find(
			(item) => item.item_code === code,
		);
		return {
			value: code,
			label: detail?.item_name || code,
			subtitle: [code, detail?.stock_uom].filter(Boolean).join(" · "),
		};
	}),
);
const itemUnit = (code) =>
	selected.value?.item_details?.find((item) => item.item_code === code)
		?.stock_uom || "";
const filteredHistory = computed(() => {
	const term = historySearch.value.trim().toLowerCase();
	return history.value.filter((row) =>
		[
			row.name,
			row.template,
			row.vendor_name,
			row.bill_no,
			row.mode_of_payment,
		].some((value) =>
			String(value || "")
				.toLowerCase()
				.includes(term),
		),
	);
});
function openNew() {
	if (!attempt.value && !busy.value) {
		resetForm();
		tab.value = "form";
	}
}
function leaveForm() {
	if (attempt.value || busy.value) return;
	if (templateName.value || files.value.length) {
		discardDestination.value = "history";
		discardOpen.value = true;
	} else tab.value = "history";
}
function confirmReset() {
	if (!attempt.value && !busy.value) {
		discardDestination.value = "form";
		discardOpen.value = true;
	}
}
function discardForm() {
	if (attempt.value || busy.value) return;
	resetForm();
	tab.value = discardDestination.value;
}
function searchSuppliers(term = "") {
	clearTimeout(supplierTimer);
	const version = ++supplierVersion;
	if (
		props.isOffline ||
		attempt.value ||
		!selected.value?.allow_supplier_change
	)
		return;
	supplierTimer = setTimeout(async () => {
		supplierLoading.value = true;
		try {
			const rows = await call(`${API}.search_spend_suppliers`, {
				company: props.company,
				pos_profile: props.posProfile,
				template: templateName.value,
				search_term: term,
			});
			if (version === supplierVersion) {
				const current = supplierOptions.value.find(
					(option) => option.value === supplier.value,
				);
				supplierOptions.value = (rows || []).map((row) => ({
					value: row.name,
					label: row.supplier_name || row.name,
					subtitle: row.name,
				}));
				if (
					current &&
					!supplierOptions.value.some(
						(option) => option.value === current.value,
					)
				)
					supplierOptions.value.unshift(current);
			}
		} catch (err) {
			if (version === supplierVersion) error.value = message(err);
		} finally {
			if (version === supplierVersion) supplierLoading.value = false;
		}
	}, 300);
}
onBeforeUnmount(() => {
	clearTimeout(supplierTimer);
	++supplierVersion;
});
const total = computed(() =>
	lines.value.reduce(
		(sum, line) =>
			sum +
			(isJE.value
				? Number(line.amount || 0)
				: Number(line.qty || 0) * Number(line.rate || 0)),
		0,
	),
);
const money = (value) =>
	`Rp ${new Intl.NumberFormat("id-ID", { maximumFractionDigits: 2 }).format(value || 0)}`;
const message = (err) =>
	humanizeError(err)?.message ||
	__("Could not complete the spending request. Please try again.");

function addLine() {
	lines.value.push({
		id: generateUUID(),
		description: "",
		amount: "",
		item_code: "",
		qty: 1,
		rate: "",
	});
}
function resetForm() {
	if (attempt.value || busy.value) return;
	requestId.value = generateUUID();
	attempt.value = null;
	templateName.value = "";
	modeOfPayment.value = "";
	supplier.value = "";
	vendorName.value = "";
	billNo.value = "";
	billDate.value = "";
	remarks.value = "";
	files.value = [];
	lines.value = [];
	error.value = "";
}
watch(templateName, () => {
	clearTimeout(supplierTimer);
	++supplierVersion;
	supplierLoading.value = false;
	lines.value = [];
	addLine();
	modeOfPayment.value =
		selected.value?.payment_sources?.find((s) => s.is_default)
			?.mode_of_payment ||
		selected.value?.payment_sources?.[0]?.mode_of_payment ||
		"";
	supplier.value = selected.value?.default_supplier || "";
	supplierOptions.value = supplier.value
		? [
				{
					value: supplier.value,
					label: selected.value.default_supplier_name || supplier.value,
					subtitle: supplier.value,
				},
			]
		: [];
	searchSuppliers();
});
watch(
	() => [props.company, props.posProfile],
	async () => {
		const version = ++scopeVersion;
			++detailVersion; detail.value = null; if (tab.value === "detail") tab.value = "history";
		clearTimeout(supplierTimer);
		++supplierVersion;
		++historyVersion;
		if (!attempt.value) resetForm();
		success.value = "";
		templates.value = [];
		history.value = [];
		cancelRow.value = null;
		historyLoading.value = false;
		loading.value = false;
		if (!props.company || !props.posProfile) return;
		loading.value = true;
		try {
			const rows = await call(`${API}.get_spend_templates`, {
				company: props.company,
				pos_profile: props.posProfile,
			});
			if (version === scopeVersion) {
				templates.value = rows || [];
				if (attempt.value) {
					tab.value = "form";
					templateName.value = attempt.value.template;
					await nextTick();
					modeOfPayment.value = attempt.value.mode_of_payment;
					supplier.value = attempt.value.supplier;
					vendorName.value = attempt.value.vendor_name;
					billNo.value = attempt.value.bill_no;
					billDate.value = attempt.value.bill_date || "";
					remarks.value = attempt.value.remarks;
					lines.value = attempt.value.lines.map((line) => ({
						...line,
						id: generateUUID(),
					}));
				}
			}
		} catch (err) {
			if (version === scopeVersion) error.value = message(err);
		} finally {
			if (version === scopeVersion) loading.value = false;
		}
		if (version === scopeVersion && tab.value === "history") loadHistory();
	},
	{ immediate: true },
);

function addFiles(picked) {
	if (busy.value || attempt.value) return;
	const { ok, tooBig } = sanitizePickedFiles(picked);
	files.value = [...files.value, ...ok];
	if (tooBig.length) error.value = __("Attachments must be 10 MB or smaller.");
}
function removeFile(file) {
	if (!busy.value && !attempt.value)
		files.value = files.value.filter((picked) => picked !== file);
}
async function submit() {
	if (
		props.isOffline ||
		busy.value ||
		!selected.value ||
		!props.company ||
		!props.posProfile
	)
		return;
	if (
		!attempt.value &&
		selected.value.requires_attachment &&
		!files.value.length
	) {
		error.value = __("An attachment is required for this spend template.");
		return;
	}
	const version = scopeVersion;
	busy.value = true;
	error.value = "";
	success.value = "";
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
			};
			for (const file of files.value) {
				const compressed = await compressImage(file);
				data.files.push({
					file_name: compressed.name,
					filedata: await fileToBase64(compressed),
				});
			}
			if (version !== scopeVersion) return;
			attempt.value = data;
			await nextTick();
		}
		const result = await call(`${API}.submit_spend`, {
			data: JSON.stringify(attempt.value),
		});
		if (version !== scopeVersion) return;
		attempt.value = null;
		busy.value = false;
		resetForm();
		tab.value = "history";
		success.value = __("Spending recorded: {0}", [result.name]);
		if (tab.value === "history") loadHistory();
	} catch (err) {
		if (version === scopeVersion) error.value = message(err);
	} finally {
		busy.value = false;
	}
}
async function showHistory() {
	tab.value = "history";
	await loadHistory();
}
async function loadHistory() {
	const version = ++historyVersion;
	if (props.isOffline || !props.company) return;
	historyLoading.value = true;
	try {
		const rows = await call(`${API}.get_spend_history`, {
			company: props.company,
			pos_profile: props.posProfile,
		});
		if (version === historyVersion) history.value = rows || [];
	} catch (err) {
		if (version === historyVersion) error.value = message(err);
	} finally {
		if (version === historyVersion) historyLoading.value = false;
	}
}
async function cancelSpend() {
	if (
		props.isOffline ||
		busy.value ||
		!cancelRow.value?.can_cancel ||
		!cancelReason.value.trim()
	)
		return;
	const version = scopeVersion;
	busy.value = true;
	error.value = "";
	try {
		await call(`${API}.cancel_spend`, {
			doctype: cancelRow.value.doctype,
			name: cancelRow.value.name,
			reason: cancelReason.value.trim(),
		});
		if (version !== scopeVersion) return;
		cancelRow.value = null;
		if (detail.value) await openDetail(detail.value);
		await loadHistory();
	} catch (err) {
		if (version === scopeVersion) error.value = message(err);
	} finally {
		busy.value = false;
	}
}
function statusTone(row) {
	if (row.docstatus === 2) return "bg-gray-100 text-gray-700 ring-gray-200";
	if (row.docstatus === 0)
		return "bg-orange-50 text-orange-800 ring-orange-200";
	return "bg-green-50 text-green-800 ring-green-200";
}
</script>

<style scoped>
@media (min-width: 1280px) {
 .spend-grid-purchase { grid-template-columns: 1.75rem minmax(0, 3fr) minmax(0, 1fr) minmax(0, 1.2fr) minmax(0, 1.2fr) 2.75rem; }
 .spend-grid-expense { grid-template-columns: 1.75rem minmax(0, 1fr) 10rem 2.75rem; }
}
.spend-cell { @apply min-h-11 w-full min-w-0 rounded-md border border-gray-300 bg-gray-50 px-2 py-1 text-sm tabular-nums focus:border-blue-500 focus:bg-white focus:ring-2 focus:ring-blue-500/30; }

.spend-label { @apply flex flex-col gap-1.5 text-sm font-medium text-gray-700; }
.spend-label :deep(.select-input) { min-height: 44px; }
.spend-input { @apply min-h-11 w-full min-w-0 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:border-gray-500 focus:ring-1 focus:ring-gray-500 disabled:bg-gray-50; }

</style>
