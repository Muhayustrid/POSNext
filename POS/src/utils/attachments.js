import { call } from "@/utils/apiWrapper"

export const ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024
export const ATTACHMENT_ACCEPT = "image/*,.pdf"

export function formatBytes(bytes) {
	const n = Number(bytes || 0)
	if (!n) return ""
	if (n < 1024) return `${n} B`
	if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`
	return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

export function fileToBase64(file) {
	return new Promise((resolve, reject) => {
		const reader = new FileReader()
		reader.onload = () => resolve(String(reader.result).split(",")[1] || "")
		reader.onerror = () => reject(reader.error || new Error("read failed"))
		reader.readAsDataURL(file)
	})
}

// split picked files into ok/tooBig — the caller toasts the rejects
export function sanitizePickedFiles(files) {
	const list = Array.from(files || [])
	return {
		ok: list.filter((f) => f.size <= ATTACHMENT_MAX_BYTES),
		tooBig: list.filter((f) => f.size > ATTACHMENT_MAX_BYTES),
	}
}

export async function uploadPurchaseFiles(doctype, name, files) {
	const payloads = await Promise.all(
		files.map(async (file) => ({
			file_name: file.name,
			filedata: await fileToBase64(file),
		})),
	)
	return call("pos_next.api.purchase_orders.attach_purchase_files", {
		doctype,
		name,
		files: JSON.stringify(payloads),
	})
}
