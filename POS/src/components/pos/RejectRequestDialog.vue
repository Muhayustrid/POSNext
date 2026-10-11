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
					<Button variant="solid" @click="openNew">
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

			<!-- NEW REQUEST (crew purposes) -->
			<template v-else-if="view === 'new'">
				<label class="block text-sm font-medium text-gray-700 mb-1">
					{{ __("Purpose") }}
				</label>
				<select
					v-model="form.purpose"
					data-testid="purpose-select"
					class="w-full px-3 py-2 mb-3 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
				>
					<option v-for="p in purposes" :key="p" :value="p">{{ __(p) }}</option>
				</select>

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
				<PurchaseAttachments
					editable
					:pending="form.files"
					:label="__('Evidence Photos')"
					@add="addEvidence(form.files, $event)"
					@remove="(row) => form.files.splice(form.files.indexOf(row), 1)"
				/>

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
									<td class="py-1.5 pe-2 tabular-nums" data-testid="item-qty">{{ row.qty }}</td>
									<td class="py-1.5 pe-2 tabular-nums">{{ row.amount }}</td>
								</tr>
							</tbody>
						</table>
					</div>

					<!-- Read-only facts for submitted requests: crew sees the reason,
					     video link, evidence count and the Head Office verdict. -->
					<dl v-if="detail.docstatus !== 0" class="grid grid-cols-1 gap-1.5 text-sm mb-3">
						<div class="flex gap-2">
							<dt class="text-gray-500 shrink-0 w-36">{{ __("Reason") }}</dt>
							<dd class="text-gray-900 whitespace-pre-line">{{ detail.reason || "—" }}</dd>
						</div>
						<div v-if="detail.video_link" class="flex gap-2">
							<dt class="text-gray-500 shrink-0 w-36">{{ __("Video Link") }}</dt>
							<dd class="min-w-0">
								<a
									:href="detail.video_link"
									target="_blank"
									rel="noopener"
									class="text-blue-600 hover:underline break-all"
									>{{ detail.video_link }}</a
								>
							</dd>
						</div>
						<div class="flex gap-2">
							<dt class="text-gray-500 shrink-0 w-36">{{ __("Evidence Photos") }}</dt>
							<dd class="text-gray-900 tabular-nums">{{ detail.evidence.length }}</dd>
						</div>
						<template v-if="detail.decision">
							<div class="flex gap-2">
								<dt class="text-gray-500 shrink-0 w-36">{{ __("Decision") }}</dt>
								<dd class="font-medium text-gray-900">
									{{ __(detail.decision) }}
									<span class="block text-xs font-normal text-gray-500">
										{{
											detail.decision === "Approved"
												? __("Borne by company")
												: detail.decision === "Rejected"
													? __("Borne by crew")
													: ""
										}}
									</span>
								</dd>
							</div>
							<div v-if="detail.decision_notes" class="flex gap-2">
								<dt class="text-gray-500 shrink-0 w-36">{{ __("Decision Notes") }}</dt>
								<dd class="text-gray-900 whitespace-pre-line">{{ detail.decision_notes }}</dd>
							</div>
							<div
								v-if="detail.decision === 'Rejected' && detail.liabilities?.length"
								class="flex gap-2"
							>
								<dt class="text-gray-500 shrink-0 w-36">{{ __("Liability") }}</dt>
								<dd class="min-w-0">
									<div
										v-for="l in detail.liabilities"
										:key="l.user"
										class="text-gray-900 tabular-nums"
									>
										{{ l.user }} · {{ l.amount }}
									</div>
								</dd>
							</div>
						</template>
					</dl>

					<!-- Crew-editable fields on a Draft: reason + video link + photos.
					     Items are never editable here — qty on a system Production Loss
					     is locked server-side, and a new Unsold request picks items in
					     the New form. -->
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
						<a
							v-if="detail.video_link && videoLinkOk"
							:href="detail.video_link"
							target="_blank"
							rel="noopener"
							class="block mb-3 text-sm text-blue-600 hover:underline break-all"
							>{{ detail.video_link }}</a
						>
						<p v-if="detail.video_link && !videoLinkOk" class="text-xs text-red-600 mb-3">
							{{ __("Video link must start with http:// or https://") }}
						</p>

						<label class="block text-sm font-medium text-gray-700 mb-1">
							{{ __("Evidence Photos (1-5)") }}
						</label>
						<PurchaseAttachments
							:attached="detail.evidence"
							:label="__('Evidence Photos')"
						/>
						<PurchaseAttachments
							editable
							:pending="newEvidence"
							:label="__('Add Evidence Photos')"
							@add="addEvidence(newEvidence, $event)"
							@remove="(row) => newEvidence.splice(newEvidence.indexOf(row), 1)"
						/>

						<div class="mt-2 flex justify-end gap-2">
							<Button variant="subtle" @click="backToList">{{ __("Back") }}</Button>
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

					<!-- Approved/Rejected: upload destruction proof, then close -->
					<template v-else-if="detail.status === 'Approved' || detail.status === 'Rejected'">
						<label class="block text-sm font-medium text-gray-700 mb-1">
							{{ __("Destruction Photos") }}
						</label>
						<PurchaseAttachments
							:attached="detail.destruction"
							:label="__('Destruction Photos')"
						/>
						<PurchaseAttachments
							editable
							:pending="newDestruction"
							:label="__('Add Destruction Photos')"
							@add="addDestruction($event)"
							@remove="(row) => newDestruction.splice(newDestruction.indexOf(row), 1)"
						/>

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
		</template>
	</DialogHost>
