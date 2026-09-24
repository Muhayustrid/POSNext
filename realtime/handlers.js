// PERF-06/SEC-NEW-04: transaction events (pos_stock_update, pos_invoice_created,
// pos_profile_updated, pos_customer_changed) are published to `pos_profile:<name>`
// rooms only (see pos_next/realtime_events.py) instead of site-wide. Frappe has no
// generic room-join for clients, so apps register their own socket handlers here
// (loaded automatically from <app>/realtime/handlers.js by the realtime service).
// Joining requires server-side verification that the socket's user is a POS
// Profile User of that profile; leaving is always allowed.
function pos_next_handlers(socket) {
	const profile_room = (pos_profile) => `pos_profile:${pos_profile}`;

	socket.on("pos_profile_subscribe", (pos_profile) => {
		if (typeof pos_profile !== "string" || !pos_profile) {
			return;
		}
		socket
			.frappe_request("/api/method/pos_next.realtime_events.has_pos_profile_access", {
				pos_profile,
			})
			.then((res) => res.json())
			.then(({ message }) => {
				if (message) {
					socket.join(profile_room(pos_profile));
				}
			})
			.catch(() => {});
	});

	socket.on("pos_profile_unsubscribe", (pos_profile) => {
		if (typeof pos_profile !== "string" || !pos_profile) {
			return;
		}
		socket.leave(profile_room(pos_profile));
	});
}

module.exports = pos_next_handlers;
