<template>
	<Dialog v-model="open" :options="{ title: __('Open POS Shift'), size: step === 2 && hasProfileSlots ? '3xl' : 'xl' }">
		<template #body-content>
			<div class="pos-dialog-bound flex flex-col gap-6 pe-1">
				<!-- Step 1: Select POS Profile -->
				<div v-if="step === 1" class="flex flex-col gap-4">
					<div>
						<label class="block text-sm font-medium text-gray-700 mb-2 text-start">
							{{ __("Select POS Profile") }}
						</label>
						<div v-if="profilesResource.loading" class="text-center py-4">
							<div
								class="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"
							></div>
						</div>
						<div
							v-else-if="profilesResource.data && profilesResource.data.length > 0"
							class="grid grid-cols-1 gap-3"
						>
							<div
								v-for="profile in profilesResource.data"
								:key="profile.name"
								@click="selectPosProfile(profile)"
								:class="[
									'p-4 border rounded-lg cursor-pointer transition-all min-h-[44px] sm:min-h-[40px] touch-manipulation',
									selectedProfile?.name === profile.name
										? 'border-blue-500 bg-blue-50 ring-2 ring-blue-500'
										: 'border-gray-200 hover:border-blue-300 hover:bg-gray-50',
								]"
							>
								<div class="flex justify-between items-start">
									<div class="text-start">
										<h3 class="font-medium text-gray-900">
											{{ profile.name }}
										</h3>
										<p class="text-sm text-gray-500 mt-1">
											{{ profile.company }}
										</p>
									</div>
									<div class="flex flex-col items-end gap-1">
										<span
											class="text-xs text-gray-500 bg-gray-100 px-2 py-1 rounded"
										>
											{{ profile.currency }}
										</span>
										<span
											v-if="profile.active_shift"
											class="text-xs font-medium text-green-700 bg-green-100 px-2 py-1 rounded text-end"
										>
											{{ __("Active") }} · {{ profile.active_shift.user_name }}
										</span>
									</div>
								</div>
							</div>
						</div>
						<div v-else class="text-center py-8 text-gray-500">
							<p>
								{{
									__(
										"No POS Profiles available. Please contact your administrator."
									)
								}}
							</p>
						</div>
					</div>

					<div v-if="profilesResource.error" class="rounded-md bg-red-50 p-4">
						<p class="text-sm text-red-800">{{ serverErrorMessage(profilesResource.error) }}</p>
					</div>
				</div>

				<!-- Step 2: Enter Opening Balances -->
				<div v-if="step === 2" class="flex flex-col gap-5 text-start">
					<!-- Profile row -->
					<div class="flex items-center gap-3 border-b border-gray-200 pb-4">
						<div class="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-blue-50 text-blue-600">
							<svg class="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
								<path d="M3 9l1.5-5h15L21 9" />
								<path d="M3 9h18v1.5a3 3 0 0 1-6 0 3 3 0 0 1-6 0 3 3 0 0 1-6 0z" />
								<path d="M5 13v7h14v-7" />
								<path d="M10 20v-4h4v4" />
							</svg>
						</div>
						<div class="min-w-0 flex-1">
							<h3 class="truncate text-base font-semibold text-gray-900">{{ selectedProfile?.name }}</h3>
							<p class="truncate text-sm text-gray-500">{{ selectedProfile?.company }}</p>
						</div>
						<Button variant="subtle" class="min-h-11 shrink-0 px-4 touch-manipulation" @click="step = 1">{{
							__("Change Profile")
						}}</Button>
					</div>

					<div
						:class="
							hasProfileSlots
								? 'grid grid-cols-1 items-start gap-4 md:grid-cols-[1fr_1.12fr]'
								: ''
						"
					>
						<!-- Shift slot picker -->
						<section v-if="hasProfileSlots" class="rounded-2xl border border-gray-200 bg-gray-50/70 p-4 sm:p-5">
							<h3 class="flex items-center gap-2 text-[15px] font-semibold text-gray-900">
								<svg class="h-[18px] w-[18px] text-blue-600" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
									<circle cx="12" cy="12" r="9" />
									<path d="M12 7v5l3 2" />
								</svg>
								{{ __("Shift") }}
							</h3>
							<p class="mt-1 text-sm text-gray-500">{{
								openSlots.length > 1 ? __("Choose the shift to open.") : __("The shift that will be opened.")
							}}</p>

							<div
								v-if="openSlots.length > 0"
								class="mt-4 flex flex-col gap-2.5"
								:role="openSlots.length > 1 ? 'radiogroup' : undefined"
							>
								<label
									v-for="slot in openSlots"
									:key="slot.shift_name"
									:class="[
										'flex min-h-[72px] items-center gap-3 rounded-xl border bg-white px-4 py-3 touch-manipulation transition-colors duration-150',
										openSlots.length > 1 ? 'cursor-pointer' : '',
										selectedSlot === slot.shift_name
											? 'border-blue-500 bg-blue-50/60 ring-1 ring-inset ring-blue-500'
											: 'border-gray-200 hover:border-blue-300',
									]"
								>
									<!-- A single open slot needs no choice: show it as a plain card -->
									<input
										v-if="openSlots.length > 1"
										v-model="selectedSlot"
										type="radio"
										name="shift-slot"
										:value="slot.shift_name"
										class="h-5 w-5 shrink-0 border-gray-300 text-blue-600 focus:ring-blue-500"
									/>
									<span class="flex min-w-0 flex-col">
										<span class="truncate text-sm font-semibold text-gray-900">{{ slot.shift_name }}</span>
										<span class="text-sm text-gray-500 tabular-nums">
											{{ hhmm(slot.start_time) }} – {{ hhmm(slot.end_time) }}
										</span>
									</span>
								</label>
							</div>

							<!-- Zero open slots: outside shift hours message -->
							<div v-else class="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-4">
								<p class="text-sm font-medium text-amber-800">{{ __("Outside shift hours") }}</p>
								<p class="mt-1 text-sm text-amber-700">
									{{ __("A shift cannot be opened outside shift hours.") }}
								</p>
							</div>
						</section>

						<!-- Cash -->
						<section :class="hasProfileSlots ? 'rounded-2xl border border-gray-200 p-4 sm:p-5' : ''">
							<h3 class="flex items-center gap-2 text-[15px] font-semibold text-gray-900">
								<svg class="h-[18px] w-[18px] text-blue-600" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
									<rect x="2" y="6" width="20" height="12" rx="2" />
									<circle cx="12" cy="12" r="2.5" />
									<path d="M6 12h.01M18 12h.01" />
								</svg>
								{{ handover ? __("Cash Received from Previous Shift") : __("Modal Awal") }}
							</h3>
							<p v-if="handover" class="mt-1 text-sm text-gray-500">
								{{ __("Cash handed over from the previous shift, according to the system.") }}
							</p>

							<div v-if="dialogDataResource.loading" class="mt-4 flex flex-col gap-3" aria-busy="true">
								<div class="h-24 animate-pulse rounded-xl bg-gray-100"></div>
								<div class="h-14 animate-pulse rounded-xl bg-gray-100"></div>
							</div>

							<div v-else-if="cashPaymentMethods.length > 0" class="mt-4 flex flex-col gap-5">
								<div v-for="method in cashPaymentMethods" :key="method.name" class="flex flex-col gap-4">
									<!-- System amount handed over by the previous shift -->
									<div
										v-if="handover && handover.amounts?.[method.mode_of_payment] != null"
										class="rounded-xl bg-blue-50/70 p-4"
									>
										<p class="text-sm text-gray-600">
											{{ __("{0} (System)", [method.mode_of_payment]) }}
										</p>
										<p class="mt-0.5 text-[28px] font-bold leading-tight text-gray-900 tabular-nums">
											{{ currencySymbol }} {{ formatCurrency(handover.amounts[method.mode_of_payment]) }}
										</p>
										<p class="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-blue-100 pt-3 text-sm text-gray-600">
											<span>{{ __("Shift {0}", [handover.user_full_name || handover.user || ""]) }}</span>
											<span aria-hidden="true">·</span>
											<span class="tabular-nums">{{
												__("Closed at {0}", [
													formatTime(handover.period_end_date) ||
														formatDateTime(handover.period_end_date) ||
														"",
												])
											}}</span>
											<span
												v-if="handover.auto_closed"
												class="inline-flex items-center rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800"
											>
												{{ __("closed automatically") }}
											</span>
										</p>
									</div>

									<div>
										<label
											:for="`opening-${method.name}`"
											class="block text-sm font-semibold text-gray-900"
										>
											{{ handover ? __("Amount Received") : method.mode_of_payment }}
										</label>
										<p v-if="handover" class="text-sm text-gray-500">
											{{ __("Count the cash and enter the amount received") }}
										</p>
										<div
											class="mt-2 flex h-14 overflow-hidden rounded-xl border border-gray-300 bg-white transition-shadow focus-within:border-blue-500 focus-within:ring-4 focus-within:ring-blue-500/15"
										>
											<span class="flex w-16 shrink-0 items-center justify-center border-e border-gray-200 bg-gray-50 text-base font-medium text-gray-500">
												{{ currencySymbol }}
											</span>
											<input
												:id="`opening-${method.name}`"
												class="min-w-0 flex-1 border-0 bg-transparent px-4 text-2xl font-semibold text-gray-900 tabular-nums text-end placeholder-gray-300 caret-blue-600 focus:outline-none focus:ring-0"
												:value="openingBalances[method.mode_of_payment]"
												inputmode="decimal"
												autocomplete="off"
												placeholder="0"
												@input="
													openingBalances[method.mode_of_payment] =
														formatAmountInput($event.target.value)
												"
											/>
										</div>
										<p
											v-if="hasHandoverDifference(method.mode_of_payment)"
											class="mt-2 flex items-center gap-1.5 text-sm font-medium text-red-700 tabular-nums"
											role="status"
										>
											<svg class="h-4 w-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
												<circle cx="12" cy="12" r="9" />
												<path d="M12 8v4M12 16h.01" />
											</svg>
											{{
												__("Difference: {0}", [
													handoverDifferenceFormatted(method.mode_of_payment),
												])
											}}
										</p>
										<p
											v-else-if="getHandoverDifference(method.mode_of_payment) === 0"
											class="mt-2 flex items-center gap-1.5 text-sm font-medium text-green-700"
											role="status"
										>
											<svg class="h-4 w-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
												<circle cx="12" cy="12" r="9" />
												<path d="m8 12 3 3 5-6" />
											</svg>
											{{ __("Matches system") }}
										</p>
									</div>
								</div>
							</div>

							<div v-else class="mt-4 rounded-xl bg-gray-50 py-6 text-center text-sm text-gray-500">
								{{ __("No payment methods configured for this POS Profile") }}
							</div>
						</section>
					</div>

					<div v-if="dialogDataResource.error" class="rounded-md bg-red-50 p-4">
						<p class="text-sm text-red-800">{{ serverErrorMessage(dialogDataResource.error) }}</p>
					</div>

				</div>

				<!-- Step 3: Resume or Open New -->
				<div v-if="step === 3" class="flex flex-col gap-4">
					<div class="text-center">
						<div
							class="mx-auto flex items-center justify-center h-12 w-12 rounded-full bg-blue-100 mb-4"
						>
							<svg
								class="h-6 w-6 text-blue-600"
								fill="none"
								stroke="currentColor"
								viewBox="0 0 24 24"
							>
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									stroke-width="2"
									d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
								/>
							</svg>
						</div>
						<h3 class="text-lg font-medium text-gray-900 mb-2">
							{{ __("Existing Shift Found") }}
						</h3>
						<p class="text-sm text-gray-500 mb-6">
							{{
								__(
									"You have an open shift. Would you like to resume it or close it and open a new one?"
								)
							}}
						</p>

						<div v-if="existingShift" class="bg-gray-50 rounded-lg p-4 mb-6">
							<div class="text-sm text-gray-600">
								<TranslatedHTML
									:tag="'p'"
									:inner="
										__('&lt;strong&gt;POS Profile:&lt;/strong&gt; {0}', [
											existingShift.pos_profile?.name,
										])
									"
								/>
								<div class="h-2"></div>
								<TranslatedHTML
									:tag="'p'"
									:inner="
										__('&lt;strong&gt;Opened:&lt;/strong&gt; {0}', [
											formatDateTime(
												existingShift.pos_opening_shift?.period_start_date
											),
										])
									"
								/>
							</div>
						</div>

						<div class="flex gap-3 justify-center">
							<Button variant="solid" theme="blue" class="min-h-10 max-sm:min-h-11 px-4 touch-manipulation" @click="resumeShift">
								{{ __("Resume Shift") }}
							</Button>
							<Button variant="subtle" theme="gray" class="min-h-10 max-sm:min-h-11 px-4 touch-manipulation" @click="closeAndOpenNew">
								{{ __("Close & Open New") }}
							</Button>
						</div>
					</div>
				</div>
			</div>
		</template>

		<template #actions>
			<div class="flex justify-between w-full">
				<Button v-if="step > 1 && step !== 3" variant="subtle" class="min-h-10 max-sm:min-h-11 px-4 touch-manipulation" @click="step--">
					{{ __("Back") }}
				</Button>
				<div v-else></div>

				<div class="flex gap-2">
					<Button
						variant="subtle"
						class="min-h-10 max-sm:min-h-11 px-4 touch-manipulation"
						@click="closeDialog('cancelled')"
						:disabled="createShiftResource.loading"
					>
						{{ __("Cancel") }}
					</Button>
					<Button
						v-if="step === 1"
						variant="solid"
						theme="blue"
						class="min-h-10 max-sm:min-h-11 px-4 touch-manipulation"
						@click="nextStep"
						:disabled="!selectedProfile"
					>
						{{ __("Next") }}
					</Button>
					<Button
						v-if="step === 2"
						variant="solid"
						theme="blue"
						class="min-h-10 max-sm:min-h-11 px-4 touch-manipulation"
						@click="openShift"
						:loading="createShiftResource.loading"
						:disabled="isOutsideShiftHours || handoverIncomplete"
					>
						{{ __("Open Shift") }}
					</Button>
				</div>
			</div>
		</template>
	</Dialog>

	<!-- Refusal popup: profile held by another cashier, or open-shift error -->
	<Dialog
		v-model="showBlocked"
		:options="{ title: blockedTitle, size: 'md' }"
	>
		<template #body-content>
			<TranslatedHTML
				:key="blockedMessage"
				tag="p"
				class="text-sm text-gray-700 text-start"
				:inner="blockedMessage"
			/>
		</template>
		<template #actions>
			<div class="flex justify-end w-full">
				<Button variant="solid" theme="blue" class="min-h-10 max-sm:min-h-11 px-4 touch-manipulation" @click="showBlocked = false">
					{{ __("OK") }}
				</Button>
			</div>
		</template>
	</Dialog>
