<template>
	<DialogHost
		v-model:show="show"
		:embedded="embedded"
		:options="{ title: dialogTitle, size: '4xl' }"
	>
		<template #body-content>
			<!-- LIST -->
			<div v-if="view === 'list'" class="mx-auto flex w-full max-w-6xl flex-col gap-3">
				<div class="flex flex-wrap items-center justify-between gap-3">
					<p class="text-sm text-gray-500">
						{{ __("Count physical stock and send it to Head Office for approval") }}
					</p>
					<div class="flex items-center gap-2">
						<RefreshButton :loading="loading" @click="loadList()" />
						<Button v-if="openOpname" variant="solid" @click="openEditor(openOpname.name)">
							<template #prefix>
								<FeatherIcon name="edit-2" class="h-4 w-4" />
							</template>
							{{ __("Continue") }}
						</Button>
						<Button v-else-if="!hasOpenOpname" variant="solid" @click="openNew">
							<template #prefix>
								<FeatherIcon name="plus" class="h-4 w-4" />
							</template>
							{{ __("New Count") }}
						</Button>
					</div>
				</div>

				<div class="rounded-xl border border-gray-200 bg-white">
					<div
						class="hidden border-b border-gray-200 bg-gray-50 px-4 py-2 text-xs font-medium text-gray-500 md:grid md:grid-cols-[minmax(0,1.4fr)_7rem_6rem_12rem] md:gap-x-3"
					>
						<span>{{ __("Document") }}</span>
						<span>{{ __("Date") }}</span>
						<span class="text-end">{{ __("Items") }}</span>
						<span>{{ __("Status") }}</span>
					</div>

					<div v-if="loading && !opnames.length" class="divide-y divide-gray-100">
						<span class="sr-only">{{ __("Loading...") }}</span>
						<div v-for="n in 3" :key="n" class="flex animate-pulse gap-4 px-4 py-3">
							<div class="h-4 w-28 rounded bg-gray-100"></div>
							<div class="h-4 flex-1 rounded bg-gray-100"></div>
							<div class="h-4 w-20 rounded bg-gray-100"></div>
						</div>
					</div>
					<div v-else-if="!opnames.length" class="px-4 py-8 text-center">
						<p class="text-sm font-medium text-gray-900">{{ __("No stock opname yet") }}</p>
						<p class="mt-1 text-xs text-gray-500">{{ __("Start one with New Count") }}</p>
					</div>
					<div v-else class="divide-y divide-gray-100">
						<button
							v-for="o in opnames"
							:key="o.name"
							type="button"
							class="grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 px-4 py-3 text-start transition-colors hover:bg-gray-50 focus-visible:bg-gray-50 focus-visible:outline-none md:grid-cols-[minmax(0,1.4fr)_7rem_6rem_12rem] md:gap-y-0"
							@click="openEditor(o.name)"
						>
							<span class="truncate text-sm font-semibold text-gray-900">{{ o.name }}</span>
							<span class="justify-self-end md:order-last md:justify-self-start">
								<span :class="statusPill(stateLabel(o))">{{ __(stateLabel(o)) }}</span>
							</span>
							<span class="text-xs text-gray-500 md:text-sm md:text-gray-700">
								{{ formatDate(o.posting_date) }}
							</span>
							<span class="col-span-2 text-sm tabular-nums text-gray-900 md:col-span-1 md:text-end">
								{{ o.item_count }}
							</span>
							<span
								v-if="stateLabel(o) === 'Rejected' && o.reject_reason"
								class="col-span-2 text-xs text-red-600 md:col-span-4"
							>
								{{ o.reject_reason }}
							</span>
						</button>
					</div>
				</div>
			</div>

			<!-- EDITOR -->
			<div v-else class="mx-auto flex w-full max-w-6xl flex-col gap-4">
				<div v-if="loadingDetail" class="py-10 text-center text-sm text-gray-500">
					{{ __("Loading...") }}
				</div>
				<template v-else-if="editor">
					<div class="flex flex-wrap items-center gap-2">
						<button
							type="button"
							class="-ms-1 rounded p-1 text-gray-500 hover:text-gray-900"
							:aria-label="__('Back')"
							:title="__('Back')"
							@click="backToList"
						>
							<FeatherIcon name="arrow-left" class="h-4 w-4" />
						</button>
						<h3 class="truncate text-base font-semibold text-gray-900">
							{{ editor.name || __("New Count") }}
						</h3>
						<span :class="statusPill(stateLabel(editor))">{{ __(stateLabel(editor)) }}</span>
					</div>

					<p
						v-if="stateLabel(editor) === 'Rejected' && editor.reject_reason"
						class="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700"
					>
						{{ __("Rejected by Head Office") }}: {{ editor.reject_reason }}
					</p>

					<!-- read-only for pending / approved / cancelled -->
					<section
						v-if="!editable"
						class="rounded-xl border border-gray-200 bg-white"
					>
						<h4 class="px-4 pb-2 pt-4 text-sm font-semibold text-gray-900">{{ __("Items") }}</h4>
						<div
							class="grid grid-cols-[minmax(0,1fr)_5rem] gap-3 border-y border-gray-200 bg-gray-50 px-4 py-2 text-xs font-medium text-gray-500"
						>
							<span>{{ __("Item") }}</span>
							<span class="text-end">{{ __("Qty") }}</span>
						</div>
						<div
							v-for="(row, idx) in editor.items"
							:key="idx"
							class="grid grid-cols-[minmax(0,1fr)_5rem] items-center gap-3 border-b border-gray-100 px-4 py-2 text-sm last:border-b-0"
						>
							<span class="truncate text-gray-900">
								{{ row.item_name || row.item_code }}
								<span v-if="row.batch_no" class="text-xs text-gray-400"> — {{ row.batch_no }}</span>
							</span>
							<span class="text-end tabular-nums">{{ row.is_serial ? row.serial_nos.length : row.qty }}</span>
						</div>
						<p v-if="!editor.items.length" class="px-4 py-4 text-center text-sm text-gray-500">
							{{ __("No items") }}
						</p>
					</section>

					<!-- editable -->
					<template v-else>
						<section
							class="flex flex-wrap items-end gap-4 rounded-xl border border-gray-200 bg-white px-4 py-3"
						>
							<div>
								<label for="so-posting-date" class="mb-1 block text-xs font-medium text-gray-700">
									{{ __("Posting Date") }}
								</label>
								<input
									id="so-posting-date"
									v-model="editor.posting_date"
									type="date"
									data-testid="posting-date-input"
									class="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30"
								/>
							</div>
							<div>
								<label for="so-posting-time" class="mb-1 block text-xs font-medium text-gray-700">
									{{ __("Time") }}
								</label>
								<input
									id="so-posting-time"
									v-model="editor.posting_time"
									type="time"
									data-testid="posting-time-input"
									class="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30"
								/>
							</div>
						</section>

						<section class="rounded-xl border border-gray-200 bg-white">
							<h4 class="px-4 pb-2 pt-4 text-sm font-semibold text-gray-900">{{ __("Counted Items") }}</h4>

							<div
								v-for="(row, idx) in editor.items"
								:key="idx"
								class="border-b border-gray-100 px-4 py-2 last:border-b-0"
							>
								<div class="flex items-center gap-2">
									<span class="hidden w-5 text-xs tabular-nums text-gray-400 md:block">{{ idx + 1 }}</span>
									<div class="min-w-0 flex-1">
										<p class="truncate text-sm font-medium text-gray-900">{{ row.item_name || row.item_code }}</p>
										<p class="truncate text-xs text-gray-400">
											<span v-if="row.item_code !== row.item_name">{{ row.item_code }}</span>
											<span v-if="row.batch_no" class="text-gray-500">{{ __("Batch") }}: {{ row.batch_no }}</span>
										</p>
									</div>
									<!-- serial qty is the number of ticked serials, never typed -->
									<span
										v-if="row.is_serial"
										data-testid="item-qty"
										class="w-16 text-end text-sm tabular-nums text-gray-900"
									>
										{{ row.serial_nos.length }}
									</span>
									<input
										v-else
										v-model.number="row.qty"
										type="number"
										min="0"
										step="any"
										inputmode="decimal"
										data-testid="qty-input"
										:aria-label="`${row.item_name || row.item_code} — ${__('Qty')}`"
										class="w-20 rounded-md border border-gray-300 px-2 py-1.5 text-sm tabular-nums focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30"
									/>
									<button
										type="button"
										class="flex h-7 w-7 shrink-0 items-center justify-center rounded text-gray-400 hover:bg-red-50 hover:text-red-600"
										:aria-label="__('Remove item')"
										:title="__('Remove item')"
										@click="editor.items.splice(idx, 1)"
									>
										<FeatherIcon name="x" class="h-4 w-4" />
									</button>
								</div>

								<!-- serial tick list: tick the serials physically present -->
								<div v-if="row.is_serial" class="mt-2 md:ms-7">
									<ul class="flex flex-wrap gap-x-4 gap-y-1">
										<li v-for="s in row.serial_options" :key="s">
											<label class="flex items-center gap-1.5 text-sm text-gray-700">
												<input
													type="checkbox"
													data-testid="serial-option"
													:checked="row.serial_nos.includes(s)"
													@change="toggleSerial(row, s)"
												/>
												{{ s }}
											</label>
										</li>
									</ul>
									<input
										type="text"
										:placeholder="__('Scan serial...')"
										:aria-label="__('Scan serial')"
										class="mt-1 w-full max-w-xs rounded-md border border-dashed border-gray-300 px-2 py-1.5 text-sm focus:border-solid focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30"
										@keyup.enter="scanSerial(row, $event)"
									/>
								</div>
							</div>

							<!-- batch picker: one row per picked batch, no free-text batch -->
							<div
								v-if="batchPicker"
								data-testid="batch-picker"
								class="border-t border-gray-200 bg-gray-50 px-4 py-3"
							>
								<p class="mb-2 text-sm font-medium text-gray-900">
									{{ __("Pick batches for {0}", [batchPicker.item_name]) }}
								</p>
								<div class="flex flex-col gap-1.5">
									<label
										v-for="b in batchPicker.batches"
										:key="b.batch_no"
										class="flex items-center gap-2 text-sm text-gray-700"
									>
										<input
											type="checkbox"
											data-testid="batch-option"
											:value="b.batch_no"
											v-model="batchPicker.selected"
										/>
										<span>{{ b.batch_no }}<span v-if="b.expiry_date"> — {{ formatDate(b.expiry_date) }}</span></span>
									</label>
									<p v-if="!batchPicker.batches.length" class="text-xs text-gray-500">
										{{ __("No enabled batch for this item") }}
									</p>
								</div>
								<div class="mt-3 flex justify-end gap-2">
									<Button variant="subtle" size="sm" @click="batchPicker = null">{{ __("Cancel") }}</Button>
									<Button
										variant="solid"
										size="sm"
										:disabled="!batchPicker.selected.length"
										@click="addBatchRows"
									>
										{{ __("Add") }}
									</Button>
								</div>
							</div>

							<div class="relative px-4 py-3">
								<div class="relative">
									<FeatherIcon
										name="search"
										class="pointer-events-none absolute start-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400"
									/>
									<input
										v-model="itemSearch"
										type="text"
										:placeholder="__('Search item...')"
										:aria-label="__('Add item')"
										class="w-full rounded-lg border border-dashed border-gray-300 py-2 pe-3 ps-9 text-sm focus:border-solid focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30"
										@input="searchItems"
										@keyup.enter="addExactMatch"
									/>
								</div>
								<div
									v-if="itemResults.length"
									class="absolute inset-x-4 top-[3.25rem] z-10 max-h-56 overflow-y-auto rounded-lg border border-gray-200 bg-white py-1 shadow-lg"
								>
									<button
										v-for="i in itemResults"
										:key="i.item_code"
										type="button"
										class="flex w-full items-center justify-between gap-3 px-3 py-2 text-start text-sm hover:bg-gray-50 focus-visible:bg-gray-50 focus-visible:outline-none"
										@click="addItem(i)"
									>
										<span class="truncate text-gray-900">{{ i.item_name || i.item_code }}</span>
										<span v-if="i.item_name && i.item_name !== i.item_code" class="shrink-0 text-xs text-gray-400">
											{{ i.item_code }}
										</span>
									</button>
								</div>
								<p v-if="!editor.items.length" class="mt-2 text-xs text-gray-400">
									{{ __("Search and add only the items you physically counted.") }}
								</p>
							</div>
						</section>

						<section class="rounded-xl border border-gray-200 bg-white p-4">
							<h4 class="mb-1 text-sm font-semibold text-gray-900">{{ __("Excel Template") }}</h4>
							<p class="mb-3 text-xs text-gray-500">
								{{ __("Download the count sheet, fill Counted Qty, then upload it back.") }}
							</p>
							<div class="flex flex-wrap items-end gap-3">
								<div>
									<label for="so-item-group" class="mb-1 block text-xs font-medium text-gray-700">
										{{ __("Item Group") }}
									</label>
									<select
										id="so-item-group"
										v-model="itemGroup"
										class="rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30"
									>
										<option value="">{{ __("All item groups") }}</option>
										<option v-for="g in itemGroups" :key="g.item_group" :value="g.item_group">
											{{ g.item_group }}
										</option>
									</select>
								</div>
								<Button variant="subtle" @click="downloadTemplate">
									<template #prefix>
										<FeatherIcon name="download" class="h-4 w-4" />
									</template>
									{{ __("Download Template") }}
								</Button>
								<label
									class="cursor-pointer rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50"
								>
									<input type="file" accept=".xlsx" class="hidden" :disabled="importing" @change="onFilePicked" />
									{{ importing ? __("Uploading...") : __("Upload") }}
								</label>
							</div>
							<ul v-if="importResult?.errors?.length" data-testid="import-errors" class="mt-3 space-y-1">
								<li
									v-for="e in importResult.errors"
									:key="`${e.row}-${e.item_code}`"
									class="text-xs text-red-600"
								>
									{{ __("Row {0}: {1} — {2}", [e.row, e.item_code, e.message]) }}
								</li>
							</ul>
						</section>

						<section class="rounded-xl border border-gray-200 bg-white p-4" data-testid="opname-attachments">
							<h4 class="mb-3 text-sm font-semibold text-gray-900">{{ __("Attachments") }}</h4>
							<PurchaseAttachments
								editable
								:pending="editor.attachments"
								:label="__('Attach File')"
								:accept="OPNAME_ATTACHMENT_ACCEPT"
								:hint="__('Image or document · max 10 MB')"
								@add="attachFiles"
								@remove="removeAttachment"
							/>
							<div class="mt-3 flex gap-2">
								<input
									v-model="linkInput"
									type="text"
									inputmode="url"
									data-testid="attach-link"
									:placeholder="__('Paste a link (https://...)')"
									:aria-label="__('Link')"
									class="min-w-0 flex-1 rounded-lg border px-3 py-2 text-sm focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30"
									:class="linkInput && !linkOk ? 'border-red-400' : 'border-gray-300'"
									@keyup.enter="attachLink"
								/>
								<Button variant="subtle" :loading="attaching" :disabled="!linkOk" @click="attachLink">
									<template #prefix>
										<FeatherIcon name="link" class="h-4 w-4" />
									</template>
									{{ __("Add Link") }}
								</Button>
							</div>
							<p v-if="linkInput && !linkOk" class="mt-1 text-xs text-red-600">
								{{ __("Link must start with http:// or https://") }}
							</p>
						</section>
					</template>

					<section
						v-if="!editable && editor.attachments.length"
						class="rounded-xl border border-gray-200 bg-white p-4"
					>
						<PurchaseAttachments :attached="editor.attachments" :label="__('Attachments')" />
					</section>
				</template>
			</div>
		</template>

		<template #actions>
			<div
				v-if="view === 'editor' && editable && editor"
				class="mx-auto flex w-full max-w-6xl justify-between gap-2"
			>
				<Button variant="subtle" @click="backToList">{{ __("Back") }}</Button>
				<div class="flex gap-2">
					<Button v-if="editor.name" variant="subtle" theme="red" :loading="acting" @click="remove">
						{{ __("Delete") }}
					</Button>
					<Button variant="subtle" :loading="saving" @click="save">{{ __("Save") }}</Button>
					<Button variant="solid" :loading="submitting" @click="submit">{{ __("Submit") }}</Button>
				</div>
			</div>
		</template>
	</DialogHost>
