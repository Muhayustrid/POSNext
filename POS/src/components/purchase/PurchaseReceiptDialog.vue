<template>
	<DialogHost
		v-model:show="show"
		:embedded="embedded"
		:options="{ title: dialogTitle, size: '4xl' }"
	>
		<template #body-content>
			<div v-if="view === 'doc'" class="mx-auto flex w-full max-w-6xl flex-col gap-3">
				<PurchaseDocView
					:title="__('Purchase Receipt')"
					kind="pr"
					:doc="currentDoc"
					:loading="loadingDoc"
					:can-attach="canAttach"
					:pending-files="[]"
					@back="goList"
					@attach-add="onAttachAdd"
					@open-erpnext="openInErpnext(currentDoc)"
				>
					<template #actions>
						<Button
							v-if="canSubmitPR && currentDoc?.editable && (currentDoc?.submit_actions?.length || 0) <= 1"
							variant="solid"
							data-testid="primary-action"
							:loading="submitting"
							@click="submitDoc(currentDoc?.submit_actions?.[0])"
						>
							{{ currentDoc?.submit_actions?.[0] || __("Submit") }}
						</Button>
						<div
							v-else-if="canSubmitPR && currentDoc?.editable && (currentDoc?.submit_actions?.length || 0) > 1"
							class="relative"
						>
							<Button
								variant="solid"
								data-testid="primary-action"
								@click="actionsMenuOpen = !actionsMenuOpen"
							>
								{{ __("Actions") }}
								<template #suffix>
									<FeatherIcon name="chevron-down" class="h-4 w-4" />
								</template>
							</Button>
							<div
								v-if="actionsMenuOpen"
								data-testid="actions-menu"
								class="absolute end-0 z-20 mt-1 w-40 rounded-lg border border-gray-200 bg-white py-1 shadow-lg"
							>
								<button
									v-for="a in currentDoc.submit_actions"
									:key="a"
									type="button"
									class="block w-full px-3 py-2 text-start text-sm text-gray-700 hover:bg-gray-50"
									@click="submitDoc(a)"
								>
									{{ a }}
								</button>
							</div>
						</div>

						<div v-if="currentDoc?.name" class="relative">
							<button
								type="button"
								data-testid="doc-menu-button"
								class="flex h-8 w-8 items-center justify-center rounded text-gray-500 hover:bg-gray-100 hover:text-gray-900"
								:aria-label="__('More actions')"
								:title="__('More actions')"
								@click="docMenuOpen = !docMenuOpen"
							>
								<FeatherIcon name="more-horizontal" class="h-4 w-4" />
							</button>
							<div
								v-if="docMenuOpen"
								data-testid="doc-menu"
								class="absolute end-0 z-20 mt-1 w-40 rounded-lg border border-gray-200 bg-white py-1 shadow-lg"
							>
								<button
									type="button"
									data-testid="doc-menu-open-erpnext"
									class="block w-full px-3 py-2 text-start text-sm text-gray-700 hover:bg-gray-50"
									@click="openInErpnext(currentDoc); docMenuOpen = false"
								>
									{{ __("Open in ERPNext") }}
								</button>
							</div>
						</div>
					</template>
				</PurchaseDocView>
			</div>

			<div v-else class="mx-auto flex w-full max-w-6xl flex-col gap-3">
				<p class="text-sm text-gray-500">
					{{ __("Goods received from suppliers and the factory") }}
				</p>

				<div class="flex gap-2">
					<div class="relative flex-1">
						<FeatherIcon
							name="search"
							class="pointer-events-none absolute start-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400"
						/>
						<input
							v-model="searchTerm"
							type="text"
							data-test="receipt-list-search"
							:placeholder="__('Search receipt or supplier...')"
							class="w-full rounded-lg border border-gray-300 py-2 pe-3 ps-9 text-sm focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30"
							@input="onSearch"
						/>
					</div>
					<RefreshButton :loading="loadingReceipts" @click="loadReceipts" />
				</div>

				<div class="-mx-1 flex gap-1 overflow-x-auto px-1 pb-0.5" role="group" :aria-label="__('Status')">
					<button
						v-for="chip in STATUS_CHIPS"
						:key="chip.value"
						type="button"
						class="shrink-0 rounded-full border px-3 py-1 text-xs font-medium transition-colors"
						:class="
							statusFilter === chip.value
								? 'border-gray-900 bg-gray-900 text-white'
								: 'border-gray-200 bg-white text-gray-600 hover:border-gray-300 hover:text-gray-900'
						"
						:aria-pressed="statusFilter === chip.value"
						@click="setStatus(chip.value)"
					>
						{{ chip.label }}
					</button>
				</div>

				<div class="rounded-xl border border-gray-200 bg-white">
					<!-- desktop column header; rows below share the same grid -->
					<div
						class="hidden border-b border-gray-200 bg-gray-50 px-4 py-2 text-xs font-medium text-gray-500 md:grid md:gap-x-3"
						:class="
							showPrices
								? 'md:grid-cols-[minmax(0,1.4fr)_minmax(0,1.6fr)_8.5rem_minmax(0,1.2fr)_10rem_2.5rem]'
								: 'md:grid-cols-[minmax(0,1.4fr)_minmax(0,1.6fr)_8.5rem_10rem_2.5rem]'
						"
					>
						<span>{{ __("Receipt No") }}</span>
						<span>{{ __("Supplier") }}</span>
						<span>{{ __("Posting Date") }}</span>
						<span v-if="showPrices" class="text-end">{{ __("Grand Total") }}</span>
						<span>{{ __("Status") }}</span>
						<span></span>
					</div>

					<div v-if="loadingReceipts" class="divide-y divide-gray-100" data-test="list-loading">
						<div v-for="n in 3" :key="n" class="flex animate-pulse gap-4 px-4 py-3">
							<div class="h-4 w-28 rounded bg-gray-100"></div>
							<div class="h-4 flex-1 rounded bg-gray-100"></div>
							<div class="h-4 w-20 rounded bg-gray-100"></div>
						</div>
					</div>
					<div v-else-if="receipts.length === 0" class="px-4 py-6 text-center">
						<p class="text-sm font-medium text-gray-900">{{ __("No purchase receipts") }}</p>
						<p class="mt-1 text-xs text-gray-500">
							{{
								searchTerm || statusFilter
									? __("Try a different search or status")
									: __("Receipts appear here once goods are received")
							}}
						</p>
					</div>
					<div v-else class="divide-y divide-gray-100">
						<div
							v-for="receipt in receipts"
							:key="receipt.name"
							tabindex="0"
							class="cursor-pointer px-4 py-3 transition-colors hover:bg-gray-50 focus:outline-none focus-visible:bg-gray-50"
							:data-test="`pr-${receipt.name}`"
							@click="openDoc(receipt.name)"
							@keydown.enter="openDoc(receipt.name)"
						>
							<div
								class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 md:gap-y-0 md:gap-x-3"
								:class="
									showPrices
										? 'md:grid-cols-[minmax(0,1.4fr)_minmax(0,1.6fr)_8.5rem_minmax(0,1.2fr)_10rem_2.5rem]'
										: 'md:grid-cols-[minmax(0,1.4fr)_minmax(0,1.6fr)_8.5rem_10rem_2.5rem]'
								"
							>
								<div class="flex min-w-0 items-center gap-1.5 md:order-1">
									<FeatherIcon
										name="chevron-right"
										class="h-3.5 w-3.5 shrink-0 text-gray-400"
									/>
									<span class="truncate text-sm font-semibold text-gray-900">{{ receipt.name }}</span>
									<span
										v-if="receipt.attachment_count"
										class="inline-flex shrink-0 items-center gap-0.5 text-xs text-gray-500"
										:title="__('Attachments')"
									>
										<FeatherIcon name="paperclip" class="h-3 w-3" />
										{{ receipt.attachment_count }}
									</span>
								</div>
								<!-- status sits top-right on cards, in its own column on desktop -->
								<div class="justify-self-end md:order-5 md:justify-self-start">
									<span :class="statusPill(receipt.workflow_state || receipt.status)">
										{{ __(receipt.workflow_state || receipt.status) }}
									</span>
								</div>
								<p class="col-span-2 truncate text-sm text-gray-700 md:order-2 md:col-span-1">
									{{ receipt.supplier_name || receipt.supplier }}
								</p>
								<p class="text-xs text-gray-500 md:order-3 md:text-sm md:text-gray-700">
									{{ formatDate(receipt.posting_date) }}
								</p>
								<div v-if="showPrices" class="text-end md:order-4">
									<p
										v-if="'grand_total' in receipt && receipt.grand_total != null"
										class="text-sm font-medium tabular-nums text-gray-900"
									>
										{{ formatMoney(receipt) }}
									</p>
									<p v-if="receipt.docstatus === 1" class="text-xs text-gray-500">
										{{ __("Billed {0}%", [Math.round(receipt.per_billed || 0)]) }}
									</p>
								</div>
								<div class="hidden justify-end md:order-6 md:flex" @click.stop>
									<button
										type="button"
										class="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700"
										:title="__('Open in ERPNext')"
										:aria-label="__('Open in ERPNext')"
										@click="openInErpnext(receipt)"
									>
										<FeatherIcon name="external-link" class="h-3.5 w-3.5" />
									</button>
								</div>
							</div>
						</div>
					</div>
				</div>
			</div>
		</template>
	</DialogHost>

	<ConfirmDialog
		v-model="confirm.open"
		:title="confirm.title"
		:message="confirm.message"
		:confirm-label="confirm.confirmLabel"
		:destructive="confirm.destructive"
		@confirm="resolveConfirm(true)"
		@cancel="resolveConfirm(false)"
	/>