</template>

<script setup>
import { Button, Dialog, createResource } from "frappe-ui"
import { computed, ref, watch } from "vue"
import { useShift } from "../composables/useShift"
import { useFormatters } from "../composables/useFormatters"
import { useToast } from "../composables/useToast"
import { formatAmountInput, parseAmountInput } from "../utils/amountInput"
import { serverErrorMessage } from "../utils/apiWrapper"
import { getCurrencySymbol } from "../utils/currency"
import TranslatedHTML from "./common/TranslatedHTML.vue"

const props = defineProps({
	modelValue: Boolean,
})

const emit = defineEmits([
	"update:modelValue",
	"shift-opened",
	"dialog-closed",
	"close-existing-shift",
])

const open = computed({
	get: () => props.modelValue,
	set: (value) => emit("update:modelValue", value),
})

const { createOpeningShift, getOpeningDialogData, checkOpeningShift } =
	useShift()
const { formatCurrency, formatDateTime, formatTime } = useFormatters()
const { showInfo } = useToast()

const step = ref(1)
const selectedProfile = ref(null)
const openingBalances = ref({})
const existingShift = ref(null)

// Get POS Profiles
const profilesResource = createResource({
	url: "pos_next.api.pos_profile.get_pos_profiles",
	auto: false,
})

