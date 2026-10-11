<template>
	<DialogHost
		v-model:show="show"
		:embedded="embedded"
		:options="{ title: __('Reject Requests'), size: '4xl' }"
	>
		<template #body-content>
			<!-- LIST LEVEL -->
			<template v-if="view === 'list'">
				<div class="flex items-end gap-2 mb-4 border-b border-gray-200">
					<nav class="flex gap-5 overflow-x-auto" role="tablist">
						<button
							v-for="tab in statusTabs"
							:key="tab.value"
							type="button"
							role="tab"
							:aria-selected="statusFilter === tab.value"
							class="-mb-px pb-2.5 pt-1 border-b-2 text-sm font-medium whitespace-nowrap transition-colors"
							:class="
								statusFilter === tab.value
									? 'border-gray-900 text-gray-900'
									: 'border-transparent text-gray-500 hover:text-gray-800'
							"
							@click="setStatusFilter(tab.value)"
						>
							{{ tab.label }}
						</button>
					</nav>
					<RefreshButton class="ms-auto mb-1.5" :loading="loading" @click="loadList()" />
				</div>

				<div class="flex justify-end mb-3">
					<Button variant="solid" :disabled="acting" @click="openNew">
						{{ __("New Request") }}
					</Button>
				</div>

				<div v-if="loading" class="py-10 text-center text-sm text-gray-500">
					{{ __("Loading...") }}
				</div>
				<div
					v-else-if="!requests.length"
					class="py-10 text-center text-sm text-gray-500"
				>
					{{ __("No reject requests") }}
				</div>
				<div v-else class="flex flex-col gap-2 max-h-[60vh] overflow-y-auto">
					<button
						v-for="r in requests"
						:key="r.name"
						class="w-full text-start px-4 py-3 border border-gray-200 rounded-lg hover:border-gray-400 transition-colors"
						@click="openDetail(r.name)"
					>
						<div class="flex items-start justify-between gap-2">
							<div class="min-w-0">
								<div class="font-medium text-gray-900 truncate">
									{{ r.name }}
									<span
										v-if="r.is_late"
										class="ms-2 px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-700"
										>{{ __("Late") }}</span
									>
								</div>
								<div class="text-xs text-gray-500 mt-0.5">
									{{ __(r.purpose) }} · {{ r.incident_date }}
								</div>
							</div>
							<span
								class="shrink-0 px-2 py-0.5 rounded-full text-xs font-medium"
								:class="statusClass(r.status)"
							>
								{{ __(r.status) }}
							</span>
						</div>
					</button>
				</div>
				<div v-if="hasMore" class="mt-3 flex justify-center">
					<Button variant="subtle" size="sm" :loading="loading" @click="loadList(false)">
						{{ __("Load More") }}
					</Button>
				</div>
			</template>

			<!-- NEW REQUEST (Unsold only) -->
			<template v-else-if="view === 'new'">
				<label class="block text-sm font-medium text-gray-700 mb-1">
					{{ __("Incident Date") }}
				</label>
				<input
					v-model="form.incident_date"
					type="date"
					class="w-full px-3 py-2 mb-3 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
				/>

				<label class="block text-sm font-medium text-gray-700 mb-1">
					{{ __("Items") }}
				</label>
				<input
					v-model="itemSearch"
					type="text"
					:placeholder="__('Search item...')"
					class="w-full px-3 py-2 mb-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
					@input="searchItems"
				/>
				<div
					v-if="itemResults.length"
					class="flex flex-col gap-1 mb-2 max-h-40 overflow-y-auto"
				>
					<button
						v-for="i in itemResults"
						:key="i.item_code"
						type="button"
						class="text-start px-3 py-2 text-sm border border-gray-200 rounded-lg hover:bg-gray-50"
						@click="addItem(i)"
					>
						{{ i.item_name || i.item_code }}
					</button>
				</div>
				<table v-if="form.items.length" class="w-full text-sm mb-3">
					<tbody>
						<tr v-for="(row, idx) in form.items" :key="row.item_code" class="border-t border-gray-100">
							<td class="py-1.5 pe-2">{{ row.item_name }}</td>
							<td class="py-1.5 pe-2 w-28">
								<input
									v-model.number="row.qty"
									type="number"
									min="0"
									step="any"
									class="w-full px-2 py-1 text-sm border border-gray-300 rounded-lg"
								/>
							</td>
							<td class="py-1.5 w-8 text-end">
								<button
									type="button"
									class="text-gray-400 hover:text-red-600"
									:aria-label="__('Remove item')"
									@click="form.items.splice(idx, 1)"
								>
									×
								</button>
							</td>
						</tr>
					</tbody>
				</table>

				<label class="block text-sm font-medium text-gray-700 mb-1">
					{{ __("Reason") }}
				</label>
				<textarea
					v-model="form.reason"
					rows="3"
					class="w-full px-3 py-2 mb-3 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
				/>

				<label class="block text-sm font-medium text-gray-700 mb-1">
					{{ __("Video Link") }}
				</label>
				<input
					v-model="form.video_link"
					type="text"
					class="w-full px-3 py-2 mb-3 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
				/>
				<p v-if="form.video_link && !videoLinkOk" class="text-xs text-red-600 mb-3">
					{{ __("Video link must start with http:// or https://") }}
				</p>

				<label class="block text-sm font-medium text-gray-700 mb-1">
					{{ __("Evidence Photos (1-5)") }}
				</label>
				<input
					data-testid="evidence-input"
					type="file"
					multiple
					:accept="ATTACHMENT_ACCEPT"
					class="mb-1 text-sm"
					@change="onEvidencePicked"
				/>
				<p v-if="form.files.length" class="text-xs text-gray-500 mb-3">
					{{ __("{0} photo(s) selected", [form.files.length]) }}
				</p>

				<div class="mt-4 flex justify-end gap-2">
					<Button variant="subtle" :disabled="submitting" @click="view = 'list'">
						{{ __("Back") }}
					</Button>
					<Button
						variant="solid"
						:loading="submitting"
						:disabled="!canSubmit"
						@click="submitNew"
					>
						{{ __("Submit") }}
					</Button>
				</div>
			</template>

			<!-- DETAIL -->
			<template v-else>
				<div v-if="loadingDetail" class="py-10 text-center text-sm text-gray-500">
					{{ __("Loading...") }}
				</div>
				<template v-else-if="detail">
					<div class="flex items-start justify-between gap-2 mb-3">
						<div>
							<div class="font-medium text-gray-900">
								{{ detail.name }}
								<span
									v-if="detail.is_late"
									class="ms-2 px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-700"
									>{{ __("Late") }}</span
								>
							</div>
							<div class="text-xs text-gray-500 mt-0.5">
								{{ __(detail.purpose) }} · {{ detail.incident_date }}
							</div>
						</div>
						<span
							class="shrink-0 px-2 py-0.5 rounded-full text-xs font-medium"
							:class="statusClass(detail.status)"
						>
							{{ __(detail.status) }}
						</span>
					</div>

					<div class="overflow-x-auto -mx-1 px-1 mb-3">
						<table class="w-full text-sm">
							<thead>
								<tr class="text-start text-xs text-gray-500 uppercase">
									<th class="py-1 text-start">{{ __("Item") }}</th>
									<th class="py-1 text-start">{{ __("Qty") }}</th>
									<th class="py-1 text-start">{{ __("Amount") }}</th>
								</tr>
							</thead>
							<tbody>
								<tr v-for="row in detail.items" :key="row.item_code" class="border-t border-gray-100">
									<td class="py-1.5 pe-2">{{ row.item_name || row.item_code }}</td>
									<td class="py-1.5 pe-2 tabular-nums">{{ row.qty }}</td>
									<td class="py-1.5 pe-2 tabular-nums">{{ row.amount }}</td>
								</tr>
							</tbody>
						</table>
					</div>

					<!-- Crew-editable fields on a Draft: reason (+ video link, and
					     items only when not system-generated) -->
					<template v-if="detail.docstatus === 0">
						<label class="block text-sm font-medium text-gray-700 mb-1">
							{{ __("Reason") }}
						</label>
						<textarea
							v-model="detail.reason"
							rows="3"
							class="w-full px-3 py-2 mb-3 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
						/>

						<label class="block text-sm font-medium text-gray-700 mb-1">
							{{ __("Video Link") }}
						</label>
						<input
							v-model="detail.video_link"
							type="text"
							class="w-full px-3 py-2 mb-3 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
						/>
						<p v-if="detail.video_link && !videoLinkOk" class="text-xs text-red-600 mb-3">
							{{ __("Video link must start with http:// or https://") }}
						</p>

						<!-- New items only for crew Unsold drafts; a system Production
						     Loss draft is locked to what the Work Order recorded. -->
						<template v-if="!detail.is_system_generated">
							<input
								v-model="itemSearch"
								type="text"
								:placeholder="__('Search item...')"
								class="w-full px-3 py-2 mb-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
								@input="searchItems"
							/>
							<div
								v-if="itemResults.length"
								class="flex flex-col gap-1 mb-2 max-h-40 overflow-y-auto"
							>
								<button
									v-for="i in itemResults"
									:key="i.item_code"
									type="button"
									class="text-start px-3 py-2 text-sm border border-gray-200 rounded-lg hover:bg-gray-50"
									@click="addDetailItem(i)"
								>
									{{ i.item_name || i.item_code }}
								</button>
							</div>
						</template>

						<label class="block text-sm font-medium text-gray-700 mb-1">
							{{ __("Evidence Photos (1-5)") }}
						</label>
						<input
							data-testid="evidence-input"
							type="file"
							multiple
							:accept="ATTACHMENT_ACCEPT"
							class="mb-1 text-sm"
							@change="onEvidencePicked"
						/>
						<p class="text-xs text-gray-500 mb-3">
							{{
								__("{0} evidence photo(s) attached", [detail.evidence.length + newEvidence.length])
							}}
						</p>

						<div class="mt-2 flex justify-end gap-2">
							<Button variant="subtle" @click="backToList">{{ __("Back") }}</Button>
							<Button
								variant="subtle"
								theme="red"
								:loading="acting"
								@click="doCancel"
							>
								{{ __("Cancel") }}
							</Button>
							<Button
								variant="solid"
								:loading="submitting"
								:disabled="!canSubmitDetail"
								@click="submitDetail"
							>
								{{ __("Submit") }}
							</Button>
						</div>
					</template>

					<!-- Pending: the crew can still withdraw the request -->
					<template v-else-if="detail.status === 'Pending'">
						<div class="mt-2 flex justify-end gap-2">
							<Button variant="subtle" @click="backToList">{{ __("Back") }}</Button>
							<Button variant="subtle" theme="red" :loading="acting" @click="doCancel">
								{{ __("Cancel") }}
							</Button>
						</div>
					</template>

					<!-- Approved: upload destruction proof, then close -->
					<template v-else-if="detail.status === 'Approved'">
						<label class="block text-sm font-medium text-gray-700 mb-1">
							{{ __("Destruction Photos") }}
						</label>
						<input
							data-testid="destruction-input"
							type="file"
							multiple
							:accept="ATTACHMENT_ACCEPT"
							class="mb-1 text-sm"
							@change="onDestructionPicked"
						/>
						<p class="text-xs text-gray-500 mb-3">
							{{ __("{0} destruction photo(s) attached", [detail.destruction.length + newDestruction.length]) }}
						</p>

						<div class="mt-2 flex justify-end gap-2">
							<Button variant="subtle" @click="backToList">{{ __("Back") }}</Button>
							<Button
								variant="solid"
								:loading="acting"
								:disabled="!destructionCount"
								@click="doClose"
							>
								{{ __("Close") }}
							</Button>
						</div>
					</template>

					<template v-else>
						<div class="mt-2 flex justify-end">
							<Button variant="subtle" @click="backToList">{{ __("Back") }}</Button>
						</div>
					</template>
				</template>
			</template>

			<div v-if="errorMessage" class="mt-3 text-sm text-red-600">{{ errorMessage }}</div>
		</template>
	</DialogHost>
