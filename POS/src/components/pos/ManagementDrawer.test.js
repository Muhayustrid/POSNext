/**
 * @vitest-environment jsdom
 */
import { flushPromises, mount } from "@vue/test-utils"
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest"
import { nextTick } from "vue"

vi.mock("frappe-ui", async () => {
	const { defineComponent } = await import("vue")
	const stub = defineComponent({ name: "FeatherIconStub", render: () => null })
	return { FeatherIcon: stub }
})

// Trivial global translation helper (identity) like the other component tests.
globalThis.__ = (message, replacements = []) => {
	if (!Array.isArray(replacements) || !replacements.length) return message
	let out = message
	for (const [i, v] of replacements.entries())
		out = out.split(`{${i}}`).join(String(v))
	return out
}

import ManagementDrawer from "./ManagementDrawer.vue"
import ManagementSlider from "./ManagementSlider.vue"
import POSHeader from "./POSHeader.vue"
import { MANAGEMENT_MENU } from "./managementMenu"

function installMatchMedia(matches = false) {
	const listeners = new Set()
	const mql = {
		matches,
		addEventListener: (_type, fn) => listeners.add(fn),
		removeEventListener: (_type, fn) => listeners.delete(fn),
	}
	window.matchMedia = vi.fn(() => mql)
	return {
		fire(next) {
			mql.matches = next
			listeners.forEach((fn) => fn({ matches: next }))
		},
	}
}

function visibleLabels(showProduction) {
	return MANAGEMENT_MENU.filter(
		(i) => !i.requiresProduction || showProduction,
	).map((i) => i.label)
}

function drawerItemLabels() {
	// First button inside the nav is the X close control; the rest are menu items.
	const buttons = document.querySelectorAll("#management-drawer button")
	return Array.from(buttons)
		.slice(1)
		.map((b) => b.textContent.trim())
}

function sliderLabels(wrapper) {
	return wrapper.findAll("button").map((b) => b.attributes("title"))
}

let mql

beforeEach(() => {
	document.body.innerHTML = ""
	document.body.style.overflow = ""
	mql = installMatchMedia(false)
})

afterEach(() => {
	document.body.style.overflow = ""
})

describe("management menu parity (desktop rail vs mobile drawer)", () => {
	it("both surfaces render the same items in the same order", () => {
		const slider = mountSlider(true)
		const drawer = mountDrawer(true)

		expect(visibleLabels(true)).toContain("Settings")
		expect(sliderLabels(slider)).toEqual(drawerItemLabels())
	})

	it("production item follows the same permission rule on both surfaces", () => {
		const slider = mountSlider(false)
		const drawer = mountDrawer(false)

		const labels = sliderLabels(slider)
		expect(labels).not.toContain("Production")
		expect(drawerItemLabels()).toEqual(labels)
		expect(visibleLabels(false)).toEqual(labels)
	})

	function mountSlider(showProduction) {
		return mountComponent(ManagementSlider, { showProduction })
	}

	function mountDrawer(showProduction) {
		return mountComponent(ManagementDrawer, { showProduction })
	}
})

