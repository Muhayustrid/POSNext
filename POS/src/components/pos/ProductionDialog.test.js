/**
 * @vitest-environment jsdom
 */
import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"

// Route createResource submits by API url so tests can resolve/reject per resource.
const resourceHandlers = vi.hoisted(() => ({ map: {} }))

vi.mock("frappe-ui", async () => {
	const { defineComponent, h } = await import("vue")
	// Render-function stubs: the app builds with the runtime-only Vue build
	// (no template compiler), so stubs must not use the `template` option.
	const Dialog = defineComponent({
		name: "DialogStub",
		props: ["modelValue", "options"],
		setup(_, { slots }) {
			return () => slots["body-content"]?.()
		},
	})
	// Button renders a real <button> root so @click listeners fall through.
	const Button = defineComponent({
		name: "ButtonStub",
		props: ["loading", "disabled", "variant"],
		setup(_, { slots }) {
			return () => h("button", slots.default?.())
		},
	})
	return {
		Dialog,
		Button,
		createResource: (opts) => ({
			submit: (params) => resourceHandlers.map[opts.url]?.(params, opts),
		}),
	}
})

// Provide a trivial global translation helper the way ShiftClosingDialog.test does.
globalThis.__ = (message, replacements = []) => {
	if (!Array.isArray(replacements) || !replacements.length) return message
	let out = message
	for (const [i, v] of replacements.entries())
		out = out.split(`{${i}}`).join(String(v))
	return out
}

import ProductionDialog from "./ProductionDialog.vue"

const RECIPES_URL = "pos_next.api.production.get_production_recipes"
const CREATE_URL = "pos_next.api.production.create_production"

const RECIPES = [
	{
		name: "RECIPE-0001",
		recipe_name: "Iced Latte",
		production_item: "ICED-LATTE",
		production_item_name: "Iced Latte",
		output_qty: 2,
		fg_stock: 10,
		fg_has_batch_no: false,
		items: [
			{
				item_code: "MILK",
				item_name: "Milk",
				qty: 1,
				stock_uom: "Litre",
				has_batch_no: false,
				available_qty: 50,
				batches: [],
			},
			{
				item_code: "SYRUP",
				item_name: "Syrup",
				qty: 0.5,
				stock_uom: "Litre",
				has_batch_no: true,
				available_qty: 4,
				batches: [
					{ batch_no: "B-OLD", qty: 1, expiry_date: "2026-01-01" },
					{ batch_no: "B-FRESH", qty: 5, expiry_date: "2026-12-31" },
				],
			},
		],
	},
]

function respond(url, handler) {
	resourceHandlers.map[url] = handler
}

/** Mount closed, then open via props so the modelValue watch fires loadRecipes. */
async function mountOpenDialog() {
	const wrapper = mount(ProductionDialog, {
		props: {
			modelValue: false,
			posProfile: "POS-1",
			company: "Co",
			currency: "IDR",
		},
		global: {
			// The app installs __() as a global property; the template needs it.
			config: { globalProperties: { __: globalThis.__ } },
		},
	})
	await wrapper.setProps({ modelValue: true })
	await flushPromises()
	return wrapper
}

function findButton(wrapper, text) {
	return wrapper.findAll("button").find((b) => b.text().includes(text))
}

async function selectFirstRecipe(wrapper) {
	await findButton(wrapper, "Iced Latte").trigger("click")
	await flushPromises()
}

describe("ProductionDialog", () => {
	beforeEach(() => {
		resourceHandlers.map = {}
		respond(RECIPES_URL, (_params, opts) =>
			opts.onSuccess({
				pos_profile: "POS-1",
				company: "Co",
				warehouse: "WH",
				recipes: RECIPES,
			}),
		)
	})

	it("shows the loading placeholder while recipes are being fetched", async () => {
		respond(RECIPES_URL, () => new Promise(() => {})) // never resolves
		const wrapper = await mountOpenDialog()
		expect(wrapper.text()).toContain("Loading recipes")
	})

	it("lists recipes with material availability after load", async () => {
		const wrapper = await mountOpenDialog()
		expect(wrapper.text()).toContain("Iced Latte")
		expect(wrapper.text()).toContain("makes 2 × Iced Latte")
		expect(wrapper.text()).toContain("Materials available")
	})

	it("selects a recipe and rescales material rows when output qty changes", async () => {
		const wrapper = await mountOpenDialog()
		await selectFirstRecipe(wrapper)

		expect(wrapper.text()).toContain("Output per run: 2 × Iced Latte")
		const qtyInputs = wrapper.findAll('input[type="number"]')
		// first number input is the output qty; the rest are material rows
		expect(qtyInputs).toHaveLength(3)
		expect(qtyInputs[1].element.value).toBe("1") // MILK: 1 × (2/2)
		expect(qtyInputs[2].element.value).toBe("0.5") // SYRUP: 0.5 × (2/2)
		// FIFO pick: first batch with enough stock for the base run
		expect(wrapper.find("select").element.value).toBe("B-OLD")

		await qtyInputs[0].setValue("4")
		await flushPromises()
		const rescaled = wrapper.findAll('input[type="number"]')
		expect(rescaled[1].element.value).toBe("2") // MILK: 1 × (4/2)
		expect(rescaled[2].element.value).toBe("1") // SYRUP: 0.5 × (4/2)
	})

	it("submits recipe, scaled items and batch picks, then emits production-created and closes", async () => {
		let payload
		respond(CREATE_URL, (params, opts) => {
			payload = params
			opts.onSuccess({
				stock_entry: "STE-0001",
				production_log: "PLOG-0001",
				production_item: "ICED-LATTE",
				qty: 2,
			})
		})

		const wrapper = await mountOpenDialog()
		await selectFirstRecipe(wrapper)
		await findButton(wrapper, "Process Production").trigger("click")
		await flushPromises()

		expect(payload).toEqual({
			recipe: "RECIPE-0001",
			qty: 2,
			items: [
				{ item_code: "MILK", qty: 1 },
				{ item_code: "SYRUP", qty: 0.5 },
			],
			pos_profile: "POS-1",
			batches: { SYRUP: "B-OLD" },
		})
		expect(wrapper.emitted("production-created")).toContainEqual([
			{
				stock_entry: "STE-0001",
				production_log: "PLOG-0001",
				production_item: "ICED-LATTE",
				qty: 2,
			},
		])
		expect(wrapper.emitted("update:modelValue")).toContainEqual([false])
	})

	it("surfaces backend errors and keeps the dialog open", async () => {
		respond(CREATE_URL, (_params, opts) =>
			opts.onError({ messages: ["Insufficient stock for Syrup"] }),
		)

		const wrapper = await mountOpenDialog()
		await selectFirstRecipe(wrapper)
		await findButton(wrapper, "Process Production").trigger("click")
		await flushPromises()

		expect(wrapper.text()).toContain("Insufficient stock for Syrup")
		expect(wrapper.emitted("update:modelValue")).not.toContainEqual([false])
	})
})
