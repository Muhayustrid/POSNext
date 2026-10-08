/**
 * Browser driver. printHTML here is only used as the final fallback when the
 * transport is invoked directly with an HTML string (e.g. Test Print). The
 * normal browser path still goes through printInvoice's /printview popup.
 */
import { __ } from "@/utils/translation"

export function createBrowserDriver() {
	return {
		id: "browser",
		async isAvailable() {
			return true
		},
		async getStatus() {
			return { ok: true, code: 0 }
		},
		// Hidden iframe, not window.open: prints reach here after awaits (shift
		// submit, HTML fetch), so a popup is no longer tied to the click and
		// popup blockers drop it intermittently. iframe print() is not gated.
		async printHTML(html) {
			const frame = document.createElement("iframe")
			frame.setAttribute("aria-hidden", "true")
			frame.style.cssText =
				"position:fixed;right:0;bottom:0;width:0;height:0;border:0;visibility:hidden"
			document.body.appendChild(frame)
			const w = frame.contentWindow
			if (!w) {
				frame.remove()
				throw new Error(__("Print frame unavailable"))
			}
			let printed = false
			const doPrint = () => {
				if (printed) return
				printed = true
				w.focus()
				w.print()
				// ponytail: fixed 60s cleanup; afterprint is unreliable on some browsers
				setTimeout(() => frame.remove(), 60000)
			}
			// load fires once images (logo) are in; the timeout covers browsers
			// that skip load for a written about:blank document.
			frame.addEventListener("load", () => setTimeout(doPrint, 250))
			setTimeout(doPrint, 3000)
			// Same server-rendered print HTML the popup used to receive.
			w.document.open()
			w.document.write(html)
			w.document.close()
			return true
		},
		describe() {
			return { id: "browser", label: __("Browser"), detail: __("system print dialog") }
		},
	}
}
