import { shiftState } from "./useShift";
import { computeScheduleStatus, isScheduleBlocking } from "@/utils/shiftSchedule";

/**
 * Point-in-time guards for checkout / payment / refund submits.
 * Components use these instead of a reactive computed because the guards only
 * matter at the moment of submission (the reactive tick lives in the
 * posShift store and drives the warning / forced-close UI in POSSale).
 */
function statusNow() {
	const state = shiftState.value;
	if (!state?.isOpen || !state.pos_opening_shift) return null;
	return computeScheduleStatus(state.pos_opening_shift, {
		serverNowMs: state._serverNowMs,
		receivedAtMs: state._receivedAt,
		localNowMs: Date.now(),
	});
}

/** True when sales / payments / refunds must be refused right now. */
export function scheduleBlockingNow() {
	return isScheduleBlocking(statusNow());
}

/**
 * True while the open shift carries a mandatory closing window (deadline
 * known). Used to disable offline checkout proactively: an invoice queued
 * offline could otherwise be rejected forever when it syncs past the
 * deadline (fail-closed — the server cannot trust client timestamps).
 */
export function scheduleEnforcedNow() {
	const status = statusNow();
	return !!(status && status.enforce && status.deadlineMs != null);
}
