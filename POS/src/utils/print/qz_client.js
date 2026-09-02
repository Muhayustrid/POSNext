import { connect, printHTML } from "@/utils/qzTray"

export function createQzDriver() {
	return {
		id: "qz",
		async isAvailable() {
			try {
				return await connect()
			} catch {
				return false
			}
		},
		async getStatus() {
			const ok = await connect()
			return { ok, code: ok ? 0 : -1 }
		},
		async printHTML(html, opts = {}) {
			const { printerName, ...options } = opts
			return printHTML(html, printerName, options)
		},
		describe() {
			return { id: "qz", label: "QZ Tray", detail: "desktop helper app" }
		},
	}
}
