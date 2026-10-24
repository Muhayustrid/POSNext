<template>
	<Dialog v-model="open" :options="{ title: __('Open POS Shift'), size: 'xl' }">
		<template #body-content>
			<div class="flex flex-col gap-6">
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
									'p-4 border rounded-lg cursor-pointer transition-all',
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
				<div v-if="step === 2" class="flex flex-col gap-4">
					<div class="mb-4">
						<div class="flex items-center justify-between">
							<div class="text-start">
								<h3 class="font-medium text-gray-900">
									{{ selectedProfile?.name }}
								</h3>
								<p class="text-sm text-gray-500">{{ selectedProfile?.company }}</p>
							</div>
							<Button variant="subtle" @click="step = 1">{{
								__("Change Profile")
							}}</Button>
						</div>
					</div>

					<!-- Shift slot selector / display -->
					<div v-if="hasProfileSlots" class="mb-2">
						<!-- Two or more open slots: show picker, default is first -->
						<div v-if="openSlots.length > 1" class="flex flex-col gap-1 text-start">
							<label for="shift-slot-select" class="block text-sm font-medium text-gray-700 mb-1">
								{{ __("Shift") }}
							</label>
							<select
								id="shift-slot-select"
								v-model="selectedSlot"
								class="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-700 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/30 cursor-pointer"
							>
								<option
									v-for="slot in openSlots"
									:key="slot.shift_name"
									:value="slot.shift_name"
								>
									{{ slot.shift_name }} ({{ hhmm(slot.start_time) }} – {{ hhmm(slot.end_time) }})
								</option>
							</select>
						</div>

						<!-- Exactly one open slot: show name without a picker -->
						<div
							v-else-if="openSlots.length === 1"
							class="text-start p-3 bg-gray-50 rounded-lg border border-gray-200"
						>
							<div class="flex items-center justify-between">
								<span class="text-xs font-medium text-gray-500 uppercase tracking-wider">
									{{ __("Shift") }}
								</span>
								<span class="text-xs text-blue-700 font-medium">
									🕒 {{ hhmm(openSlots[0].start_time) }} – {{ hhmm(openSlots[0].end_time) }}
								</span>
							</div>
							<p class="text-sm font-semibold text-gray-900 mt-1">
								{{ openSlots[0].shift_name }}
							</p>
						</div>

						<!-- Zero open slots: outside shift hours message -->
						<div
							v-else
							class="rounded-lg bg-amber-50 border border-amber-200 p-4 text-start"
						>
							<div class="flex items-center gap-2">
								<span class="text-amber-800 font-medium text-sm">
									⚠️ {{ __("Outside shift hours") }}
								</span>
							</div>
							<p class="text-xs text-amber-700 mt-1">
								{{ __("A shift cannot be opened outside shift hours.") }}
							</p>
						</div>
					</div>

					<div>
						<label class="block text-sm font-medium text-gray-700 mb-3 text-start">
							{{ __("Modal Awal") }}
						</label>

						<div v-if="dialogDataResource.loading" class="text-center py-4">
							<div
								class="inline-block animate-spin rounded-full h-6 w-6 border-b-2 border-blue-600"
							></div>
						</div>

						<div v-else-if="cashPaymentMethods.length > 0" class="flex flex-col gap-3">
							<div
								v-for="method in cashPaymentMethods"
								:key="method.name"
								class="flex items-center gap-3 p-3 border rounded-lg"
							>
								<div class="flex-1 text-start">
									<label class="text-sm font-medium text-gray-700">
										{{ method.mode_of_payment }}
									</label>
								</div>
								<div class="w-32">
									<input
										class="w-full rounded border border-gray-300 bg-transparent px-2 py-1.5 text-base text-ink-gray-9 placeholder-ink-gray-4 focus:border-gray-500 focus:outline-none focus:ring-0"
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
							</div>
						</div>

						<div v-else class="text-center py-4 text-gray-500">
							<p class="text-sm">
								{{ __("No payment methods configured for this POS Profile") }}
							</p>
						</div>
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
							<Button variant="solid" theme="blue" @click="resumeShift">
								{{ __("Resume Shift") }}
							</Button>
							<Button variant="subtle" theme="gray" @click="closeAndOpenNew">
								{{ __("Close & Open New") }}
							</Button>
						</div>
					</div>
				</div>
			</div>
		</template>

		<template #actions>
			<div class="flex justify-between w-full">
				<Button v-if="step > 1 && step !== 3" variant="subtle" @click="step--">
					{{ __("Back") }}
				</Button>
				<div v-else></div>

				<div class="flex gap-2">
					<Button
						variant="subtle"
						@click="closeDialog('cancelled')"
						:disabled="createShiftResource.loading"
					>
						{{ __("Cancel") }}
					</Button>
					<Button
						v-if="step === 1"
						variant="solid"
						theme="blue"
						@click="nextStep"
						:disabled="!selectedProfile"
					>
						{{ __("Next") }}
					</Button>
					<Button
						v-if="step === 2"
						variant="solid"
						theme="blue"
						@click="openShift"
						:loading="createShiftResource.loading"
						:disabled="isOutsideShiftHours"
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
				<Button variant="solid" theme="blue" @click="showBlocked = false">
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
const { formatDateTime } = useFormatters()
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

watch(
	openSlots,
	(slots) => {
		if (slots && slots.length > 0) {
			if (
				!selectedSlot.value ||
				!slots.some((s) => s.shift_name === selectedSlot.value)
			) {
				selectedSlot.value = slots[0].shift_name
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
	if (!selectedProfile.value || isOutsideShiftHours.value) return

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