describe("ManagementDrawer behaviour", () => {
	it("renders a modal dialog with aria semantics and locks body scroll", () => {
		const wrapper = mountComponent(ManagementDrawer, { showProduction: false })
		const nav = document.getElementById("management-drawer")

		expect(nav).not.toBeNull()
		expect(nav.getAttribute("role")).toBe("dialog")
		expect(nav.getAttribute("aria-modal")).toBe("true")
		expect(document.body.style.overflow).toBe("hidden")
		wrapper.unmount()
	})

	it("restores body scroll on unmount", () => {
		document.body.style.overflow = "auto"
		const wrapper = mountComponent(ManagementDrawer, { showProduction: false })
		wrapper.unmount()
		expect(document.body.style.overflow).toBe("auto")
	})

	it("closes via backdrop, X button, item selection and Escape", async () => {
		const wrapper = mountComponent(ManagementDrawer, { showProduction: false })
		const closeEvents = () => wrapper.emitted("close")?.length ?? 0

		document
			.querySelector('[data-testid="drawer-backdrop"]')
			.dispatchEvent(new MouseEvent("click", { bubbles: true }))
		await nextTick()
		expect(closeEvents()).toBe(1)

		document
			.querySelector('#management-drawer [aria-label="Close menu"]')
			.dispatchEvent(new MouseEvent("click", { bubbles: true }))
		await nextTick()
		expect(closeEvents()).toBe(2)

		document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }))
		await nextTick()
		expect(closeEvents()).toBe(3)

		// Selection navigates with the same ids the desktop rail emits, then closes.
		const item = document.querySelectorAll("#management-drawer button")[2] // products
		item.dispatchEvent(new MouseEvent("click", { bubbles: true }))
		await nextTick()
		expect(wrapper.emitted("navigate")).toEqual([["products"]])
		expect(closeEvents()).toBe(4)
		wrapper.unmount()
	})

	it("traps Tab focus inside the dialog", async () => {
		const wrapper = mountComponent(ManagementDrawer, { showProduction: true })
		await flushPromises()
		const buttons = document.querySelectorAll("#management-drawer button")
		const first = buttons[0]
		const last = buttons[buttons.length - 1]

		// Drawer focuses the close button on open.
		expect(document.activeElement).toBe(first)

		last.focus()
		document.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab" }))
		expect(document.activeElement).toBe(first)

		first.focus()
		document.dispatchEvent(
			new KeyboardEvent("keydown", { key: "Tab", shiftKey: true }),
		)
		expect(document.activeElement).toBe(last)
		wrapper.unmount()
	})

	it("closes when the viewport grows to the desktop breakpoint", () => {
		const wrapper = mountComponent(ManagementDrawer, { showProduction: false })
		mql.fire(true)
		expect(wrapper.emitted("close")).toHaveLength(1)
		wrapper.unmount()
	})
})

describe("POSHeader hamburger trigger", () => {
	const mountHeader = (props) =>
		mountComponent(
			POSHeader,
			{
				currentTime: "10:00",
				userName: "Tester",
				...props,
			},
			{
				ActionButton: true,
				StatusBadge: true,
				UserMenu: true,
				LanguageSwitcher: true,
			},
		)

	const trigger = (wrapper) =>
		wrapper.find('button[aria-controls="management-drawer"]')

	it("shows a collapsed-state-only trigger that toggles the drawer", async () => {
		const wrapper = mountHeader()
		const btn = trigger(wrapper)

		// Same breakpoint class that hides the desktop rail (lg:hidden vs hidden lg:flex).
		expect(btn.classes()).toContain("lg:hidden")
		expect(btn.attributes("aria-expanded")).toBe("false")

		await btn.trigger("click")
		expect(btn.attributes("aria-expanded")).toBe("true")
		expect(document.getElementById("management-drawer")).not.toBeNull()

		document
			.querySelector('button[aria-label="Close menu"]')
			.dispatchEvent(new MouseEvent("click", { bubbles: true }))
		await nextTick()
		expect(btn.attributes("aria-expanded")).toBe("false")
		expect(document.getElementById("management-drawer")).toBeNull()
		wrapper.unmount()
	})

	it("restores focus to the trigger after closing", async () => {
		const wrapper = mountHeader()
		const btn = trigger(wrapper)
		await btn.trigger("click")
		await flushPromises()

		document
			.querySelector('[data-testid="drawer-backdrop"]')
			.dispatchEvent(new MouseEvent("click", { bubbles: true }))
		await flushPromises()
		expect(document.activeElement).toBe(btn.element)
		wrapper.unmount()
	})

	it("never opens over a mandatory dialog and closes if one appears", async () => {
		const guarded = mountHeader({ isAnyDialogOpen: true })
		await trigger(guarded).trigger("click")
		expect(document.getElementById("management-drawer")).toBeNull()
		guarded.unmount()

		const wrapper = mountHeader()
		await trigger(wrapper).trigger("click")
		expect(document.getElementById("management-drawer")).not.toBeNull()

		await wrapper.setProps({ isAnyDialogOpen: true })
		expect(document.getElementById("management-drawer")).toBeNull()
		wrapper.unmount()
	})
})

function mountComponent(component, props = {}, stubs = {}) {
	return mount(component, {
		props,
		attachTo: document.body,
		global: {
			config: { globalProperties: { __: globalThis.__ } },
			stubs,
		},
	})
}