</template>

<script setup>
import { Button, call } from "frappe-ui"
import { computed, ref, watch } from "vue"
import DialogHost from "@/components/common/DialogHost.js"
import RefreshButton from "@/components/common/RefreshButton.vue"
import PurchaseAttachments from "@/components/purchase/PurchaseAttachments.vue"
import { useToast } from "@/composables/useToast"
import { fileToBase64, sanitizePickedFiles } from "@/utils/attachments"

const props = defineProps({
	modelValue: Boolean,
	posProfile: String,
	embedded: { type: Boolean, default: false },
	initialRequest: { type: String, default: null },
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

// name of the draft created by a first submit attempt — kept across retries
// so a failed submit updates the same draft instead of inserting a duplicate
const draftName = ref(null)
// files already uploaded in this session — retries after a failed final
// save/close must not be blocked by gates that only count pending files
const uploadedEvidence = ref(0)
const uploadedDestruction = ref(0)

const form = ref({
	incident_date: "",
	purpose: "",
	reason: "",
	video_link: "",
	items: [],
	files: [],
})

// enabled non-system purposes for the crew dropdown; a Production Loss
// draft keeps its purpose read-only since it is not in this list
const purposes = ref([])

async function loadPurposes() {
	try {
		purposes.value =
			(await call("pos_next.api.reject_request.get_reject_purposes")) || []
		// the New form may already be open while purposes were loading
		if (!form.value.purpose && purposes.value.length)
			form.value.purpose = purposes.value[0]
	} catch (err) {
		showError(errMsg(err, "Failed to load reject purposes"))
	}
}

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
	if (!link) return true
	try {
		return ["http:", "https:"].includes(new URL(link).protocol)
	} catch {
		return false
	}
})

const destructionCount = computed(
	() =>
		(detail.value?.destruction?.length || 0) +
		newDestruction.value.length +
		uploadedDestruction.value,
)

const canSubmit = computed(
	() =>
		!!props.posProfile &&
		!!form.value.purpose &&
		!!form.value.reason.trim() &&
		form.value.items.length > 0 &&
		form.value.items.every((i) => i.qty > 0) &&
		form.value.files.length + uploadedEvidence.value >= 1 &&
		form.value.files.length + uploadedEvidence.value <= MAX_EVIDENCE &&
		videoLinkOk.value,
)

const canSubmitDetail = computed(
	() =>
		!!detail.value?.reason?.trim() &&
		(detail.value?.evidence?.length || 0) +
			newEvidence.value.length +
			uploadedEvidence.value >=
			1 &&
		(detail.value?.evidence?.length || 0) +
			newEvidence.value.length +
			uploadedEvidence.value <=
			MAX_EVIDENCE &&
		videoLinkOk.value,
)

// ---------------------------------------------------------------------------
// List
// ---------------------------------------------------------------------------