// Get dialog data (payment methods)
const dialogDataResource = createResource({
	url: "pos_next.api.shifts.get_opening_dialog_data",
	auto: false,
})

// Create shift resource
const createShiftResource = createOpeningShift

// Computed payment methods for selected profile
const paymentMethods = computed(() => {
	if (!dialogDataResource.data || !selectedProfile.value) return []

	return (dialogDataResource.data.payments_method || []).filter(
		(method) => method.parent === selectedProfile.value.name,
	)
})

// Cash-only payment methods shown in Modal Awal
const cashPaymentMethods = computed(() => {
	return paymentMethods.value.filter(isCashMethod)
})

// Only the profile's drawer cash mode carries a Modal Awal; every other mode
// (QRIS, debit, e-wallet, ...) stays at 0 so it can't double-count a shift.
function isCashMethod(method) {
	return Boolean(method.is_drawer_cash)
}

const selectedSlot = ref(null)

// Shift slots for selected profile
const profileSlots = computed(() => {
	if (!dialogDataResource.data?.slots || !selectedProfile.value) return []
	return dialogDataResource.data.slots[selectedProfile.value.name] || []
})

const hasProfileSlots = computed(() => {
	return profileSlots.value.length > 0
})

const openSlots = computed(() => {
	return profileSlots.value.filter((s) => s.open_now)
})