</template>

<script setup>
import { Button, FeatherIcon, call } from "frappe-ui"
import { computed, ref, watch } from "vue"
import DialogHost from "@/components/common/DialogHost.js"
import RefreshButton from "@/components/common/RefreshButton.vue"
import PurchaseAttachments from "@/components/purchase/PurchaseAttachments.vue"
import { useFormatters } from "@/composables/useFormatters"
import { useToast } from "@/composables/useToast"
import { ATTACHMENT_ACCEPT, fileToBase64, sanitizePickedFiles } from "@/utils/attachments"
import { downloadXlsx } from "@/utils/downloadXlsx"

const props = defineProps({
	modelValue: Boolean,
	posProfile: String,
	embedded: { type: Boolean, default: false },
})

const emit = defineEmits(["update:modelValue"])

const { showSuccess, showError } = useToast()
const { formatDate } = useFormatters()

const show = ref(props.modelValue)
watch(
	() => props.modelValue,
	(v) => {
		show.value = v
		if (v) {
			view.value = "list"
			loadList()
		}
	},
)
watch(show, (v) => emit("update:modelValue", v))

const API = "pos_next.api.stock_opname"

// view: "list" | "editor"
const view = ref("list")
const opnames = ref([])
const loading = ref(false)
const loadingDetail = ref(false)
const editor = ref(null)