</template>

<script setup>
import { Button, call, FeatherIcon } from "frappe-ui"
import { computed, ref, watch } from "vue"
import DialogHost from "@/components/common/DialogHost.js"
import RefreshButton from "@/components/common/RefreshButton.vue"
import { useToast } from "@/composables/useToast"
import {
	ATTACHMENT_ACCEPT,
	fileToBase64,
	sanitizePickedFiles,
} from "@/utils/attachments"

const props = defineProps({
	modelValue: Boolean,
	posProfile: String,
	company: String,
	currency: { type: String, default: "" },
	embedded: { type: Boolean, default: false },
})

const emit = defineEmits(["update:modelValue"])

const { showSuccess, showError } = useToast()

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

const MAX_EVIDENCE = 5
const PAGE = 30

// view: "list" | "new" | "detail"
const view = ref("list")
const statusFilter = ref("")
const requests = ref([])
const hasMore = ref(false)
const loading = ref(false)
const loadingDetail = ref(false)
const errorMessage = ref("")

const statusTabs = [
	{ value: "", label: __("All") },
	{ value: "Draft", label: __("Draft") },
	{ value: "Pending", label: __("Pending") },
	{ value: "Approved", label: __("Approved") },
	{ value: "Rejected", label: __("Rejected") },
	{ value: "Closed", label: __("Closed") },
	{ value: "Cancelled", label: __("Cancelled") },
]

