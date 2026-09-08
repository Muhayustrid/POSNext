import { call as frappeCall } from "frappe-ui";

import { forceRefreshCSRFToken, isCSRFApiError } from "./csrf";

// Wrapped call function with CSRF auto-refresh
export async function call(method, params) {
	try {
		return await frappeCall(method, params);
	} catch (error) {
		if (isCSRFApiError(error)) {
			console.warn("CSRF token error in call(), refreshing token and retrying...");
			const refreshed = await forceRefreshCSRFToken();

			if (refreshed) {
				console.log("Retrying call after CSRF refresh...");
				return await frappeCall(method, params);
			}

			console.warn("Could not refresh CSRF token. Server may have ignore_csrf enabled.");
		}

		throw error;
	}
}

/**
 * Extract the human-readable, server-translated message from a frappe-ui
 * resource error. The Error object stringifies as
 * "Error: <method> <ValidationError>" which is useless to cashiers; the real
 * message (already passed through frappe's `_()` in the user's language)
 * lives on `error.messages`.
 */
export function serverErrorMessage(error, fallback = "Something went wrong. Please try again.") {
	const messages = Array.isArray(error?.messages) ? error.messages.filter(Boolean) : [];
	if (messages.length) return messages.join(" ");
	return fallback;
}