const itemSearch = ref("")
const itemResults = ref([])
const batchPicker = ref(null)
const itemGroups = ref([])
const itemGroup = ref("")

const saving = ref(false)
const submitting = ref(false)
const acting = ref(false)
const importing = ref(false)
const importResult = ref(null)
const attaching = ref(false)
// opname evidence also comes as office documents (count sheets, BA)
const OPNAME_ATTACHMENT_ACCEPT = `${ATTACHMENT_ACCEPT},.doc,.docx,.xls,.xlsx,.csv,.txt,.ppt,.pptx,.odt,.ods`
const linkInput = ref("")
const linkOk = computed(() => /^https?:\/\/\S+$/i.test(linkInput.value.trim()))

const dialogTitle = computed(() => __("Stock Opname"))

const STATUS_TONES = {
	Draft: "bg-orange-50 text-orange-700 ring-orange-200",
	"Approval Pending by Stock Manager": "bg-blue-50 text-blue-700 ring-blue-200",
	"Approval Pending by Accounts Manager":
		"bg-blue-50 text-blue-700 ring-blue-200",
	Rejected: "bg-red-50 text-red-700 ring-red-200",
	Approved: "bg-green-50 text-green-700 ring-green-200",
	Processing: "bg-blue-50 text-blue-700 ring-blue-200",
	Cancelled: "bg-gray-50 text-gray-500 ring-gray-200",
}

