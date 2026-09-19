import { useShift, shiftState } from "@/composables/useShift";
import { DEFAULT_CURRENCY, DEFAULT_LOCALE } from "@/utils/currency";
import { computeScheduleStatus } from "@/utils/shiftSchedule";
import { formatShiftDuration } from "@/utils/shiftDuration";
import { defineStore } from "pinia";
import { computed, ref } from "vue";

export const usePOSShiftStore = defineStore("posShift", () => {
	// Use the existing shift composable
	const { currentProfile, currentShift, hasOpenShift, checkOpeningShift } = useShift();

	// Additional shift state
	const currentTime = ref("");
	const shiftDuration = ref("");
	const shiftTimerPaused = ref(false);
	const scheduleStatus = ref(null);

	// Computed
	const profileName = computed(() => currentProfile.value?.name);
	const profileCurrency = computed(() => currentProfile.value?.currency || DEFAULT_CURRENCY);
	const profileWarehouse = computed(() => currentProfile.value?.warehouse);
	const profileCompany = computed(() => currentProfile.value?.company);
	const profileCustomer = computed(() => currentProfile.value?.customer);
	const writeOffAccount = computed(() => currentProfile.value?.write_off_account);
	const writeOffCostCenter = computed(() => currentProfile.value?.write_off_cost_center);
	const writeOffLimit = computed(() => currentProfile.value?.write_off_limit || 0);

	// Actions
	function updateShiftDuration() {
		if (!hasOpenShift.value || !currentShift.value?.period_start_date) {
			shiftDuration.value = "";
			return;
		}

		// Freeze the counter when closing dialog is open
		if (shiftTimerPaused.value) return;

		// Elapsed = initial elapsed (server_now - shift_start, computed at fetch time)
		//         + time since we received the data (local clock only)
		// This avoids timezone mismatch between server and browser.
		const { _initialElapsedMs, _receivedAt } = shiftState.value;
		const diff = _initialElapsedMs + (Date.now() - (_receivedAt || Date.now()));
		shiftDuration.value = formatShiftDuration(diff);
	}

	/**
	 * Re-evaluate the shift schedule (runs on the 1s timer). Drives the
	 * warning toast and forced-closing dialog in POSSale.
	 */
	function updateScheduleStatus() {
		const state = shiftState.value;
		scheduleStatus.value =
			state.isOpen && state.pos_opening_shift
				? computeScheduleStatus(state.pos_opening_shift, {
						serverNowMs: state._serverNowMs,
						receivedAtMs: state._receivedAt,
						localNowMs: Date.now(),
					})
				: null;
	}

	function updateCurrentTime() {
		const now = new Date();
		currentTime.value = now.toLocaleTimeString(DEFAULT_LOCALE, { hour12: false });
	}

	function startTimers() {
		// Update both immediately
		updateCurrentTime();
		updateShiftDuration();
		updateScheduleStatus();

		// Then update every second
		const intervalId = setInterval(() => {
			updateCurrentTime();
			updateShiftDuration();
			updateScheduleStatus();
		}, 1000);

		return intervalId;
	}

	async function checkShift() {
		await checkOpeningShift.fetch();
		updateScheduleStatus();
		return hasOpenShift.value;
	}

	return {
		// State
		currentProfile,
		currentShift,
		hasOpenShift,
		currentTime,
		shiftDuration,
		shiftTimerPaused,
		scheduleStatus,

		// Computed
		profileName,
		profileCurrency,
		profileWarehouse,
		profileCompany,
		profileCustomer,
		writeOffAccount,
		writeOffCostCenter,
		writeOffLimit,

		// Actions
		updateShiftDuration,
		updateCurrentTime,
		startTimers,
		checkShift,
		checkOpeningShift,
	};
});