</template>

<script setup>
import { Button, FeatherIcon } from "frappe-ui"
import { computed, onBeforeUnmount, reactive, ref, watch } from "vue"
import ConfirmDialog from "@/components/common/ConfirmDialog.vue"
import DialogHost from "@/components/common/DialogHost.js"
import RefreshButton from "@/components/common/RefreshButton.vue"
import PurchaseDocView from "@/components/purchase/PurchaseDocView.vue"
import { useFormatters } from "@/composables/useFormatters"
import { usePermissions } from "@/composables/usePermissions"
import { useToast } from "@/composables/useToast"
import { sanitizePickedFiles, uploadPurchaseFiles } from "@/utils/attachments"
import { call, serverErrorMessage } from "@/utils/apiWrapper"
import { parseError } from "@/utils/errorHandler"
import { __ } from "@/utils/translation"

const props = defineProps({
	embedded: { type: Boolean, default: false },
	modelValue: { type: Boolean, default: true },
	posProfile: { type: String, default: null },
})

const emit = defineEmits(["update:modelValue"])

const PR_API = "pos_next.api.purchase_receipts"

const STATUS_CHIPS = [
	{ value: "", label: __("All") },
	{ value: "Draft", label: __("Draft") },
	{ value: "To Bill", label: __("To Bill") },
	{ value: "Completed", label: __("Completed") },
	{ value: "Cancelled", label: __("Cancelled") },
]