const acting = ref(false)
const submitting = ref(false)
const detail = ref(null)
// newly picked files waiting to be uploaded with the save
const newEvidence = ref([])
const newDestruction = ref([])

const itemSearch = ref("")
const itemResults = ref([])

const form = ref({
	incident_date: "",
	reason: "",
	video_link: "",
	items: [],
	files: [],
})

function statusClass(status) {
	if (status === "Pending") return "bg-blue-100 text-blue-700"
	if (status === "Approved") return "bg-green-100 text-green-700"
	if (status === "Rejected") return "bg-red-100 text-red-700"
	if (status === "Cancelled") return "bg-red-100 text-red-700"
	if (status === "Closed") return "bg-gray-100 text-gray-600"
	return "bg-yellow-100 text-yellow-700"
}

const videoLinkOk = computed(() => {
	const link = (view.value === "detail" ? detail.value?.video_link : form.value.video_link) || ""
	return !link || /^(https?:\/\/)/.test(link)
})

const evidenceCount = computed(() =>
	view.value === "detail"
		? (detail.value?.evidence?.length || 0) + newEvidence.value.length
		: form.value.files.length,
)

const destructionCount = computed(
	() => (detail.value?.destruction?.length || 0) + newDestruction.value.length,
)

const canSubmit = computed(
	() =>
		!!props.posProfile &&
		!!form.value.reason.trim() &&
		form.value.items.length > 0 &&
		form.value.items.every((i) => i.qty > 0) &&
		evidenceCount.value >= 1 &&
		evidenceCount.value <= MAX_EVIDENCE &&
		videoLinkOk.value,
)

