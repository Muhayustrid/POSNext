<template>
	<div class="min-h-screen bg-gray-50">
		<div class="mx-auto max-w-5xl px-3 py-4 sm:px-6 sm:py-6">
			<div class="mb-4 flex items-center justify-between gap-3">
				<h1 class="text-lg font-semibold text-gray-900 sm:text-xl">
					{{ __("Direct Print") }}
				</h1>
				<div class="flex items-center gap-2">
					<Badge v-if="currentDriver" :theme="statusOk ? 'green' : 'orange'">
						{{ statusOk ? __("Connected") : __("Not connected") }}
					</Badge>
					<Badge v-else theme="gray">{{ __("No driver") }}</Badge>
				</div>
			</div>

			<!-- Status card -->
			<div class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm sm:p-5">
				<div class="mb-3 flex items-center justify-between">
					<h2 class="text-sm font-semibold text-gray-900">
						{{ __("Printer status") }}
					</h2>
					<span
						v-if="statusLoading"
						class="text-xs text-gray-500"
					>{{ __("Checking...") }}</span>
				</div>

				<div v-if="statusError" class="rounded bg-red-50 px-3 py-2 text-sm text-red-700">
					{{ statusError }}
				</div>

				<div v-if="configError" class="rounded bg-amber-50 px-3 py-2 text-sm text-amber-700">
					{{ configError }}
				</div>

				<dl v-else-if="currentDriver" class="grid grid-cols-1 gap-3 text-sm sm:grid-cols-3">
					<div>
						<dt class="text-xs font-medium uppercase tracking-wide text-gray-500">
							{{ __("Driver") }}
						</dt>
						<dd class="mt-1 font-medium text-gray-900">{{ currentDriver.label }}</dd>
						<dd class="text-xs text-gray-500">{{ currentDriver.detail }}</dd>
						<dd class="text-xs text-gray-400">
							{{ __("ID: {0}", [currentDriver.id]) }}
						</dd>
					</div>
					<div>
						<dt class="text-xs font-medium uppercase tracking-wide text-gray-500">
							{{ __("Connection") }}
						</dt>
						<dd class="mt-1">
							<span
								:class="[
									'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium',
									statusOk ? 'bg-green-50 text-green-700' : 'bg-amber-50 text-amber-700',
								]"
							>
								<span
									:class="[
										'h-2 w-2 rounded-full',
										statusOk ? 'bg-green-500' : 'bg-amber-500',
									]"
								/>
								{{ statusOk ? __("Ready") : __("Unavailable") }}
							</span>
						</dd>
						<dd class="mt-1 text-xs text-gray-500">
							{{ __("Code: {0}", [String(statusCode)]) }}
						</dd>
						<dd v-if="statusMessage" class="mt-1 text-xs text-red-600">
							{{ statusMessage }}
						</dd>
					</div>
					<div>
						<dt class="text-xs font-medium uppercase tracking-wide text-gray-500">
							{{ __("Paper") }}
						</dt>
						<dd class="mt-1 text-gray-900">
							{{ transportPaperLabel }}
						</dd>
						<dd class="text-xs text-gray-500">
							{{ __("Fallback: {0}", [fallbackEnabled ? __("On") : __("Off")]) }}
						</dd>
					</div>
				</dl>
				<p v-else class="text-sm text-gray-500">
					{{ __("No driver information available.") }}
				</p>

				<p class="mt-3 text-xs text-gray-400">
					{{
						__("Status is polled every 3 seconds. Values reflect the transport driver and its live availability.")
					}}
				</p>
			</div>

			<!-- Device config card -->
			<div class="mt-4 rounded-lg border border-gray-200 bg-white p-4 shadow-sm sm:mt-6 sm:p-5">
				<h2 class="text-sm font-semibold text-gray-900">
					{{ __("Device config") }}
				</h2>
				<p class="mt-1 text-xs text-gray-500">
					{{
						__(
							"Stored in this device's localStorage. Applied on the next print — no page format call is made when saving.",
						)
					}}
				</p>

				<div class="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
					<div>
						<label class="mb-1 block text-xs font-medium text-gray-700" for="direct-print-host">
							{{ __("iMin host") }}
						</label>
						<Input
							id="direct-print-host"
							v-model="cfg.host"
							:placeholder="__('127.0.0.1')"
						/>
						<p class="mt-1 text-xs text-gray-400">
							{{ __("Hostname or IP for the iMin service. Port is fixed to 8081.") }}
						</p>
					</div>

					<div>
						<label class="mb-1 block text-xs font-medium text-gray-700" for="direct-print-paper">
							{{ __("Paper") }}
						</label>
						<Select
							id="direct-print-paper"
							v-model="cfg.paper"
							:options="paperOptions"
							:placeholder="__('Choose paper')"
						/>
						<p class="mt-1 text-xs text-gray-400">
							{{ __("58mm = 384 dots, 80mm = 576 dots, custom = use dot count below.") }}
						</p>
					</div>

					<div v-if="cfg.paper === 'custom'">
						<label class="mb-1 block text-xs font-medium text-gray-700" for="direct-print-custom-dots">
							{{ __("Custom dots") }}
						</label>
						<Input
							id="direct-print-custom-dots"
							v-model="customDotsText"
							type="number"
							:placeholder="__('384')"
						/>
						<p class="mt-1 text-xs text-gray-400">
							{{ __("Multiple of 8, 8–576. Snapped down to the nearest multiple of 8.") }}
						</p>
					</div>

					<div class="flex items-end pb-1">
						<Checkbox
							v-model="cfg.cut"
							:label="__('Cut paper after print (partial cut)')"
						/>
					</div>

					<div>
						<label class="mb-1 block text-xs font-medium text-gray-700" for="direct-print-copies">
							{{ __("Copies per transaction") }}
						</label>
						<Select
							id="direct-print-copies"
							v-model="cfg.copies"
							:options="copiesOptions"
						/>
						<p class="mt-1 text-xs text-gray-400">
							{{ __("1 = one receipt. 2 = customer + crew.") }}
						</p>
					</div>

					<div v-if="Number(cfg.copies) > 1">
						<label class="mb-1 block text-xs font-medium text-gray-700" for="direct-print-copy-delay">
							{{ __("Delay between copies (ms)") }}
						</label>
						<Input
							id="direct-print-copy-delay"
							v-model="copyDelayText"
							type="text"
							inputmode="numeric"
							:placeholder="__('800')"
						/>
						<p class="mt-1 text-xs text-gray-400">
							{{ __("Tear-off pause so the first copy can be removed. Default 800.") }}
						</p>
					</div>
				</div>

				<div class="mt-4 flex items-center gap-2">
					<Button
						variant="solid"
						:loading="saving"
						@click="onSaveConfig"
					>
						{{ __("Save") }}
					</Button>
					<Button variant="ghost" @click="reloadConfig">
						{{ __("Reset") }}
					</Button>
				</div>
			</div>

			<!-- Test print -->
			<div class="mt-4 rounded-lg border border-gray-200 bg-white p-4 shadow-sm sm:mt-6 sm:p-5">
				<h2 class="text-sm font-semibold text-gray-900">
					{{ __("Test print") }}
				</h2>
				<p class="mt-1 text-xs text-gray-500">
					{{
						__(
							"Builds a small test receipt and sends it through the transport exactly as a real print does. On a non-iMin machine a connection error is expected and will appear in the recent attempts below.",
						)
					}}
				</p>
				<div class="mt-4">
					<Button
						variant="solid"
						:loading="printing"
						@click="onTestPrint"
					>
						{{ __("Test Print") }}
					</Button>
				</div>
			</div>

			<!-- Recent attempts -->
			<div class="mt-4 rounded-lg border border-gray-200 bg-white p-4 shadow-sm sm:mt-6 sm:p-5">
				<div class="flex items-center justify-between gap-2">
					<h2 class="text-sm font-semibold text-gray-900">
						{{ __("Recent attempts") }}
					</h2>
					<Button variant="ghost" :loading="logsLoading" @click="fetchLogs">
						{{ __("Refresh") }}
					</Button>
				</div>

				<div v-if="logsLoading && logs.length === 0" class="py-8 text-center text-sm text-gray-500">
					{{ __("Loading...") }}
				</div>

				<div
					v-else-if="logsError"
					class="mt-3 rounded bg-red-50 px-3 py-2 text-sm text-red-700"
				>
					{{ logsError }}
				</div>

				<div
					v-else-if="logs.length === 0"
					class="mt-3 rounded border border-dashed border-gray-200 bg-gray-50 px-3 py-6 text-center text-sm text-gray-500"
				>
					{{ __("No print attempts yet.") }}
				</div>

				<div v-else class="mt-3 overflow-x-auto">
					<table class="min-w-full text-left text-sm">
						<thead>
							<tr class="border-b border-gray-200 text-xs font-medium uppercase tracking-wide text-gray-500">
								<th class="px-2 py-2">{{ __("Time") }}</th>
								<th class="px-2 py-2">{{ __("Reference") }}</th>
								<th class="px-2 py-2">{{ __("Driver") }}</th>
								<th class="px-2 py-2">{{ __("Status") }}</th>
								<th class="px-2 py-2">{{ __("Error") }}</th>
							</tr>
						</thead>
						<tbody>
							<tr
								v-for="row in logs"
								:key="row.name"
								class="border-b border-gray-100 last:border-0"
							>
								<td class="whitespace-nowrap px-2 py-2 text-xs text-gray-600">
									{{ formatTime(row.creation) }}
								</td>
								<td class="whitespace-nowrap px-2 py-2 text-gray-900">
									{{ row.reference_name || "-" }}
								</td>
								<td class="whitespace-nowrap px-2 py-2">
									<Badge :theme="driverBadgeTheme(row.driver)">
										{{ row.driver || "-" }}
									</Badge>
								</td>
								<td class="whitespace-nowrap px-2 py-2">
									<Badge :theme="statusBadgeTheme(row.status)">
										{{ row.status || "-" }}
									</Badge>
								</td>
								<td class="max-w-[22rem] px-2 py-2 text-xs text-gray-600">
									<span
										class="block truncate"
										:title="row.error_message || ''"
									>{{ row.error_message || "-" }}</span>
								</td>
							</tr>
						</tbody>
					</table>
				</div>

				<p class="mt-3 text-xs text-gray-400">
					{{ __("Showing the last 50 attempts.") }}
				</p>
			</div>
		</div>
	</div>