const STATUS_TONES = {
	Draft: "bg-orange-50 text-orange-700 ring-orange-200",
	"To Bill": "bg-purple-50 text-purple-700 ring-purple-200",
	Completed: "bg-green-50 text-green-700 ring-green-200",
	Cancelled: "bg-red-50 text-red-700 ring-red-200",
}

const { showSuccess, showError } = useToast()
const { formatDate } = useFormatters()
const { usePermissionCheck } = usePermissions()
const { hasPermission: canSubmitPR } = usePermissionCheck(
	"Purchase Receipt",
	"submit",
)

const show = ref(props.modelValue)
watch(show, (val) => emit("update:modelValue", val))

const view = ref("list") // "list" | "doc"
const currentDoc = ref(null)
const loadingDoc = ref(false)
const submitting = ref(false)
const actionsMenuOpen = ref(false)
const docMenuOpen = ref(false)

const canAttach = computed(() => currentDoc.value?.docstatus === 0)

const receipts = ref([])
const showPrices = computed(() =>
	receipts.value.some((r) => "grand_total" in r && r.grand_total != null),
)
const loadingReceipts = ref(false)
const searchTerm = ref("")
const statusFilter = ref("")
let searchTimer = null

onBeforeUnmount(() => clearTimeout(searchTimer))