const isOutsideShiftHours = computed(() => {
	return hasProfileSlots.value && openSlots.value.length === 0
})

const handover = computed(() => {
	if (!dialogDataResource.data?.handover || !selectedProfile.value) return null
	return dialogDataResource.data.handover[selectedProfile.value.name] ?? null
})

const currencySymbol = computed(() => getCurrencySymbol(selectedProfile.value?.currency))

const handoverIncomplete = computed(() => {
	if (!handover.value) return false
	return cashPaymentMethods.value.some((method) => {
		const val = openingBalances.value[method.mode_of_payment]
		return val === undefined || val === null || String(val).trim() === ""
	})
})

function getHandoverDifference(mode) {
	if (!handover.value || handover.value.amounts?.[mode] == null) return null
	const raw = openingBalances.value[mode]
	if (raw === undefined || raw === null || String(raw).trim() === "") {
		return null
	}
	const proposed = Number(handover.value.amounts[mode]) || 0
	const entered = parseAmountInput(raw)
	return entered - proposed
}

function hasHandoverDifference(mode) {
	const diff = getHandoverDifference(mode)
	return diff !== null && diff !== 0
}

function handoverDifferenceFormatted(mode) {
	const diff = getHandoverDifference(mode)
	if (diff == null) return ""
	return diff > 0 ? `+${formatCurrency(diff)}` : formatCurrency(diff)
}