</template>

<script setup>
import { Badge, Button, Checkbox, Input, Select } from "frappe-ui"
import { computed, onMounted, onUnmounted, reactive, ref, watch } from "vue"

import { useToast } from "@/composables/useToast"
import { call } from "@/utils/apiWrapper"
import { loadDeviceConfig, saveDeviceConfig } from "@/utils/print/imin_client"
import { PAPER_PROFILES } from "@/utils/print/paper"
import { useBootstrapStore } from "@/stores/bootstrap"
import {
	ensureIminSdk,
	getTransport,
	initTransportFromServer,
	printHTML as transportPrint,
} from "@/utils/print/transport"
import { buildReceiptDocumentHTML } from "@/utils/printInvoice"

const { showSuccess, showError, showInfo } = useToast()
const bootstrap = useBootstrapStore()

const currentDriver = ref(null)
const statusOk = ref(false)
const statusCode = ref(-1)
const statusMessage = ref("")
const statusError = ref("")
const configError = ref("")
const statusLoading = ref(true)

const transportPaperLabel = ref("-")
const fallbackEnabled = ref(true)

const saving = ref(false)
const printing = ref(false)

const logs = ref([])
const logsLoading = ref(false)
const logsError = ref("")

const paperOptions = computed(() => {
	const base = Object.keys(PAPER_PROFILES).map((k) => ({
		label: PAPER_PROFILES[k].label,
		value: k,
	}))
	return [...base, { label: "custom", value: "custom" }]
})

