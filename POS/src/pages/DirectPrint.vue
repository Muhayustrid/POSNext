<template>
	<div class="min-h-screen bg-gray-50">
		<div class="mx-auto max-w-5xl px-3 py-4 sm:px-6 sm:py-6">
			<div class="mb-4 flex items-center justify-between gap-3">
				<h1 class="text-lg font-semibold text-gray-900 sm:text-xl">
					{{ __("Direct Print") }}
					<span
						v-if="buildLabel"
						class="ml-2 align-middle text-[10px] font-normal text-gray-400"
						:title="__('Build executed by this page — if it is old, the service worker served a stale bundle')"
					>build {{ buildLabel }}</span>
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

					<div>
						<label class="mb-1 block text-xs font-medium text-gray-700" for="direct-print-feed">
							{{ __("Paper advance per copy (dots)") }}
						</label>
						<Input
							id="direct-print-feed"
							v-model="feedDotsText"
							type="text"
							inputmode="numeric"
							:placeholder="__('160')"
						/>
						<p class="mt-1 text-xs text-gray-400">
							{{
								__(
									"Distance the paper moves after each copy, 0.125 mm per dot. 160 = 20 mm. Together with the tail spacer this is the gap between the last printed line and the tear bar.",
								)
							}}
						</p>
					</div>

					<div>
						<label class="mb-1 block text-xs font-medium text-gray-700" for="direct-print-tail">
							{{ __("Tail spacer (dots)") }}
						</label>
						<Input
							id="direct-print-tail"
							v-model="tailDotsText"
							type="text"
							inputmode="numeric"
							:placeholder="__('24')"
						/>
						<p class="mt-1 text-xs text-gray-400">
							{{
								__(
									"Blank space added inside the image itself, below the last line. Unlike the paper advance this cannot be clamped away by the printer service. 24 = 3 mm.",
								)
							}}
						</p>
					</div>

					<div>
						<label class="mb-1 block text-xs font-medium text-gray-700" for="direct-print-font-scale">
							{{ __("Font scale (%)") }}
						</label>
						<Input
							id="direct-print-font-scale"
							v-model="fontScaleText"
							type="text"
							inputmode="numeric"
							:placeholder="__('100')"
						/>
						<p class="mt-1 text-xs text-gray-400">
							{{
								__(
									"Receipt text is authored at 96 DPI and translated to the 205 DPI print head, so it already prints at the size you see in a browser preview. Raise this only if it still reads small. 60–250.",
								)
							}}
						</p>
					</div>

					<div>
						<label class="mb-1 block text-xs font-medium text-gray-700" for="direct-print-crew-font-scale">
							{{ __("Crew copy font scale (%)") }}
						</label>
						<Input
							id="direct-print-crew-font-scale"
							v-model="crewFontScaleText"
							type="text"
							inputmode="numeric"
							:placeholder="__('100')"
						/>
						<p class="mt-1 text-xs text-gray-400">
							{{
								__(
									"Same knob, but only for the crew copy — the short order slip the outlet keeps. It starts bigger than the receipt because it is read across the counter. 60–250.",
								)
							}}
						</p>
					</div>

					<div>
						<label class="mb-1 block text-xs font-medium text-gray-700" for="direct-print-line-spacing">
							{{ __("Line spacing (%)") }}
						</label>
						<Input
							id="direct-print-line-spacing"
							v-model="lineSpacingText"
							type="text"
							inputmode="numeric"
							:placeholder="__('100')"
						/>
						<p class="mt-1 text-xs text-gray-400">
							{{
								__(
									"Vertical density of the printed output. 100 = as authored; lower (e.g. 80) closes up the gaps between lines without shrinking the text. 50–150.",
								)
							}}
						</p>
					</div>

					<div>
						<label class="mb-1 block text-xs font-medium text-gray-700" for="direct-print-side-margin">
							{{ __("Side margin (dots)") }}
						</label>
						<Input
							id="direct-print-side-margin"
							v-model="sideMarginDotsText"
							type="text"
							inputmode="numeric"
							:placeholder="__('16')"
						/>
						<p class="mt-1 text-xs text-gray-400">
							{{
								__(
									"Blank space on the left and right of every line, in dots (dots = mm×8, e.g. 16≈2mm). Overrides the padding the receipt format's own CSS sets on either side, so the text uses more of the paper width. 0–64.",
								)
							}}
						</p>
					</div>
				</div>

				<!-- What the NEXT print will actually use (device over server) -->
				<div
					v-if="effectiveCfg"
					class="mt-4 rounded bg-gray-50 px-3 py-2 text-xs text-gray-600"
				>
					<p class="mb-1 font-medium text-gray-700">
						{{ __("Effective config for the next print") }}
					</p>
					<p>
						{{
							__(
								"Paper {0} ({1} dots) · Copies {2} · Delay {3} ms · Advance {4} dots · Tail {5} dots · Font {6}% · Crew font {7}% · Line spacing {8}% · Side margin {9} dots",
								[
									String(effectiveCfg.paper),
									String(effectiveCfg.dots),
									String(effectiveCfg.copies),
									String(effectiveCfg.copyDelayMs),
									String(effectiveCfg.feedDots),
									String(effectiveCfg.tailDots),
									String(effectiveCfg.fontScale),
									String(effectiveCfg.crewFontScale),
									String(effectiveCfg.lineSpacing),
									String(effectiveCfg.sideMarginDots),
								],
							)
						}}
					</p>
					<p class="mt-1 text-gray-400">
						{{
							__(
								"Delay 0 means copies print back-to-back with no tear-off pause. Values here are what Test Print uses.",
							)
						}}
					</p>
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
							"Prints a sample receipt through the transport exactly as a real print does — the last invoice of this profile through the server receipt template when one is available, the built-in test receipt otherwise. On a non-iMin machine a connection error is expected and will appear in the recent attempts below.",
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
					<p
						v-if="sampleNote"
						class="mt-2 text-xs text-gray-400"
					>
						{{ sampleNote }}
					</p>
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

			<!-- Print preview: the same bitmap path that reaches the printer, no device needed. -->
			<div class="mt-4 rounded-lg border border-gray-200 bg-white p-4 shadow-sm sm:mt-6 sm:p-5">
				<h2 class="text-sm font-semibold text-gray-900">{{ __("Preview") }}</h2>
				<p class="mt-1 text-xs text-gray-500">
					{{
						__(
							"Renders the sample receipt through the same bitmap path a real print uses, at the same width, with the same tail spacer — copy 2 is the crew slip, at its own font scale, when the profile prints two. Nothing reaches the printer. What it cannot show is where the physical tear bar sits — that still needs the device.",
						)
					}}
				</p>

				<div class="mt-4 flex flex-wrap items-center gap-2">
					<Button :loading="previewing" @click="runPreview(1)">
						{{ __("Preview 1 copy") }}
					</Button>
					<Button :loading="previewing" @click="runPreview(2)">
						{{ __("Preview 2 copies") }}
					</Button>
					<Button v-if="previewCopies.length" variant="ghost" @click="clearPreview">
						{{ __("Clear") }}
					</Button>
				</div>

				<div v-if="previewError" class="mt-3 rounded bg-red-50 px-3 py-2 text-sm text-red-700">
					{{ previewError }}
				</div>

				<div v-else-if="previewing && !previewCopies.length" class="py-8 text-center text-sm text-gray-500">
					{{ __("Rendering...") }}
				</div>

				<div v-else-if="previewCopies.length" class="mt-4 flex flex-wrap items-start gap-6">
					<div v-for="row in previewCopies" :key="row.index" class="min-w-0">
						<div class="mb-1 flex items-center gap-2">
							<span class="text-xs font-medium text-gray-700">{{ row.label }}</span>
							<Badge v-if="!row.visible" theme="gray">
								{{ __("after {0} ms", [String(row.delayMs)]) }}
							</Badge>
						</div>
						<div
							class="overflow-x-auto rounded border border-gray-200 bg-neutral-100 p-2"
							:aria-label="row.label"
						>
							<img
								v-if="row.visible && row.bitmap"
								:src="row.bitmap.dataURL"
								:alt="row.label"
								class="block bg-white"
								:style="{ width: previewDots + 'px', maxWidth: 'none' }"
							/>
							<div
								v-else
								:style="{ width: previewDots + 'px' }"
								class="flex h-24 items-center justify-center text-xs text-gray-400"
							>
								{{ __("waiting {0} ms", [String(row.delayMs)]) }}
							</div>
						</div>
						<p v-if="row.bitmap" class="mt-1 text-xs text-gray-400">
							{{
								__("{0} x {1} dots", [
									String(row.bitmap.width),
									String(row.bitmap.height),
								])
							}}
						</p>
					</div>
				</div>
			</div>
		</div>
	</div>
