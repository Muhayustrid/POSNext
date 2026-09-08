/**
 * @vitest-environment jsdom
 *
 * Small-screen responsive contracts. jsdom has no layout engine, so these
 * pin the structural decisions the layout relies on (bounded dialog bodies,
 * single scroll regions, wrap-instead-of-clip cart rows, 44px primary touch
 * targets). Visual/bounds verification lives in the Playwright harness audit
 * (gui-test-screenshots/responsive-*-metrics.json).
 */
import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"
import { createPinia } from "pinia"

const resources = vi.hoisted(() => ({ instances: [] }))

vi.mock("frappe-ui", async () => {
	const { defineComponent, reactive } = await import("vue")
	const Button = defineComponent({
		name: "Button",
		emits: ["click"],
		template: `<button v-bind="$attrs" @click="$emit('click')"><slot /></button>`,
	})
	const Dialog = defineComponent({
		name: "Dialog",
		props: {
			modelValue: { type: Boolean, default: false },
			options: { type: Object, default: () => ({}) },
		},
		// render both slots the way the app's dialogs split body/footer
		template: `<div><slot name="body" /><slot name="body-content" /><slot name="actions" /></div>`,
	})
	const stub = defineComponent({ name: "FrappeUIStub", render: () => null })
	return {
		Button,
		Dialog,
		FeatherIcon: stub,
		Input: stub,
		TextInput: stub,
		createResource: (opts) => {
			const reload = vi.fn(() => {
				r.loading = true
			})
			const r = reactive({ loading: false, data: null, error: null, reload })
			resources.instances.push({ url: opts.url, opts, resource: r })
			return r
		},
	}
})

vi.mock("reka-ui", () => ({
	DialogTitle: { name: "DialogTitle", template: "<h2><slot /></h2>" },
}))

vi.mock("@/composables/useFormatters", () => ({
	useFormatters: () => ({
		formatCurrency: (v) => String(v ?? ""),
		formatQuantity: (v) => String(v ?? ""),
		formatDate: (v) => String(v ?? ""),
		formatTime: (v) => String(v ?? ""),
		formatDateTime: (v) => String(v ?? ""),
	}),
}))

vi.mock("@/utils/offline/workerClient", () => ({
	// catch-all: every worker method becomes a rejected async no-op
	offlineWorker: new Proxy(
		{},
		{
			get: (t, prop) =>
				prop in t ? t[prop] : vi.fn().mockRejectedValue(new Error("offline")),
		},
	),
}))

vi.mock("@/utils/offline", () => ({ isOffline: () => false }))

// The app installs __() as a global property; some modules call it at
// module-evaluation time, so this must be hoisted above the imports.
vi.hoisted(() => {
	globalThis.__ = (message, replacements = []) => {
		if (!Array.isArray(replacements) || !replacements.length) return message
		let out = message
		for (const [i, v] of replacements.entries())
			out = out.split(`{${i}}`).join(String(v))
		return out
	}
})

import { readFileSync } from "node:fs"
import { resolve } from "node:path"
import InvoiceHistoryDialog from "./InvoiceHistoryDialog.vue"
import InvoiceCart from "./InvoiceCart.vue"
import POSHeader from "../pos/POSHeader.vue"

const CART_ITEM = {
	item_code: "ITEM-1",
	item_name: "Kopi Susu Gula Aren Kekinian Spesial 350ml",
	item_group: "Makanan",
	quantity: 1,
	qty: 1,
	rate: 25000,
	amount: 25000,
	uom: "Nos",
	stock_uom: "Nos",
	conversion_factor: 1,
	is_free_item: false,
	has_serial_no: 0,
	is_resolved_barcode: false,
}