const canSubmitDetail = computed(
	() =>
		!!detail.value?.reason?.trim() &&
		evidenceCount.value >= 1 &&
		evidenceCount.value <= MAX_EVIDENCE &&
		videoLinkOk.value,
)

// ---------------------------------------------------------------------------
// List
// ---------------------------------------------------------------------------

async function loadList(reset = true) {
	if (!props.posProfile || loading.value) return
	const offset = reset ? 0 : requests.value.length
	loading.value = true
	errorMessage.value = ""
	try {
		const data = await call("pos_next.api.reject_request.get_reject_requests", {
			pos_profile: props.posProfile,
			status: statusFilter.value || undefined,
			limit: PAGE,
			offset,
		})
		requests.value = reset
			? data.requests || []
			: requests.value.concat(data.requests || [])
		hasMore.value = !!data.has_more
	} catch (err) {
		showError(errMsg(err, "Failed to load reject requests"))
	} finally {
		loading.value = false
	}
}

function setStatusFilter(value) {
	if (statusFilter.value === value) return
	statusFilter.value = value
	loadList()
}

// ---------------------------------------------------------------------------
// New request
// ---------------------------------------------------------------------------

function openNew() {
	form.value = {
		incident_date: new Date().toISOString().slice(0, 10),
		reason: "",
		video_link: "",
		items: [],
		files: [],
	}
	itemSearch.value = ""
	itemResults.value = []
	errorMessage.value = ""
	view.value = "new"
}

let searchTimer = null
let lastSearchTerm = ""
function searchItems() {
	clearTimeout(searchTimer)
	const term = itemSearch.value.trim()
	if (!term) {
		itemResults.value = []
		return
	}
	// search immediately, then debounced while the user keeps typing
	if (term !== lastSearchTerm) doSearchItems()
	lastSearchTerm = term
	searchTimer = setTimeout(() => {
		if (itemSearch.value.trim() === lastSearchTerm) doSearchItems()
	}, 400)
}

async function doSearchItems() {
	try {
		const rows = await call("pos_next.api.items.get_items", {
			pos_profile: props.posProfile,
			search_term: itemSearch.value,
			start: 0,
			limit: 10,
		})
		itemResults.value = rows || []
	} catch (err) {
		showError(errMsg(err, "Failed to search items"))
	}
}

function addItem(item) {
	if (!form.value.items.some((r) => r.item_code === item.item_code)) {
		form.value.items.push({ item_code: item.item_code, item_name: item.item_name || item.item_code, qty: 1 })
	}
	itemSearch.value = ""
	itemResults.value = []
}

function addDetailItem(item) {
	if (!detail.value.items.some((r) => r.item_code === item.item_code)) {
		detail.value.items.push({ item_code: item.item_code, item_name: item.item_name || item.item_code, qty: 1 })
	}
	itemSearch.value = ""
	itemResults.value = []
}