</template>

<script setup>
import { Badge, Button, Checkbox, Input, Select } from "frappe-ui"
import { computed, onMounted, onUnmounted, reactive, ref, watch } from "vue"

import { useToast } from "@/composables/useToast"
import { call } from "@/utils/apiWrapper"
import { buildCrewSlipHTML } from "@/utils/print/crew_slip"
import { loadDeviceConfig, saveDeviceConfig } from "@/utils/print/imin_client"
import { PAPER_PROFILES } from "@/utils/print/paper"
import { buildReceiptPreviewSet } from "@/utils/print/receipt_preview"
import {
	DEFAULT_FEED_DOTS,
	DEFAULT_TAIL_DOTS,
	parseNumericField,
	resolvePrintConfig,
} from "@/utils/print/receipt_layout"
import { fetchSampleReceiptBundle } from "@/utils/print/sample_receipt"
import { useBootstrapStore } from "@/stores/bootstrap"
import {
	ensureIminSdk,
	getTransport,
	initTransportFromServer,
	printHTML as transportPrint,
} from "@/utils/print/transport"
import {
	buildReceiptDocumentHTML,
	effectiveReceiptDots,
} from "@/utils/printInvoice"

const { showSuccess, showError, showInfo } = useToast()

// Compile-time constant injected by vite (define.__BUILD_VERSION__). It lives
// in the executing bundle, so it dates the CODE actually running — a stale
// service worker shows an old time (or nothing, pre-marker) even when the
// server already serves the new build.
let buildLabel = ""
try {
	const v =
		typeof __BUILD_VERSION__ !== "undefined"
			? Number(__BUILD_VERSION__)
			: Number.NaN
	buildLabel =
		Number.isFinite(v) && v > 0
			? new Date(v).toLocaleString()
			: String(__BUILD_VERSION__ ?? "")
} catch {
	buildLabel = ""
}
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
const feedDotsText = ref(String(DEFAULT_FEED_DOTS))
const tailDotsText = ref(String(DEFAULT_TAIL_DOTS))
const fontScaleText = ref("100")
const crewFontScaleText = ref("100")
const lineSpacingText = ref("100")
const sideMarginDotsText = ref("16")
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
	const fd = stored.feedDots
	feedDotsText.value =
		fd === undefined || fd === "" || fd === null
			? String(DEFAULT_FEED_DOTS)
			: String(fd)
	const td = stored.tailDots
	tailDotsText.value =
		td === undefined || td === "" || td === null
			? String(DEFAULT_TAIL_DOTS)
			: String(td)
	const fs = stored.fontScale
	fontScaleText.value =
		fs === undefined || fs === "" || fs === null ? "100" : String(fs)
	const cfs = stored.crewFontScale
	crewFontScaleText.value =
		cfs === undefined || cfs === "" || cfs === null ? "100" : String(cfs)
	const ls = stored.lineSpacing
	lineSpacingText.value =
		ls === undefined || ls === "" || ls === null ? "100" : String(ls)
	const sm = stored.sideMarginDots
	sideMarginDotsText.value =
		sm === undefined || sm === "" || sm === null ? "16" : String(sm)
}