// Overlapping open slots: default to the one that started most recently
// (13:30 with 05-14 and 13-18 open picks 13-18); wraps past midnight.
function latestStartedSlot(slots) {
	const now = new Date()
	const nowMin = now.getHours() * 60 + now.getMinutes()
	const sinceStart = (slot) => {
		const [h, m] = String(slot.start_time).split(":").map(Number)
		return (nowMin - (h * 60 + (m || 0)) + 1440) % 1440
	}
	return slots.reduce((best, s) => (sinceStart(s) < sinceStart(best) ? s : best))
}

watch(
	openSlots,
	(slots) => {
		if (slots && slots.length > 0) {
			if (
				!selectedSlot.value ||
				!slots.some((s) => s.shift_name === selectedSlot.value)
			) {
				selectedSlot.value = latestStartedSlot(slots).shift_name
			}
		} else {
			selectedSlot.value = null
		}
	},
	{ immediate: true },
)

// Watch dialog open state.
// Reset happens when the dialog OPENS (inside initDialog), never on close:
// tearing the DOM down (step=1, profiles=null) while the reka-ui unmount
// transition is running leaves the dialog stuck on screen with no way to
// close it. Data surviving through close is invisible — the next open
// re-initializes from scratch.
// Use { immediate: true } to ensure initDialog runs even when
// the component mounts with open already true (e.g., after logout with dialog open)
watch(
	open,
	(isOpen) => {
		if (isOpen) {
			initDialog()
		}
	},
	{ immediate: true },
)

