import { beforeEach, describe, expect, it, vi } from "vitest";
import { shiftState } from "@/composables/useShift";
import { createProfileRoomBinding } from "@/composables/usePosProfileRoom";

// PERF-06/SEC-NEW-04: subscribers must join the socket room of the ACTIVE POS
// profile (pos_profile_subscribe) and leave it again, so room-scoped backend
// events actually reach the terminal and nothing else.
function mockRealtime() {
	const connectHandlers = new Set();
	const socket = {
		on: vi.fn((event, cb) => {
			if (event === "connect") connectHandlers.add(cb);
		}),
		off: vi.fn((event, cb) => {
			if (event === "connect") connectHandlers.delete(cb);
		}),
	};
	const realtime = {
		socket,
		emit: vi.fn(),
		on: vi.fn(),
		off: vi.fn(),
	};
	window.frappe = { realtime };
	return { realtime, connectHandlers };
}

const setProfile = (name) => {
	shiftState.value = { ...shiftState.value, pos_profile: name ? { name } : null };
};

describe("createProfileRoomBinding", () => {
	beforeEach(() => {
		setProfile(null);
	});

	it("joins the active profile room on start", () => {
		const { realtime } = mockRealtime();
		setProfile("Register A");

		const binding = createProfileRoomBinding();
		binding.start();

		expect(realtime.emit).toHaveBeenCalledWith("pos_profile_subscribe", "Register A");
		binding.stop();
	});

	it("switches rooms when the active profile changes", () => {
		const { realtime } = mockRealtime();
		setProfile("Register A");
		const binding = createProfileRoomBinding();
		binding.start();

		setProfile("Register B");

		expect(realtime.emit).toHaveBeenCalledWith("pos_profile_unsubscribe", "Register A");
		expect(realtime.emit).toHaveBeenCalledWith("pos_profile_subscribe", "Register B");
		binding.stop();
	});

	it("stops reacting on stop but keeps the shared socket in the room", () => {
		const { realtime } = mockRealtime();
		setProfile("Register A");
		const binding = createProfileRoomBinding();
		binding.start();
		binding.stop();

		// stop() must NOT unsubscribe: the socket (and its room membership)
		// is shared by every realtime composable, so one listener stopping
		// must not mute the others. The room dies with the socket instead.
		expect(realtime.emit).not.toHaveBeenCalledWith("pos_profile_unsubscribe", "Register A");

		realtime.emit.mockClear();
		setProfile("Register C");
		expect(realtime.emit).not.toHaveBeenCalled();
	});

	it("re-joins the room after a socket reconnect", () => {
		const { realtime, connectHandlers } = mockRealtime();
		setProfile("Register A");
		const binding = createProfileRoomBinding();
		binding.start();

		expect(connectHandlers.size).toBe(1);
		realtime.emit.mockClear();
		connectHandlers.forEach((cb) => cb());

		expect(realtime.emit).toHaveBeenCalledWith("pos_profile_subscribe", "Register A");
		binding.stop();
	});

	it("does nothing without an active profile", () => {
		const { realtime } = mockRealtime();
		const binding = createProfileRoomBinding();
		binding.start();

		expect(realtime.emit).not.toHaveBeenCalled();
		binding.stop();
	});
});