function reloadConfig() {
	readCfgIntoForm()
	refreshEffectiveConfig()
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
		// Parse BEFORE writing anything: a non-numeric delay used to silently
		// save as 0, which disabled the tear-off pause entirely (device report:
		// "sometimes the delay happens, sometimes it doesn't").
		const copyDelayMs = parseNumericField(
			"Delay between copies",
			copyDelayText.value,
			{
				min: 0,
				max: 10000,
				dflt: 800,
			},
		)
		const feedDots = parseNumericField("Paper advance", feedDotsText.value, {
			min: 8,
			max: 500,
			dflt: DEFAULT_FEED_DOTS,
		})
		const tailDots = parseNumericField("Tail spacer", tailDotsText.value, {
			min: 0,
			max: 200,
			dflt: DEFAULT_TAIL_DOTS,
		})
		const fontScale = parseNumericField("Font scale", fontScaleText.value, {
			min: 60,
			max: 250,
			dflt: 100,
		})
		const crewFontScale = parseNumericField(
			"Crew copy font scale",
			crewFontScaleText.value,
			{
				min: 60,
				max: 250,
				dflt: 100,
			},
		)
		const lineSpacing = parseNumericField(
			"Line spacing",
			lineSpacingText.value,
			{
				min: 50,
				max: 150,
				dflt: 100,
			},
		)
		const sideMarginDots = parseNumericField(
			"Side margin",
			sideMarginDotsText.value,
			{
				min: 0,
				max: 64,
				dflt: 16,
			},
		)
		saveDeviceConfig({
			host: host || undefined,
			paper,
			customDots,
			cut: Boolean(cfg.cut),
			copies: Math.max(1, Math.min(Number(cfg.copies) || 1, 5)),
			copyDelayMs,
			feedDots,
			tailDots,
			fontScale,
			crewFontScale,
			lineSpacing,
			sideMarginDots,
		})
		// Do not call setPageFormat directly — imin_client applies it on next print.
		showSuccess(__("Device config saved. It will apply on the next print."))
		transportSnapshot()
		refreshEffectiveConfig()
	} catch (e) {
		showError(e?.message || String(e))
	} finally {
		saving.value = false
	}
}