async function initDialog() {
	// Reset first, then fetch — a reopen must not inherit the previous
	// session's step/profile/errors, and the fetch must repopulate a
	// clean resource.
	resetDialog()

	try {
		// Await profile fetch to ensure data is loaded before proceeding
		await profilesResource.fetch()

		// Check if user already has an open shift
		const checkResult = await checkOpeningShift.fetch()
		if (checkResult) {
			existingShift.value = checkResult
			step.value = 3
		}
	} catch (error) {
		console.error("Error initializing shift dialog:", error)
		// Error will be displayed via profilesResource.error in the UI
	}
}

function resetDialog() {
	step.value = 1
	selectedProfile.value = null
	openingBalances.value = {}
	existingShift.value = null
	selectedSlot.value = null
	profilesResource.reset()
	dialogDataResource.reset()
	createShiftResource.reset()
}

// Time fields arrive as "8:00:00" (serialized timedelta) → "08:00"
function hhmm(time) {
	const [h, m] = String(time).split(":")
	return `${h.padStart(2, "0")}:${m}`
}

function selectPosProfile(profile) {
	if (selectedProfile.value?.name !== profile?.name) {
		openingBalances.value = {}
	}
	selectedProfile.value = profile
	selectedSlot.value = null
}

const showBlocked = ref(false)
const blockedTitle = ref("")
const blockedMessage = ref("")

function showBlockedPopup(title, message) {
	blockedTitle.value = title
	blockedMessage.value = message
	showBlocked.value = true
}

async function nextStep() {
	if (step.value === 1 && selectedProfile.value) {
		// Refuse up front instead of letting the cashier fill balances first;
		// the server still re-checks under lock in create_opening_shift.
		const profile = selectedProfile.value
		if (profile.locked) {
			const s = profile.active_shift
			const bold = (v) => `<strong>${v}</strong>`
			showBlockedPopup(
				__("POS Profile Is Active"),
				__(
					"POS Profile {0} is still active on shift {1}, opened by {2} since {3}. Only one shift can be open per POS Profile. Ask {2} or a POS Manager to close that shift first.",
					[bold(profile.name), bold(s.name), bold(s.user_name), s.since],
				),
			)
			return
		}
		await dialogDataResource.fetch()
		step.value = 2
	}
}

async function openShift() {
	if (
		!selectedProfile.value ||
		isOutsideShiftHours.value ||
		handoverIncomplete.value
	)
		return

	// Prepare balance details: all payment methods are sent so closing can reconcile them,
	// but only Cash may carry an opening amount; non-cash is strictly 0.
	const balance_details = paymentMethods.value.map((method) => ({
		mode_of_payment: method.mode_of_payment,
		opening_amount: isCashMethod(method)
			? parseAmountInput(openingBalances.value[method.mode_of_payment])
			: 0,
	}))

	try {
		await createShiftResource.submit({
			pos_profile: selectedProfile.value.name,
			company: selectedProfile.value.company,
			balance_details,
			...(selectedSlot.value ? { shift_slot: selectedSlot.value } : {}),
		})

		// manager on a profile that is already active: the server joined the
		// running shift instead of opening a second drawer
		if (createShiftResource.data?.resumed) {
			showInfo(
				__("POS Profile {0} is already active. Resumed shift {1}.", [
					selectedProfile.value.name,
					createShiftResource.data.pos_opening_shift?.name,
				]),
			)
		}
		emit("shift-opened")
		closeDialog("shift-opened")
	} catch (error) {
		console.error("Error opening shift:", error)
		// race: someone took the profile after the list loaded
		showBlockedPopup(
			__("Open Shift"),
			serverErrorMessage(createShiftResource.error || error),
		)
	}
}

function resumeShift() {
	emit("shift-opened")
	closeDialog("resumed")
}

function closeAndOpenNew() {
	if (!existingShift.value?.pos_opening_shift?.name) {
		return
	}

	// The page-level ShiftClosingDialog (POSSale.vue) owns closing the stale
	// shift — the shared checkOpeningShift already mirrored it into
	// shiftStore.currentShift, so the page dialog has the right opening shift.
	// handleShiftClosed there reopens this dialog once the close settles.
	emit("close-existing-shift", existingShift.value.pos_opening_shift.name)
	closeDialog("close-and-open-new")
}

function closeDialog(reason) {
	open.value = false
	emit("dialog-closed", { reason })
}
</script>