const copiesOptions = [1, 2, 3, 4, 5].map((n) => ({
	label: String(n),
	value: n,
}))

const cfg = reactive({
	host: "",
	paper: "58mm",
	cut: false,
	copies: 1,
})
// Two copies printing (customer + outlet crew). Device-level delay
// overrides the POS Settings value — same override pattern as `paper`/
// `cut`, but specific to multi-copy jobs. Raw text so the field can be
// empty (meaning 'use server default 800ms').
const copyDelayText = ref("800")
const customDotsText = ref("384")

function readCfgIntoForm() {
	const stored = loadDeviceConfig() || {}
	cfg.host = typeof stored.host === "string" ? stored.host : ""
	const p = stored.paper
	cfg.paper = p === "58mm" || p === "80mm" || p === "custom" ? p : "58mm"
	cfg.cut = Boolean(stored.cut)
	const n = Number(stored.copies)
	cfg.copies = Number.isFinite(n) && n >= 1 && n <= 5 ? Math.floor(n) : 1
	const cd = stored.customDots
	customDotsText.value = cd != null && cd !== "" ? String(cd) : "384"
	const d = stored.copyDelayMs
	copyDelayText.value =
		d === undefined || d === "" || d === null ? "800" : String(d)
}

function reloadConfig() {
	readCfgIntoForm()
	showInfo(__("Device config reloaded from this browser."))
}

function transportSnapshot() {
	try {
		const t = getTransport()
		const d = t.getDriver()
		currentDriver.value = d ? d.describe() : null
		const c = t.getConfig ? t.getConfig() : {}
		transportPaperLabel.value = c.paper || "-"
		fallbackEnabled.value = c.fallback_enabled !== false
	} catch (e) {
		currentDriver.value = null
		statusError.value = e?.message || String(e)
	}
}

async function pollStatus() {
	statusError.value = ""
	try {
		const t = getTransport()
		const driver = t.getDriver()
		if (!driver || typeof driver.getStatus !== "function") {
			statusOk.value = false
			statusCode.value = -1
			statusMessage.value = ""
			return
		}
		const s = await driver.getStatus()
		statusOk.value = Boolean(s?.ok)
		statusCode.value = s?.code ?? -1
		statusMessage.value = s?.message || ""
	} catch (e) {
		statusOk.value = false
		statusCode.value = -1
		statusMessage.value = e?.message || String(e)
	} finally {
		statusLoading.value = false
	}
}