function buildTestInvoiceData() {
	const now = new Date()
	// Real invoices carry date and time as separate fields; the crew slip's
	// timestamp row prints them as-is, so keep the test doc shaped the same
	// instead of stuffing a full ISO timestamp into posting_date.
	const iso = now.toISOString()
	return {
		name: "TEST",
		posting_date: iso.slice(0, 10),
		posting_time: iso.slice(11, 19),
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

/**
 * The sample the page prints: the last invoice of the active profile through
 * the real "POS Next Receipt" server template, or the local test receipt when
 * that is out of reach (fresh site, offline till, no profile in scope).
 *
 * The bundle is fetched at most once per page mount and shared by Test Print
 * and the preview — three server calls to look at one receipt is enough.
 */
const sampleBundle = ref(null)
// One-line note under the Test Print button: which sample was used and why.
// Null until the first fetch, so the page never claims something it has not
// checked yet.
const sampleInfo = ref(null)

const sampleNote = computed(() => {
	if (!sampleInfo.value) return ""
	return sampleInfo.value.source === "server"
		? __("Sample: last invoice {0} through the server receipt template.", [
				sampleInfo.value.name,
			])
		: __("Sample: built-in test receipt (no server invoice available).")
})

async function getSampleBundle({ refresh = false } = {}) {
	if (sampleBundle.value && !refresh) return sampleBundle.value
	const bundle = await fetchSampleReceiptBundle(
		bootstrap.getPreloadedPOSProfile()?.name || null,
		buildTestInvoiceData(),
	)
	sampleBundle.value = bundle
	sampleInfo.value = {
		source: bundle.source,
		name: bundle.invoiceDoc?.name || "",
	}
	return bundle
}

/** Receipt document for the sample: server template, else the local one. */
function sampleReceiptHTML(bundle) {
	return (
		bundle.serverHTML ||
		buildReceiptDocumentHTML(bundle.invoiceDoc, {
			includeControls: false,
			dots: effectiveReceiptDots(),
		})
	)
}

/** Crew slip for the sample — copy 2 whenever the profile prints two. */
function sampleCrewHTML(bundle) {
	return buildCrewSlipHTML(bundle.invoiceDoc, {
		dots: effectiveReceiptDots(),
	})
}

async function onTestPrint() {
	printing.value = true
	try {
		// Always re-fetch the sample: the server print format changes out
		// from under a mounted page (fixture syncs), and a cached bundle had
		// the operator test-printing a stale template while the fixes were
		// already live on the server.
		const bundle = await getSampleBundle({ refresh: true })
		await transportPrint(sampleReceiptHTML(bundle), {
			crewHTML: sampleCrewHTML(bundle),
			// Still "TEST": the log row must stay recognisable as a test print
			// and must not look like a real sale of that invoice.
			logContext: {
				reference_doctype: "Sales Invoice",
				reference_name: "TEST",
			},
		})
		showSuccess(__("Test print sent."))
		await fetchLogs()
		await pollStatus()
		refreshEffectiveConfig()
	} catch (e) {
		showError(e?.message || String(e))
		await fetchLogs()
	} finally {
		printing.value = false
	}
}

/**
 * Read the effective print config the way the driver does: device
 * localStorage on top of the transport's server config. Shared resolver, so
 * the preview cannot drift from the print.
 */

const previewing = ref(false)
const previewDots = ref(384)
const previewCopies = ref([])
const previewError = ref("")
const previewTimers = []

function serverConfigFromTransport() {
	try {
		const c = getTransport().getConfig() || {}
		return {
			paper: c.paper,
			customDots: c.custom_dots,
			cut: c.cut,
			copies: c.copies,
			copyDelayMs: c.copy_delay_ms,
			feedDots: c.feed_dots,
			tailDots: c.tail_dots,
			fontScale: c.font_scale,
			crewFontScale: c.crew_font_scale,
			lineSpacing: c.line_spacing,
			sideMarginDots: c.side_margin,
		}
	} catch {
		return {}
	}
}

/**
 * Resolve exactly what the NEXT print will use (device localStorage on top of
 * the transport's server config, same resolver the driver calls). Exposed on
 * the page so "sometimes the delay applies, sometimes it doesn't" is visible
 * as a concrete value instead of a guess.
 */
function effectivePrintConfig(copiesOverride) {
	const device = { ...(loadDeviceConfig() || {}) }
	if (copiesOverride != null) device.copies = copiesOverride
	return resolvePrintConfig(device, serverConfigFromTransport())
}

const effectiveCfg = ref(null)

function refreshEffectiveConfig() {
	try {
		effectiveCfg.value = effectivePrintConfig()
	} catch {
		effectiveCfg.value = null
	}
}

function clearPreview() {
	for (const t of previewTimers) window.clearTimeout(t)
	previewTimers.length = 0
	previewCopies.value = []
	previewError.value = ""
}

/**
 * Render the preview through the same path that actually prints
 * (resolvePrintConfig -> renderHTMLToBitmap, crew slip at its own font scale),
 * then reveal later copies only after the configured delay. The second sheet
 * therefore appears exactly when it would leave the printer. Same invariant
 * the driver itself relies on — the template only iterates the set it is given.
 */
async function runPreview(copiesOverride) {
	clearPreview()
	previewing.value = true
	try {
		const device = { ...(loadDeviceConfig() || {}) }
		if (copiesOverride != null) device.copies = copiesOverride
		const server = serverConfigFromTransport()
		const bundle = await getSampleBundle()
		const set = await buildReceiptPreviewSet(sampleReceiptHTML(bundle), {
			device,
			server,
			// Copy 2 is the compact crew slip, exactly as the driver prints it.
			crewHTML: sampleCrewHTML(bundle),
		})
		previewDots.value = set.dots
		previewCopies.value = set.copies
		// Later copies are hidden until their delay elapses, so the tear-off
		// pause between physical sheets is visible even without a printer.
		for (const row of set.copies) {
			if (!row.visible)
				previewTimers.push(
					window.setTimeout(() => {
						const n = previewCopies.value
						const t = n.find((x) => x.index === row.index)
						if (t) t.visible = true
					}, row.delayMs),
				)
		}
	} catch (e) {
		previewError.value = e?.message || String(e)
	} finally {
		previewing.value = false
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
	refreshEffectiveConfig()
	await pollStatus()
	timer = window.setInterval(pollStatus, 3000)
	await fetchLogs()
	// Fetch the sample bundle in the background so the note under Test Print is
	// already truthful before the first click. Never awaited: a slow print-format
	// fetch must not delay the status poll or the log table. fetchSampleReceiptBundle
	// resolves to a fallback bundle instead of rejecting, so there is no error path.
	getSampleBundle()
})

onUnmounted(() => {
	if (timer) window.clearInterval(timer)
	timer = null
})
</script>