function statusPill(label) {
	return [
		"inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset",
		STATUS_TONES[label] || "bg-gray-50 text-gray-700 ring-gray-200",
	]
}

// Approved but still docstatus 0 -> ERPNext is submitting it in the background.
function stateLabel(row) {
	if (row?.workflow_state === "Approved" && !row.docstatus) return "Processing"
	return row?.workflow_state || "Draft"
}

const editable = computed(
	() =>
		!!editor.value &&
		["Draft", "Rejected"].includes(editor.value.workflow_state),
)

// Continue opens only an editable count; a Pending opname opens read-only.
const openOpname = computed(() =>
	opnames.value.find((o) => !o.docstatus && ["Draft", "Rejected"].includes(o.workflow_state)),
)

// One open count per warehouse: any docstatus-0 opname blocks starting a new one (D5).
const hasOpenOpname = computed(() => opnames.value.some((o) => !o.docstatus))

// ---------------------------------------------------------------------------
// List
// ---------------------------------------------------------------------------

async function loadList() {
	if (!props.posProfile) return
	loading.value = true
	try {
		opnames.value =
			(await call(`${API}.list_opnames`, { pos_profile: props.posProfile })) ||
			[]
	} catch (err) {
		showError(errMsg(err, "Failed to load stock opname"))
	} finally {
		loading.value = false
	}
}