let timer = null

function onSaveConfig() {
	saving.value = true
	try {
		const host = (cfg.host || "").trim()
		const paper = cfg.paper
		let customDots = undefined
		if (paper === "custom") {
			const n = Number(customDotsText.value)
			if (!Number.isFinite(n))
				throw new Error(__("Custom dots must be a number."))
			customDots = n
		}
		if (
			paper === "custom" &&
			(customDots == null || String(customDots).trim() === "")
		) {
			throw new Error(__("Custom dots is required when paper is custom."))
		}
		saveDeviceConfig({
			host: host || undefined,
			paper,
			customDots,
			cut: Boolean(cfg.cut),
			copies: Math.max(1, Math.min(Number(cfg.copies) || 1, 5)),
			copyDelayMs:
				copyDelayText.value === "" || copyDelayText.value == null
					? 800
					: Math.max(0, Math.min(Number(copyDelayText.value) || 0, 10000)),
		})
		// Do not call setPageFormat directly — imin_client applies it on next print.
		showSuccess(__("Device config saved. It will apply on the next print."))
		transportSnapshot()
	} catch (e) {
		showError(e?.message || String(e))
	} finally {
		saving.value = false
	}
}

function buildTestInvoiceData() {
	const now = new Date()
	return {
		name: "TEST",
		posting_date: now.toISOString(),
		company: __("POS Next"),
		header: __("TEST PRINT"),
		customer: __("Walk-in Customer"),
		customer_name: __("Walk-in Customer"),
		grand_total: 10000,
		total_taxes_and_charges: 0,
		discount_amount: 0,
		items: [
			{
				item_code: "TEST-ITEM",
				item_name: __("Test Item"),
				qty: 1,
				quantity: 1,
				rate: 10000,
				price_list_rate: 10000,
			},
		],
		payments: [{ mode_of_payment: __("Cash"), amount: 10000 }],
		footer: __("Test print — Direct Print"),
	}
}

async function onTestPrint() {
	printing.value = true
	try {
		const html = buildReceiptDocumentHTML(buildTestInvoiceData(), {
			includeControls: false,
		})
		await transportPrint(html, {
			logContext: {
				reference_doctype: "Sales Invoice",
				reference_name: "TEST",
			},
		})
		showSuccess(__("Test print sent."))
		await fetchLogs()
		await pollStatus()
	} catch (e) {
		showError(e?.message || String(e))
		await fetchLogs()
	} finally {
		printing.value = false
	}
}

async function fetchLogs() {
	logsLoading.value = true
	logsError.value = ""
	try {
		const rows = await call("pos_next.api.printing.get_print_logs", {
			limit: 50,
		})
		logs.value = Array.isArray(rows) ? rows : []
	} catch (e) {
		logsError.value = e?.message || String(e)
	} finally {
		logsLoading.value = false
	}
}

function driverBadgeTheme(v) {
	if (v === "imin") return "blue"
	if (v === "qz") return "orange"
	if (v === "browser") return "gray"
	return "gray"
}

function statusBadgeTheme(v) {
	if (v === "Success") return "green"
	if (v === "Fallback") return "orange"
	if (v === "Failed") return "red"
	return "gray"
}

function formatTime(v) {
	if (!v) return "-"
	try {
		return new Date(v).toLocaleString()
	} catch {
		return String(v)
	}
}

watch(
	() => cfg.paper,
	(v, prev) => {
		if (v === "custom" && prev !== "custom" && !customDotsText.value) {
			customDotsText.value = "384"
		}
	},
)

onMounted(async () => {
	readCfgIntoForm()
	// Load the server print config before anything reads transport state.
	// Test Print calls the module-level transportPrint, which relies on the
	// singleton config populated here; without it the chain is empty and no
	// driver can be reached. The store call is idempotent — awaiting it closes
	// the race with the non-blocking preload started in main.js.
	try {
		await bootstrap.loadInitialData()
		const cfg = await initTransportFromServer(
			bootstrap.getPreloadedPOSProfile()?.name || null,
		)
		configError.value = ""
		// Load the iMin SDK before the first status poll whenever the chain can
		// reach it. Without this the very first poll throws "not a constructor"
		// and the card reads "Unavailable / code -1" on a perfectly healthy
		// device — misleading for anyone diagnosing a till.
		if (cfg?.driver === "imin" || cfg?.fallback_enabled !== false) {
			await ensureIminSdk()
		}
	} catch (e) {
		configError.value = __("Could not load print config from the server: {0}", [
			e?.message || String(e),
		])
	}
	transportSnapshot()
	await pollStatus()
	timer = window.setInterval(pollStatus, 3000)
	await fetchLogs()
})

onUnmounted(() => {
	if (timer) window.clearInterval(timer)
	timer = null
})
</script>