describe("small-screen responsive contracts", () => {
	beforeEach(() => {
		resources.instances.length = 0
	})

	it("defines the xs breakpoint that header clock and pagination labels use", () => {
		// `hidden xs:inline` / `xs:hidden` were dead classes before this
		// screen existed; the layouts above 400px depend on it. The config
		// pulls the frappe-ui preset, which vitest cannot import directly.
		const configSrc = readFileSync(
			resolve(process.cwd(), "tailwind.config.js"),
			"utf8",
		)
		expect(configSrc).toMatch(/xs:\s*"400px"/)
	})

	it("bounds the invoice history dialog with one scroll region and a visible footer", async () => {
		const wrapper = mount(InvoiceHistoryDialog, {
			props: {
				modelValue: true,
				posProfile: "Kasir 1",
				posOpeningShift: "POS-1",
			},
			global: {
				plugins: [createPinia()],
				config: { globalProperties: { __: globalThis.__ } },
			},
		})
		await flushPromises()

		const body = wrapper.find('[data-test="dialog-body"]')
		const footer = wrapper.find('[data-test="dialog-footer"]')
		expect(body.exists()).toBe(true)
		expect(footer.exists()).toBe(true)

		const shell = body.element.closest(".flex.flex-col")
		expect(shell.className).toContain("max-h-[calc(100dvh-6rem)]")

		// exactly one scroll container inside the bounded shell (the body)
		const scrollers = [...shell.querySelectorAll("*")].filter((el) =>
			el.className.split?.(" ").includes("overflow-y-auto"),
		)
		expect(scrollers).toHaveLength(1)
		wrapper.unmount()
	})

	it("keeps the shift closing footer reachable via a bounded body region", async () => {
		const getClosingShiftData = {
			submit: vi.fn().mockResolvedValue({ pos_profile: "P" }),
			loading: false,
			error: null,
		}
		const submitClosingShift = {
			submit: vi.fn().mockResolvedValue({}),
			loading: false,
		}
		vi.doMock("../../composables/useShift", () => ({
			useShift: () => ({ getClosingShiftData, submitClosingShift }),
			shiftState: { value: {} },
		}))
		vi.doMock("../../stores/posSync", () => ({
			usePOSSyncStore: () => ({
				hasPendingInvoices: false,
				pendingInvoicesCount: 0,
				updatePendingCount: vi.fn().mockResolvedValue(undefined),
			}),
		}))
		vi.doMock("../../stores/posCart", () => ({
			usePOSCartStore: () => ({ isSubmitting: false }),
		}))
		vi.doMock("../../stores/posSettings", async () => {
			const { ref } = await import("vue")
			// a ref (not a plain boolean): storeToRefs() in
			// ShiftClosingDialog skips non-ref values, leaving the computed
			// reading `.value` with undefined
			const reloadSettings = vi.fn().mockResolvedValue(undefined)
			return {
				usePOSSettingsStore: () => ({
					hideExpectedAmount: ref(false),
					reloadSettings,
				}),
			}
		})
		const { default: ShiftClosingDialog } = await import(
			"../ShiftClosingDialog.vue"
		)

		const wrapper = mount(ShiftClosingDialog, {
			props: { modelValue: false, openingShift: "POS-OPEN-1" },
			global: {
				plugins: [createPinia()],
				config: { globalProperties: { __: globalThis.__ } },
				stubs: { TranslatedHTML: true },
			},
		})
		// the open watch loads closing data only on a false -> true transition
		await wrapper.setProps({ modelValue: true })
		await flushPromises()
		await flushPromises()

		const bound = wrapper.find(".pos-dialog-bound")
		expect(bound.exists()).toBe(true)
		wrapper.unmount()
	})

	it("wraps cart item rows instead of clipping them on narrow phones", () => {
		const wrapper = mount(InvoiceCart, {
			props: {
				items: [CART_ITEM],
				customer: null,
				subtotal: 25000,
				taxAmount: 0,
				discountAmount: 0,
				grandTotal: 25000,
				posProfile: "Kasir 1",
				currency: "IDR",
				appliedOffers: [],
				warehouses: [],
			},
			global: {
				plugins: [createPinia()],
				config: { globalProperties: { __: globalThis.__ } },
			},
		})

		// qty/UOM/total line must wrap and keep the total right-aligned
		const row = wrapper.find(".flex-wrap.items-center")
		expect(row.exists()).toBe(true)
		const total = row.find(".ms-auto.text-end")
		expect(total.exists()).toBe(true)

		// remove targets keep a 24px hit area without shifting layout
		const remove = wrapper.find('button[aria-label^="Remove "]')
		expect(remove.classes()).toContain("p-1")
		expect(remove.classes()).toContain("-m-1")

		// checkout stays a reachable primary control in the pinned footer
		const checkout = wrapper.find('button[aria-label="Proceed to payment"]')
		expect(checkout.exists()).toBe(true)
		wrapper.unmount()
	})

	it("gives the header hamburger a 44px touch target", () => {
		const wrapper = mount(POSHeader, {
			props: { currentTime: "10:00", userName: "Kasir" },
			global: {
				config: { globalProperties: { __: globalThis.__ } },
				stubs: {
					ActionButton: true,
					StatusBadge: true,
					UserMenu: true,
					LanguageSwitcher: true,
					ManagementDrawer: true,
					FeatherIcon: true,
				},
			},
		})
		const hamburger = wrapper.find('button[aria-label="Open menu"]')
		expect(hamburger.exists()).toBe(true)
		expect(hamburger.classes()).toContain("h-11")
		expect(hamburger.classes()).toContain("w-11")

		// secondary header icon buttons get the mobile size bump
		const sync = wrapper.find('button[aria-label="Online mode active"]')
		expect(sync.classes()).toContain("pos-icon-btn")
		wrapper.unmount()
	})
})