// ---------------------------------------------------------------------------
// Editor
// ---------------------------------------------------------------------------

// has_serial_no is the item flag (not stock data): a serial row with zero
// ticked serials is still a serial row and must save serial_nos: [].
function normalizeItem(item) {
	const serials = Array.isArray(item.serial_nos) ? item.serial_nos : []
	const is_serial = !!item.has_serial_no || serials.length > 0
	return {
		item_code: item.item_code,
		item_name: item.item_name || item.item_code,
		stock_uom: item.stock_uom,
		qty: item.qty,
		batch_no: item.batch_no || null,
		has_batch_no: item.has_batch_no,
		has_serial_no: item.has_serial_no,
		serial_nos: [...serials],
		serial_options: [...serials],
		is_serial,
	}
}

function localDate() {
	const d = new Date()
	const m = String(d.getMonth() + 1).padStart(2, "0")
	const day = String(d.getDate()).padStart(2, "0")
	return `${d.getFullYear()}-${m}-${day}`
}

function localTime() {
	const d = new Date()
	return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`
}

function applyEditor(payload) {
	editor.value = {
		name: payload.name,
		workflow_state: payload.workflow_state,
		docstatus: payload.docstatus,
		modified: payload.modified,
		reject_reason: payload.reject_reason,
		posting_date: payload.posting_date || localDate(),
		posting_time: (payload.posting_time || localTime()).slice(0, 5),
		items: (payload.items || []).map(normalizeItem),
		attachments: payload.attachments || [],
	}
}

async function openEditor(name) {
	view.value = "editor"
	loadingDetail.value = true
	importResult.value = null
	batchPicker.value = null
	try {
		applyEditor(
			await call(`${API}.get_opname`, { pos_profile: props.posProfile, name }),
		)
		if (!itemGroups.value.length) loadGroups()
		ensureSerialOptions()
	} catch (err) {
		showError(errMsg(err, "Failed to load stock opname"))
		backToList()
	} finally {
		loadingDetail.value = false
	}
}

function openNew() {
	editor.value = {
		name: null,
		workflow_state: "Draft",
		docstatus: 0,
		modified: null,
		reject_reason: null,
		posting_date: localDate(),
		posting_time: localTime(),
		items: [],
		attachments: [],
	}
	linkInput.value = ""
	view.value = "editor"
	importResult.value = null
	batchPicker.value = null
	itemSearch.value = ""
	itemResults.value = []
	if (!itemGroups.value.length) loadGroups()
}

function backToList() {
	editor.value = null
	importResult.value = null
	batchPicker.value = null
	view.value = "list"
	loadList()
}

// A loaded serial row only knows its ticked serials; fetch the full list so a
// fresh physical serial can still be ticked or scanned.
async function ensureSerialOptions() {
	for (const row of editor.value?.items || []) {
		if (!row.is_serial) continue
		try {
			const all = await call(`${API}.get_item_serials`, {
				pos_profile: props.posProfile,
				item_code: row.item_code,
			})
			row.serial_options = Array.from(
				new Set([...(all || []), ...row.serial_nos]),
			)
		} catch {
			// options stay as the loaded ticked serials
		}
	}
}

async function loadGroups() {
	try {
		itemGroups.value =
			(await call("pos_next.api.items.get_item_groups", {
				pos_profile: props.posProfile,
			})) || []
	} catch {
		itemGroups.value = [] // the filter is optional; the sheet still works
	}
}

// ---------------------------------------------------------------------------
// Item search / add
// ---------------------------------------------------------------------------

let searchTimer = null
let searchSeq = 0

function searchItems() {
	clearTimeout(searchTimer)
	const term = itemSearch.value.trim()
	if (!term) {
		searchSeq++
		itemResults.value = []
		return
	}
	searchTimer = setTimeout(() => doSearchItems(term), 350)
}

async function doSearchItems(term) {
	const seq = ++searchSeq
	try {
		const rows = await call(`${API}.search_opname_items`, {
			pos_profile: props.posProfile,
			search_term: term,
			start: 0,
			limit: 10,
		})
		if (seq === searchSeq) itemResults.value = rows || []
	} catch (err) {
		if (seq === searchSeq) showError(errMsg(err, "Failed to search items"))
	}
}

// Barcode scan: Enter picks the exact item_code from the current results.
function addExactMatch() {
	const term = itemSearch.value.trim()
	const exact = itemResults.value.find((i) => i.item_code === term)
	if (exact) addItem(exact)
}

function clearSearch() {
	searchSeq++
	clearTimeout(searchTimer)
	itemSearch.value = ""
	itemResults.value = []
}

async function addItem(item) {
	clearSearch()
	if (item.has_serial_no) {
		if (editor.value.items.some((r) => r.item_code === item.item_code)) return
		const serials =
			(await call(`${API}.get_item_serials`, {
				pos_profile: props.posProfile,
				item_code: item.item_code,
			})) || []
		editor.value.items.push({
			item_code: item.item_code,
			item_name: item.item_name || item.item_code,
			stock_uom: item.stock_uom,
			qty: 0,
			batch_no: null,
			has_batch_no: item.has_batch_no,
			has_serial_no: item.has_serial_no,
			serial_nos: [],
			serial_options: serials,
			is_serial: true,
		})
	} else if (item.has_batch_no) {
		const batches =
			(await call(`${API}.get_item_batches`, {
				pos_profile: props.posProfile,
				item_code: item.item_code,
			})) || []
		batchPicker.value = {
			item_code: item.item_code,
			item_name: item.item_name || item.item_code,
			stock_uom: item.stock_uom,
			batches,
			selected: [],
		}
	} else {
		if (editor.value.items.some((r) => r.item_code === item.item_code)) return
		editor.value.items.push({
			item_code: item.item_code,
			item_name: item.item_name || item.item_code,
			stock_uom: item.stock_uom,
			qty: 1,
			batch_no: null,
			has_batch_no: item.has_batch_no,
			has_serial_no: item.has_serial_no,
			serial_nos: [],
			serial_options: [],
			is_serial: false,
		})
	}
}

function addBatchRows() {
	const picker = batchPicker.value
	if (!picker) return
	for (const batch_no of picker.selected) {
		if (
			editor.value.items.some(
				(r) => r.item_code === picker.item_code && r.batch_no === batch_no,
			)
		)
			continue
		editor.value.items.push({
			item_code: picker.item_code,
			item_name: picker.item_name,
			stock_uom: picker.stock_uom,
			qty: 0,
			batch_no,
			has_batch_no: 1,
			has_serial_no: 0,
			serial_nos: [],
			serial_options: [],
			is_serial: false,
		})
	}
	batchPicker.value = null
}

function toggleSerial(row, serial) {
	const i = row.serial_nos.indexOf(serial)
	if (i === -1) row.serial_nos.push(serial)
	else row.serial_nos.splice(i, 1)
}

function scanSerial(row, event) {
	const value = (event.target.value || "").trim()
	event.target.value = ""
	if (!value) return
	if (!row.serial_options.includes(value)) {
		showError(__("Unknown serial"))
		return
	}
	if (!row.serial_nos.includes(value)) row.serial_nos.push(value)
}

function buildItems() {
	return editor.value.items.map((r) => {
		const out = {
			item_code: r.item_code,
			qty: r.is_serial ? r.serial_nos.length : Number(r.qty) || 0,
		}
		if (r.batch_no) out.batch_no = r.batch_no
		if (r.is_serial) out.serial_nos = [...r.serial_nos]
		return out
	})
}

// ---------------------------------------------------------------------------
// Save / submit / delete
// ---------------------------------------------------------------------------

async function save() {
	if (saving.value) return false
	saving.value = true
	try {
		const payload = await call(`${API}.save_opname`, {
			pos_profile: props.posProfile,
			data: {
				name: editor.value.name || undefined,
				modified: editor.value.modified || undefined,
				items: buildItems(),
				posting_date: editor.value.posting_date || undefined,
				posting_time: editor.value.posting_time || undefined,
			},
		})
		applyEditor(payload)
		// a save response only carries the ticked serials; refetch so the
		// remaining serials of a serial item stay tickable
		await ensureSerialOptions()
		showSuccess(__("Stock opname saved"))
		return true
	} catch (err) {
		// a stale save comes back as "modified, reload" — surface the server text
		showError(errMsg(err, "Failed to save stock opname"))
		return false
	} finally {
		saving.value = false
	}
}

async function submit() {
	if (submitting.value) return
	if (!window.confirm(__("After submitting you cannot edit until HQ decides")))
		return
	submitting.value = true
	try {
		// persist the current count first (a Rejected doc revises back to Draft)
		if (!editor.value.name || editable.value) {
			const ok = await save()
			if (!ok || !editor.value.name) return
		}
		// a link typed but not added yet would be lost once the doc locks
		if (linkInput.value.trim()) {
			if (!linkOk.value) {
				showError(__("Link must start with http:// or https://"))
				return
			}
			if (!(await sendAttachments({ link: linkInput.value.trim() }))) return
			linkInput.value = ""
		}
		await call(`${API}.submit_opname`, {
			pos_profile: props.posProfile,
			name: editor.value.name,
		})
		showSuccess(__("Stock opname submitted"))
		backToList()
	} catch (err) {
		showError(errMsg(err, "Failed to submit stock opname"))
	} finally {
		submitting.value = false
	}
}

async function remove() {
	if (acting.value || !editor.value.name) return
	if (!window.confirm(__("Delete this stock opname?"))) return
	acting.value = true
	try {
		await call(`${API}.delete_opname`, {
			pos_profile: props.posProfile,
			name: editor.value.name,
		})
		showSuccess(__("Stock opname deleted"))
		backToList()
	} catch (err) {
		showError(errMsg(err, "Failed to delete stock opname"))
	} finally {
		acting.value = false
	}
}

// ---------------------------------------------------------------------------
// Excel template
// ---------------------------------------------------------------------------

async function downloadTemplate() {
	try {
		await downloadXlsx(`${API}.download_opname_template`, {
			pos_profile: props.posProfile,
			item_group: itemGroup.value || undefined,
		})
	} catch (err) {
		showError(err?.message || __("Export failed"))
	}
}

async function uploadFile(file) {
	const form = new FormData()
	form.append("file", file)
	form.append("is_private", "1")
	const response = await fetch("/api/method/upload_file", {
		method: "POST",
		headers: { "X-Frappe-CSRF-Token": window.csrf_token || "" },
		body: form,
	})
	if (!response.ok) throw new Error(__("Upload failed"))
	const body = await response.json()
	return body.message.file_url
}

async function onFilePicked(event) {
	const file = event.target.files?.[0]
	event.target.value = ""
	if (!file) return
	// An unsaved new count would be replaced wholesale by the upload: confirm.
	if (!editor.value?.name && editor.value?.items?.length) {
		if (!window.confirm(__("Uploading replaces the unsaved rows. Upload anyway?"))) return
	}
	importing.value = true
	importResult.value = null
	try {
		const fileUrl = await uploadFile(file)
		const result = await call(`${API}.import_opname_template`, {
			pos_profile: props.posProfile,
			file_url: fileUrl,
			name: editor.value.name || undefined,
		})
		importResult.value = result
		// merge lands in the open draft: reload its rows, keep the row errors
		if (result?.name) {
			applyEditor(
				await call(`${API}.get_opname`, {
					pos_profile: props.posProfile,
					name: result.name,
				}),
			)
			ensureSerialOptions()
		}
		showSuccess(__("Template imported"))
	} catch (err) {
		showError(errMsg(err, "Failed to import template"))
	} finally {
		importing.value = false
	}
}

// ---------------------------------------------------------------------------
// Attachments (files + links, stored as File rows on the opname)
// ---------------------------------------------------------------------------

// Files attach to a real doc name: a new count is saved first.
async function ensureSaved() {
	if (editor.value.name) return true
	if (!editor.value.items.length) {
		showError(__("Add at least one counted item before attaching"))
		return false
	}
	return (await save()) && !!editor.value.name
}

async function sendAttachments(args) {
	if (attaching.value || !(await ensureSaved())) return false
	attaching.value = true
	try {
		editor.value.attachments =
			(await call(`${API}.attach_opname_files`, {
				pos_profile: props.posProfile,
				name: editor.value.name,
				...args,
			})) || []
		return true
	} catch (err) {
		showError(errMsg(err, "Failed to attach"))
		return false
	} finally {
		attaching.value = false
	}
}

async function attachFiles(files) {
	const { ok, tooBig } = sanitizePickedFiles(files)
	if (tooBig.length) showError(__("Some files exceed the 10 MB limit"))
	if (!ok.length) return
	const payloads = await Promise.all(
		ok.map(async (file) => ({ file_name: file.name, filedata: await fileToBase64(file) })),
	)
	if (await sendAttachments({ files: JSON.stringify(payloads) }))
		showSuccess(__("{0} attachment(s) uploaded", [ok.length]))
}

async function attachLink() {
	if (!linkOk.value) return
	if (await sendAttachments({ link: linkInput.value.trim() })) linkInput.value = ""
}

async function removeAttachment(row) {
	if (!window.confirm(__("Remove this attachment?"))) return
	try {
		editor.value.attachments =
			(await call(`${API}.remove_opname_attachment`, {
				pos_profile: props.posProfile,
				name: editor.value.name,
				file: row.name,
			})) || []
	} catch (err) {
		showError(errMsg(err, "Failed to remove attachment"))
	}
}

function errMsg(err, fallback) {
	const text =
		err?.messages?.join("\n") ||
		(typeof err === "string" ? err : err?.message) ||
		""
	return text || __(fallback)
}

if (props.modelValue) {
	view.value = "list"
	loadList()
}
</script>