async function loadList(reset = true) {
	if (!props.posProfile) return
	if (!reset && loading.value) return // Load More waits for the page in flight
	const offset = reset ? 0 : requests.value.length
	loading.value = true
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

// A filter click during a load must not be swallowed — reset loads always
// re-run with the latest filter.
function setStatusFilter(value) {
	if (statusFilter.value === value) return
	statusFilter.value = value
	loadList()
}

// ---------------------------------------------------------------------------
// New request
// ---------------------------------------------------------------------------

// local date string for <input type="date"> — toISOString would shift a day
// for any timezone east of UTC
function localDate() {
	const d = new Date()
	const m = String(d.getMonth() + 1).padStart(2, "0")
	const day = String(d.getDate()).padStart(2, "0")
	return `${d.getFullYear()}-${m}-${day}`
}

function openNew() {
	form.value = {
		incident_date: localDate(),
		// default = first purpose of the list; empty until loadPurposes lands
		purpose: purposes.value[0] || "",
		reason: "",
		video_link: "",
		items: [],
		files: [],
	}
	itemSearch.value = ""
	itemResults.value = []
	draftName.value = null
	uploadedEvidence.value = 0
	if (!purposes.value.length) loadPurposes()
	view.value = "new"
}

let searchTimer = null
let searchSeq = 0
// trailing-only debounce: one request per settled term; stale responses and
// in-flight searches after a selection are discarded via the sequence guard
function searchItems() {
	clearTimeout(searchTimer)
	const term = itemSearch.value.trim()
	if (!term) {
		// invalidate in-flight searches when the field is cleared
		searchSeq++
		itemResults.value = []
		return
	}
	searchTimer = setTimeout(() => doSearchItems(term), 350)
}

async function doSearchItems(term) {
	const seq = ++searchSeq // starting a request invalidates older ones
	try {
		const rows = await call("pos_next.api.items.get_items", {
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

function addItem(item) {
	if (!form.value.items.some((r) => r.item_code === item.item_code)) {
		form.value.items.push({ item_code: item.item_code, item_name: item.item_name || item.item_code, qty: 1 })
	}
	// invalidate any in-flight search so a late response can't repopulate
	searchSeq++
	clearTimeout(searchTimer)
	itemSearch.value = ""
	itemResults.value = []
}

function addEvidence(list, files) {
	const { ok, tooBig } = sanitizePickedFiles(files)
	if (tooBig.length) showError(__("Some files exceed the 10 MB limit"))
	const attached =
		view.value === "detail"
			? (detail.value?.evidence?.length || 0) + uploadedEvidence.value
			: 0
	const room = Math.max(0, MAX_EVIDENCE - attached - list.length)
	const accepted = ok.slice(0, room)
	if (ok.length > room) {
		showError(__("At most {0} evidence photos are allowed", [MAX_EVIDENCE]))
	}
	list.push(...accepted)
}

function addDestruction(files) {
	const { ok, tooBig } = sanitizePickedFiles(files)
	if (tooBig.length) showError(__("Some files exceed the 10 MB limit"))
	newDestruction.value.push(...ok)
}

async function toPayloads(files) {
	return Promise.all(
		files.map(async (f) => ({ file_name: f.name, filedata: await fileToBase64(f) })),
	)
}

async function attachFiles(name, kind, files) {
	if (!files.length) return 0
	await call("pos_next.api.reject_request.attach_reject_files", {
		name,
		kind,
		files: JSON.stringify(await toPayloads(files)),
	})
	return files.length
}

/**
 * Draft save → attach evidence → submit save. On any failure the draft name
 * is kept, so a retry updates the same draft instead of inserting another.
 * Pending files are cleared right after a successful attach so retries
 * never re-upload them. `getPending()` returns the pending files and clears
 * them when called with true.
 */
async function submitRequest(buildData, draftNameRef, getPending) {
	if (!draftNameRef.value) {
		const draft = await call("pos_next.api.reject_request.save_reject_request", {
			data: JSON.stringify(buildData()),
			submit: 0,
		})
		draftNameRef.value = draft.name
	}
	const name = draftNameRef.value
	const pending = getPending()
	uploadedEvidence.value += await attachFiles(name, "evidence", pending)
	getPending(true)
	return call("pos_next.api.reject_request.save_reject_request", {
		data: JSON.stringify({ ...buildData(), name }),
		submit: 1,
	})
}
async function submitNew() {
	if (!canSubmit.value || submitting.value) return
	submitting.value = true
	try {
		const buildData = () => ({
			pos_profile: props.posProfile,
			incident_date: form.value.incident_date,
			purpose: form.value.purpose,
			reason: form.value.reason.trim(),
			video_link: form.value.video_link.trim() || null,
			items: form.value.items.map((i) => ({ item_code: i.item_code, qty: i.qty })),
		})
		await submitRequest(
			buildData,
			draftName,
			(clear) => {
				const files = form.value.files
				if (clear) form.value.files = []
				return files
			},
		)
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
	view.value = "detail"
	loadingDetail.value = true
	draftName.value = null
	uploadedEvidence.value = 0
	uploadedDestruction.value = 0
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
	submitting.value = true
	try {
		const buildData = () => ({
			name: detail.value.name,
			reason: detail.value.reason.trim(),
			video_link: detail.value.video_link || null,
		})
		// the draft already exists — reuse its name, never insert again
		await submitRequest(
			buildData,
			draftName,
			(clear) => {
				const files = newEvidence.value
				if (clear) newEvidence.value = []
				return files
			},
		)
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
	if (!window.confirm(__("Cancel this reject request?"))) return
	acting.value = true
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
	if (!window.confirm(__("Close this reject request after destruction?"))) return
	acting.value = true
	try {
		uploadedDestruction.value += await attachFiles(
			detail.value.name,
			"destruction",
			newDestruction.value,
		)
		// clear before closing: a failed close retry must not re-upload
		newDestruction.value = []
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

// Task 6 deep link: when the dialog opens with a request name, jump
// straight into its detail view. Declared last so `view` exists before
// the immediate callback can touch it.
watch(
	[() => props.initialRequest, show],
	([name, open]) => {
		if (name && open) openDetail(name)
	},
	{ immediate: true },
)
</script>