async function onEvidencePicked(e) {
	const { ok, tooBig } = sanitizePickedFiles(e.target.files)
	if (tooBig.length) showError(__("Some files exceed the 10 MB limit"))
	const picked = ok.slice(0, MAX_EVIDENCE)
	if (view.value === "detail") newEvidence.value = picked
	else form.value.files = picked
	e.target.value = ""
}

async function onDestructionPicked(e) {
	const { ok, tooBig } = sanitizePickedFiles(e.target.files)
	if (tooBig.length) showError(__("Some files exceed the 10 MB limit"))
	newDestruction.value = ok
	e.target.value = ""
}

async function toPayloads(files) {
	return Promise.all(
		files.map(async (f) => ({ file_name: f.name, filedata: await fileToBase64(f) })),
	)
}

async function attachFiles(name, kind, files) {
	if (!files.length) return
	await call("pos_next.api.reject_request.attach_reject_files", {
		name,
		kind,
		files: JSON.stringify(await toPayloads(files)),
	})
}

/** Controller flow: draft save → attach evidence → submit save. */
async function submitRequest(data, files) {
	const draft = await call("pos_next.api.reject_request.save_reject_request", {
		data: JSON.stringify(data),
		submit: 0,
	})
	await attachFiles(draft.name, "evidence", files)
	return call("pos_next.api.reject_request.save_reject_request", {
		data: JSON.stringify({ ...data, name: draft.name }),
		submit: 1,
	})
}

async function submitNew() {
	if (!canSubmit.value || submitting.value) return
	errorMessage.value = ""
	submitting.value = true
	try {
		const data = {
			pos_profile: props.posProfile,
			incident_date: form.value.incident_date,
			reason: form.value.reason.trim(),
			video_link: form.value.video_link.trim() || null,
			items: form.value.items.map((i) => ({ item_code: i.item_code, qty: i.qty })),
		}
		await submitRequest(data, form.value.files)
		showSuccess(__("Reject request submitted"))
		view.value = "list"
		loadList()
	} catch (err) {
		showError(errMsg(err, "Failed to submit reject request"))
	} finally {
		submitting.value = false
	}
}

// ---------------------------------------------------------------------------
// Detail
// ---------------------------------------------------------------------------

async function openDetail(name) {
	errorMessage.value = ""
	view.value = "detail"
	loadingDetail.value = true
	newEvidence.value = []
	newDestruction.value = []
	itemSearch.value = ""
	itemResults.value = []
	try {
		detail.value = await call("pos_next.api.reject_request.get_reject_request", { name })
	} catch (err) {
		detail.value = null
		showError(errMsg(err, "Failed to load reject request"))
		backToList()
	} finally {
		loadingDetail.value = false
	}
}

function backToList() {
	detail.value = null
	view.value = "list"
	loadList()
}

async function submitDetail() {
	if (!canSubmitDetail.value || submitting.value || !detail.value) return
	errorMessage.value = ""
	submitting.value = true
	try {
		const data = {
			name: detail.value.name,
			reason: detail.value.reason.trim(),
			video_link: detail.value.video_link || null,
		}
		if (!detail.value.is_system_generated) {
			data.items = detail.value.items.map((i) => ({
				item_code: i.item_code,
				qty: i.qty,
			}))
		}
		await submitRequest(data, newEvidence.value)
		showSuccess(__("Reject request submitted"))
		backToList()
	} catch (err) {
		showError(errMsg(err, "Failed to submit reject request"))
	} finally {
		submitting.value = false
	}
}

async function doCancel() {
	if (acting.value || !detail.value) return
	acting.value = true
	errorMessage.value = ""
	try {
		await call("pos_next.api.reject_request.cancel_reject_request", {
			name: detail.value.name,
		})
		showSuccess(__("Reject request cancelled"))
		backToList()
	} catch (err) {
		showError(errMsg(err, "Failed to cancel reject request"))
	} finally {
		acting.value = false
	}
}

async function doClose() {
	if (acting.value || !detail.value) return
	acting.value = true
	errorMessage.value = ""
	try {
		await attachFiles(detail.value.name, "destruction", newDestruction.value)
		await call("pos_next.api.reject_request.close_reject_request", {
			name: detail.value.name,
		})
		showSuccess(__("Reject request closed"))
		backToList()
	} catch (err) {
		showError(errMsg(err, "Failed to close reject request"))
	} finally {
		acting.value = false
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