watch(
	() => props.modelValue,
	(val) => {
		show.value = val
		if (val) {
			view.value = "list"
			loadReceipts()
		} else {
			clearTimeout(searchTimer)
		}
	},
	{ immediate: true },
)

const dialogTitle = computed(() => __("Purchase Receipt"))

// ---------------------------------------------------------------------------
// Confirmation Dialog
// ---------------------------------------------------------------------------
const confirm = reactive({
	open: false,
	title: "",
	message: "",
	confirmLabel: "",
	destructive: false,
})
let confirmResolve = null

function confirmAsk({
	title,
	message = "",
	confirmLabel = "",
	destructive = false,
}) {
	confirm.title = title
	confirm.message = message
	confirm.confirmLabel = confirmLabel
	confirm.destructive = destructive
	confirm.open = true
	return new Promise((resolve) => {
		confirmResolve = resolve
	})
}

function resolveConfirm(value) {
	confirm.open = false
	if (confirmResolve) {
		confirmResolve(value)
		confirmResolve = null
	}
}

function statusPill(status) {
	return [
		"inline-flex shrink-0 items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset",
		STATUS_TONES[status] || "bg-blue-50 text-blue-700 ring-blue-200",
	]
}

function formatMoney(receipt) {
	const currency = receipt.currency || "Rp"
	return `${currency} ${Number(receipt.grand_total || 0).toLocaleString("id-ID")}`
}

async function openDoc(name) {
	view.value = "doc"
	loadingDoc.value = true
	actionsMenuOpen.value = false
	docMenuOpen.value = false
	currentDoc.value = { name }
	try {
		const res = await call(`${PR_API}.get_purchase_receipt`, { name })
		currentDoc.value = res || { name }
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	} finally {
		loadingDoc.value = false
	}
}

function goList() {
	view.value = "list"
	currentDoc.value = null
	actionsMenuOpen.value = false
	docMenuOpen.value = false
}

async function reloadDoc() {
	if (!currentDoc.value?.name) return
	try {
		const res = await call(`${PR_API}.get_purchase_receipt`, {
			name: currentDoc.value.name,
		})
		currentDoc.value = res || currentDoc.value
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	}
}

async function submitDoc(action) {
	if (submitting.value || !currentDoc.value?.name) return
	actionsMenuOpen.value = false
	const name = currentDoc.value.name
	const ok = await confirmAsk({
		title: __("Submit Purchase Receipt"),
		message: __(
			"Submit {0}? Stock will be updated and the receipt can no longer be edited.",
			[name],
		),
		confirmLabel: action || __("Submit"),
	})
	if (!ok) return
	submitting.value = true
	try {
		const res = await call(`${PR_API}.submit_purchase_receipt`, {
			name,
			action: action || undefined,
		})
		showSuccess(__("Purchase Receipt {0} submitted", [res?.name || name]))
		await reloadDoc()
		await loadReceipts()
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	} finally {
		submitting.value = false
	}
}

async function onAttachAdd(files) {
	if (!currentDoc.value?.name) return
	const { ok, tooBig } = sanitizePickedFiles(files)
	if (tooBig.length) {
		showError(__("File {0} exceeds the {1} MB limit", [tooBig[0].name, 10]))
	}
	if (!ok.length) return
	try {
		await uploadPurchaseFiles("Purchase Receipt", currentDoc.value.name, ok)
		showSuccess(__("Files attached"))
		await reloadDoc()
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	}
}

async function loadReceipts() {
	loadingReceipts.value = true
	try {
		const res = await call(`${PR_API}.get_purchase_receipts`, {
			pos_profile: props.posProfile,
			status: statusFilter.value || null,
			search_term: searchTerm.value || null,
			limit: 50,
		})
		receipts.value = res?.receipts || []
	} catch (error) {
		showError(parseError(error)?.message || serverErrorMessage(error))
	} finally {
		loadingReceipts.value = false
	}
}

function onSearch() {
	clearTimeout(searchTimer)
	searchTimer = setTimeout(loadReceipts, 300)
}

function setStatus(value) {
	statusFilter.value = value
	loadReceipts()
}

function openInErpnext(receipt) {
	const name = typeof receipt === "string" ? receipt : receipt?.name
	if (name) {
		window.open(`/app/purchase-receipt/${name}`, "_blank")
	}
}
</script>
