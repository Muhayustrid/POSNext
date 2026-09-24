/**
 * Socket room binding for the active POS Profile.
 *
 * PERF-06/SEC-NEW-04: transaction events are published to `pos_profile:<name>`
 * rooms instead of site-wide. The server only admits sockets whose user is a
 * POS Profile User of that profile (pos_next/realtime/handlers.js), so the
 * client must emit pos_profile_subscribe for the room it wants. Importing
 * shiftState directly (not useShift()) avoids re-creating its resources; on a
 * profile switch the old room is left before joining the new one.
 */

import { computed, watch } from "vue";
import { shiftState } from "@/composables/useShift";
import { logger } from "@/utils/logger";

const log = logger.create("RealtimeRoom");

const activeProfile = computed(() => {
	const profile = shiftState.value.pos_profile;
	return profile?.name || profile || null;
});

/**
 * Creates a start/stop binding that keeps the socket in the active profile's room.
 * @returns {{ start: Function, stop: Function }}
 */
export function createProfileRoomBinding() {
	let joinedRoom = null;
	let stopWatch = null;

	function join(profile) {
		const realtime = window.frappe?.realtime;
		if (!realtime?.socket || !profile || profile === joinedRoom) {
			return;
		}
		if (joinedRoom) {
			realtime.emit("pos_profile_unsubscribe", joinedRoom);
		}
		realtime.emit("pos_profile_subscribe", profile);
		log.debug("Joined profile room", { profile });
		joinedRoom = profile;
	}

	// socket.io drops server-side rooms on every (re)connect, so re-join the
	// current room even though the tracker already holds it
	function onConnect() {
		const realtime = window.frappe?.realtime;
		if (realtime?.socket && joinedRoom) {
			realtime.emit("pos_profile_subscribe", joinedRoom);
			return;
		}
		join(activeProfile.value);
	}

	function start() {
		join(activeProfile.value);
		// sync flush: the room must switch the moment the profile does, not on
		// the next render tick (events emitted in between would be missed)
		stopWatch = watch(activeProfile, join, { flush: "sync" });
		window.frappe?.realtime?.socket?.on("connect", onConnect);
	}

	function stop() {
		if (stopWatch) {
			stopWatch();
			stopWatch = null;
		}
		const realtime = window.frappe?.realtime;
		realtime?.socket?.off("connect", onConnect);
		// Deliberately NOT emitting pos_profile_unsubscribe here: three
		// composables share one socket, and socket.leave() is global for it,
		// so the first stop() would silently kill the room for the other two
		// (e.g. unmounting POSSale stops stock listening and mutes customer
		// and profile events). Room membership is per-socket anyway: it dies
		// with the disconnect and join() leaves the old room on profile
		// switches, matching how core doctype_subscribe rooms behave.
		joinedRoom = null;
	}

	return { start, stop };
}
