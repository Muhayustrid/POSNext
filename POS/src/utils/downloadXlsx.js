/**
 * Download a server-built .xlsx (frappe build_xlsx_response) from a whitelisted
 * GET method. fetch + blob, not a bare link: a permission or validation error
 * comes back as JSON and must surface as a message, not a broken file.
 */
export async function downloadXlsx(method, params = {}) {
	const query = new URLSearchParams(
		Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== ""),
	)
	const response = await fetch(`/api/method/${method}?${query}`, {
		headers: { "X-Frappe-CSRF-Token": window.csrf_token || "" },
	})
	if (!response.ok) {
		let message = ""
		try {
			const body = await response.json()
			message = JSON.parse(body._server_messages || "[]")
				.map((m) => JSON.parse(m).message)
				.join("\n")
		} catch {
			// non-JSON error body: fall through to the generic message
		}
		throw new Error(message || __("Export failed"))
	}
	const disposition = response.headers.get("Content-Disposition") || ""
	const filename = /filename="?([^";]+)"?/.exec(disposition)?.[1] || "export.xlsx"
	const url = URL.createObjectURL(await response.blob())
	const link = document.createElement("a")
	link.href = url
	link.download = filename
	document.body.appendChild(link)
	link.click()
	link.remove()
	URL.revokeObjectURL(url)
}
