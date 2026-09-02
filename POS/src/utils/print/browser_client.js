/**
 * Browser driver. printHTML here is only used as the final fallback when the
 * transport is invoked directly with an HTML string (e.g. Test Print). The
 * normal browser path still goes through printInvoice's /printview popup.
 */
export function createBrowserDriver() {
	return {
		id: "browser",
		async isAvailable() {
			return true
		},
		async getStatus() {
			return { ok: true, code: 0 }
		},
		async printHTML(html) {
			const w = window.open("", "_blank", "width=380,height=600")
			if (!w) throw new Error("Popup blocked — check browser settings")
			w.document.write(html)
			w.document.close()
			w.onload = () => setTimeout(() => w.print(), 250)
			return true
		},
		describe() {
			return { id: "browser", label: "Browser", detail: "system print dialog" }
		},
	}
}
