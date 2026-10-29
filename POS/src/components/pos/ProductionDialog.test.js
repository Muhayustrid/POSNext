/**
 * @vitest-environment jsdom
 */
import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"

// Route createResource submits by API url so tests can resolve/reject per
// resource; call() (start/finish/close/cancel) is routed the same way.
const handlers = vi.hoisted(() => ({ map: {}, calls: {} }))

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
		props: ["loading", "disabled", "variant", "theme", "size"],
		setup(props, { slots }) {
			return () =>
				h("button", { disabled: !!props.disabled }, slots.default?.())
		},
	})
	const FeatherIcon = defineComponent({
		name: "FeatherIconStub",
		props: ["name"],
	})
	return {
		Dialog,
		Button,
		FeatherIcon,
		createResource: (opts) => ({
			submit: (params) => handlers.map[opts.url]?.(params, opts),
		}),
		call: (url, params) =>
			handlers.calls[url]?.(params) ??
			Promise.reject(new Error(`no call mock for ${url}`)),
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
const ACTIVE_URL = "pos_next.api.production.get_active_productions"
const HISTORY_URL = "pos_next.api.production.get_production_history"
const START_URL = "pos_next.api.production.start_production"
const FINISH_URL = "pos_next.api.production.finish_production"
const CONTEXT_URL = "pos_next.api.production.get_finish_context"
const CLOSE_URL = "pos_next.api.production.close_production"
const CANCEL_URL = "pos_next.api.production.cancel_production"

const RECIPES = [
	{
		name: "RECIPE-0001",
		recipe_name: "Iced Latte",
		production_item: "ICED-LATTE",
		production_item_name: "Iced Latte",
		output_qty: 2,
		output_uom: "Cup",
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

/** Server-style datetime ("YYYY-MM-DD HH:mm:ss", device-local) for elapsed tests. */
function serverDate(date) {
	const pad = (n) => String(n).padStart(2, "0")
	return (
		`${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
		`${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
	)
}

const ACTIVE = {
	pos_profile: "POS-1",
	company: "Co",
	productions: [
		{
			work_order: "WO-0001",
			production_item: "ICED-LATTE",
			production_item_name: "Iced Latte",
			recipe: "RECIPE-0001",
			recipe_name: "Iced Latte",
			qty: 10,
			produced_qty: 2,
			process_loss_qty: 0,
			status: "In Process",
			pos_status: "In Progress",
			operator: "op@example.com",
			started_at: Math.floor(Date.now() / 1000) - 120 * 60, // epoch seconds
			updated_at: serverDate(new Date()),
		},
	],
}

const ACTIVE_ZERO = {
	pos_profile: "POS-1",
	company: "Co",
	productions: [
		{
			...ACTIVE.productions[0],
			produced_qty: 0,
			status: "Not Started",
			pos_status: "Not Started",
			// naive string = payload API lama; menjaga jalur fallback parseServerDate
			started_at: serverDate(new Date()),
		},
	],
}

const HISTORY = {
	pos_profile: "POS-1",
	company: "Co",
	productions: [
		{
			work_order: "WO-0009",
			production_item: "ICED-LATTE",
			production_item_name: "Iced Latte",
			recipe: "RECIPE-0001",
			recipe_name: "Iced Latte",
			qty: 5,
			produced_qty: 5,
			process_loss_qty: 0,
			status: "Completed",
			pos_status: "Completed",
			operator: "op@example.com",
			started_at: "2026-09-26 08:00:00",
			created_at: "2026-09-26 08:30:00",
		},
	],
	has_more: false,
}

const FINISH_CTX = {
	work_order: "WO-0001",
	wo_qty: 10,
	fg_has_batch_no: false,
	materials: [
		{
			item_code: "MILK",
			item_name: "Milk",
			required_qty: 5,
			stock_uom: "Litre",
			has_batch_no: false,
			batches: [],
		},
		{
			item_code: "SYRUP",
			item_name: "Syrup",
			required_qty: 4,
			stock_uom: "Litre",
			has_batch_no: true,
			batches: [
				{ batch_no: "B-OLD", qty: 2, expiry_date: "2026-12-01" },
				{ batch_no: "B-NEW", qty: 9, expiry_date: "2027-01-15" },
			],
		},
	],
}

function respond(url, handler) {
	handlers.map[url] = handler
}

function mockCall(url, impl) {
	handlers.calls[url] = vi.fn(impl)
	return handlers.calls[url]
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

async function showActiveList(wrapper) {
	await findButton(wrapper, "In Production").trigger("click")
	await flushPromises()
}

describe("ProductionDialog", () => {
	beforeEach(() => {
		handlers.map = {}
		handlers.calls = {}
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

	it("lists recipes with what the material stock can still make", async () => {
		const wrapper = await mountOpenDialog()
		expect(wrapper.text()).toContain("Iced Latte")
		expect(wrapper.text()).not.toContain("makes 2 × Iced Latte")
		// SYRUP limits: 4 / 0.5 × 2 = 16 cups (MILK allows 100)
		expect(wrapper.text()).toContain("Stock is enough for 16 Cup")
	})

	it("names the short material when stock cannot make a single unit", async () => {
		const short = structuredClone(RECIPES)
		short[0].items[0].available_qty = 0.4 // MILK needs 0.5 per cup
		respond(RECIPES_URL, (_params, opts) =>
			opts.onSuccess({ pos_profile: "POS-1", company: "Co", warehouse: "WH", recipes: short }),
		)
		const wrapper = await mountOpenDialog()
		expect(wrapper.text()).toContain("Not enough Milk")
	})

	it("selects a recipe and rescales material rows by output units, not batches", async () => {
		const wrapper = await mountOpenDialog()
		await selectFirstRecipe(wrapper)

		expect(wrapper.text()).not.toContain("Output per run")
		expect(wrapper.text()).not.toContain("Number of batches")
		expect(wrapper.text()).toContain("Stock is enough for 16 Cup")
		expect(wrapper.text()).toContain("Limited by Syrup")
		// starts at one unit, not at the recipe's output qty
		const qtyInputs = wrapper.findAll('input[type="number"]')
		expect(qtyInputs).toHaveLength(1)
		expect(qtyInputs[0].element.value).toBe("1")
		expect(wrapper.text()).toContain("0.5 Litre") // MILK: 1 / 2 cups
		expect(wrapper.text()).toContain("0.25 Litre") // SYRUP: 0.5 / 2 cups
		// FIFO pick: first batch with enough stock for the base run
		expect(wrapper.text()).toContain("B-OLD (1)")

		await qtyInputs[0].setValue("4")
		await flushPromises()
		expect(wrapper.text()).toContain("2 Litre") // MILK: 1 × 4/2
		expect(wrapper.text()).toContain("1 Litre") // SYRUP: 0.5 × 4/2

		expect(wrapper.findAll("button").some((button) => button.text().includes("Max ("))).toBe(false)
		await qtyInputs[0].setValue("16")
		await flushPromises()
		expect(qtyInputs[0].element.value).toBe("16")
		expect(wrapper.text()).toContain("8 Litre") // MILK: 1 × 16/2
	})

	it("allows fractional qty when the output UOM is not whole-number", async () => {
		const kg = structuredClone(RECIPES)
		kg[0].output_uom = "Kg"
		kg[0].output_whole = false
		kg[0].items[1].available_qty = 4.1 // SYRUP: 4.1 / 0.5 × 2 = 16.4 Kg
		respond(RECIPES_URL, (_params, opts) =>
			opts.onSuccess({ pos_profile: "POS-1", company: "Co", warehouse: "WH", recipes: kg }),
		)
		const wrapper = await mountOpenDialog()
		await selectFirstRecipe(wrapper)
		expect(wrapper.text()).toContain("Stock is enough for 16.4 Kg")
		const input = wrapper.find("#production-qty")
		expect(input.attributes("inputmode")).toBe("decimal")
		await input.setValue("0.5")
		await flushPromises()
		expect(wrapper.text()).toContain("0.25 Litre") // MILK: 1 × 0.5/2
	})

	it("sends the typed unit qty to start_production", async () => {
		const startCall = mockCall(START_URL, () =>
			Promise.resolve({ work_order: "WO-1", qty: 3 }),
		)
		respond(ACTIVE_URL, (_params, opts) => opts.onSuccess(ACTIVE_ZERO))
		const wrapper = await mountOpenDialog()
		await selectFirstRecipe(wrapper)
		await wrapper.find('button[aria-label="Increase"]').trigger("click")
		await wrapper.find('button[aria-label="Increase"]').trigger("click")
		await findButton(wrapper, "Start Production").trigger("click")
		await flushPromises()
		expect(startCall).toHaveBeenCalledWith({
			recipe: "RECIPE-0001",
			qty: 3,
			pos_profile: "POS-1",
		})
	})

	it("locks the detail view: no change-recipe, no add material, no editable material qty", async () => {
		const wrapper = await mountOpenDialog()
		await selectFirstRecipe(wrapper)

		expect(findButton(wrapper, "Change recipe")).toBeUndefined()
		expect(wrapper.text()).not.toContain("Add material")
		// a single editable input (output qty); Back/Start are buttons
		expect(wrapper.findAll("input")).toHaveLength(1)
		expect(wrapper.findAll("select")).toHaveLength(0)
	})

	it("offers only Start Production from a recipe — no one-shot button", async () => {
		const wrapper = await mountOpenDialog()
		await selectFirstRecipe(wrapper)

		expect(findButton(wrapper, "Start Production")).toBeDefined()
		expect(findButton(wrapper, "Process Production")).toBeUndefined()
	})

	it("hides Close before any output and shows it once something was produced", async () => {
		respond(ACTIVE_URL, (_params, opts) => opts.onSuccess(ACTIVE_ZERO))
		const zeroWrapper = await mountOpenDialog()
		await showActiveList(zeroWrapper)
		expect(findButton(zeroWrapper, "Close")).toBeUndefined()
		expect(zeroWrapper.text()).toContain("Ready to Produce")

		respond(ACTIVE_URL, (_params, opts) => opts.onSuccess(ACTIVE))
		const wrapper = await mountOpenDialog()
		await showActiveList(wrapper)
		expect(findButton(wrapper, "Close")).toBeDefined()
	})

	it("cancel confirmation mentions reversed units only when output exists", async () => {
		respond(ACTIVE_URL, (_params, opts) => opts.onSuccess(ACTIVE_ZERO))
		const zeroWrapper = await mountOpenDialog()
		await showActiveList(zeroWrapper)
		await zeroWrapper
			.findAll("button")
			.find((b) => b.text() === "Cancel")
			.trigger("click")
		expect(zeroWrapper.text()).toContain(
			"Cancel production WO-0001? All stock movements will be reversed.",
		)

		respond(ACTIVE_URL, (_params, opts) => opts.onSuccess(ACTIVE))
		const wrapper = await mountOpenDialog()
		await showActiveList(wrapper)
		await wrapper
			.findAll("button")
			.find((b) => b.text() === "Cancel")
			.trigger("click")
		expect(wrapper.text()).toContain(
			"Cancel production WO-0001? All 2 produced units will be reversed.",
		)
	})

	it("starts a two-phase production and lands on the In Production list", async () => {
		const startCall = mockCall(START_URL, () =>
			Promise.resolve({
				work_order: "WO-0002",
				production_item: "ICED-LATTE",
				recipe: "RECIPE-0001",
				qty: 2,
				produced_qty: 0,
				pos_status: "Not Started",
			}),
		)
		respond(ACTIVE_URL, (_params, opts) => opts.onSuccess(ACTIVE))

		const wrapper = await mountOpenDialog()
		await selectFirstRecipe(wrapper)
		await findButton(wrapper, "Start Production").trigger("click")
		await flushPromises()

		expect(startCall).toHaveBeenCalledWith({
			recipe: "RECIPE-0001",
			qty: 1,
			pos_profile: "POS-1",
		})
		// list view shows the running Work Order with planned/produced/elapsed
		expect(wrapper.text()).toContain("In Progress")
		expect(wrapper.text()).toContain("Planned")
		expect(wrapper.text()).toContain("10")
		expect(wrapper.text()).toContain("Produced")
		expect(wrapper.text()).toMatch(/2h \d+m/) // started 2h ago
		// starting only creates the plan — the catalog-refresh event must not fire
		expect(wrapper.emitted("production-created")).toBeUndefined()
	})

	it("finish form prefills remaining qty and has no loss input", async () => {
		respond(ACTIVE_URL, (_params, opts) => opts.onSuccess(ACTIVE))
		mockCall(CONTEXT_URL, () => ({ materials: [] }))
		const wrapper = await mountOpenDialog()
		await showActiveList(wrapper)

		await findButton(wrapper, "Finish").trigger("click")
		await flushPromises()

		// good prefill = planned - produced = 10 - 2 = 8; only good + notes
		const textInputs = wrapper.findAll('input[type="text"]')
		expect(textInputs).toHaveLength(2)
		expect(wrapper.text()).not.toContain("Loss Qty")
		const goodInput = textInputs[0]
		expect(goodInput.element.value).toBe("8")
		expect(wrapper.text()).not.toContain("recorded as loss")

		await goodInput.setValue("6")
		expect(wrapper.text()).toContain("2 short of plan, recorded as loss")
	})

	it("submits good, loss and notes to finish_production, then reloads the list", async () => {
		respond(ACTIVE_URL, (_params, opts) => opts.onSuccess(ACTIVE))
		// empty materials = no batch pickers; Auto sends no batch_picks at all
		mockCall(CONTEXT_URL, () => ({ materials: [] }))
		const finishCall = mockCall(FINISH_URL, () =>
			Promise.resolve({ good_qty: 7 }),
		)

		const wrapper = await mountOpenDialog()
		await showActiveList(wrapper)
		await findButton(wrapper, "Finish").trigger("click")
		await flushPromises()

		const textInputs = wrapper.findAll('input[type="text"]')
		await textInputs[0].setValue("7")
		const notes = wrapper.findAll('input[type="text"]')[1]
		await notes.setValue("menit ke-12")

		await findButton(wrapper, "Finish").trigger("click") // form's Finish button
		await flushPromises()

		expect(finishCall).toHaveBeenCalledWith({
			work_order: "WO-0001",
			good_qty: 7,
			loss_qty: 1, // remaining 8 - good 7
			notes: "menit ke-12",
		})
		// stock just moved — the parent refreshes its item catalog
		expect(wrapper.emitted("production-created")).toHaveLength(1)
	})

	it("shows batch pickers for batched materials; Auto sends no batch_picks", async () => {
		respond(ACTIVE_URL, (_params, opts) => opts.onSuccess(ACTIVE))
		mockCall(CONTEXT_URL, () => FINISH_CTX)
		const finishCall = mockCall(FINISH_URL, () =>
			Promise.resolve({ good_qty: 7 }),
		)

		const wrapper = await mountOpenDialog()
		await showActiveList(wrapper)
		await findButton(wrapper, "Finish").trigger("click")
		await flushPromises()

		// one picker, for the batched material only, defaulting to Auto
		const selects = wrapper.findAll("select")
		expect(selects).toHaveLength(1)
		expect(selects[0].element.value).toBe("")
		expect(wrapper.text()).toContain("Syrup")

		const textInputs = wrapper.findAll('input[type="text"]')
		await textInputs[0].setValue("7")
		await findButton(wrapper, "Finish").trigger("click")
		await flushPromises()

		const payload = finishCall.mock.calls[0][0]
		expect(payload.batch_picks).toBeUndefined()
		expect(wrapper.emitted("production-created")).toHaveLength(1)
	})

	it("sends batch_picks when the cashier picks a batch explicitly", async () => {
		respond(ACTIVE_URL, (_params, opts) => opts.onSuccess(ACTIVE))
		mockCall(CONTEXT_URL, () => FINISH_CTX)
		const finishCall = mockCall(FINISH_URL, () =>
			Promise.resolve({ good_qty: 7 }),
		)

		const wrapper = await mountOpenDialog()
		await showActiveList(wrapper)
		await findButton(wrapper, "Finish").trigger("click")
		await flushPromises()

		await wrapper.findAll("select")[0].setValue("B-NEW")

		const textInputs = wrapper.findAll('input[type="text"]')
		await textInputs[0].setValue("7")
		await findButton(wrapper, "Finish").trigger("click")
		await flushPromises()

		expect(finishCall).toHaveBeenCalledWith({
			work_order: "WO-0001",
			good_qty: 7,
			loss_qty: 1, // remaining 8 - good 7
			batch_picks: { SYRUP: "B-NEW" },
		})
	})

	it("falls back to Auto with a note when the batch list fails to load", async () => {
		respond(ACTIVE_URL, (_params, opts) => opts.onSuccess(ACTIVE))
		mockCall(CONTEXT_URL, () => Promise.reject(new Error("boom")))
		const finishCall = mockCall(FINISH_URL, () =>
			Promise.resolve({ good_qty: 7 }),
		)

		const wrapper = await mountOpenDialog()
		await showActiveList(wrapper)
		await findButton(wrapper, "Finish").trigger("click")
		await flushPromises()

		// finishing must never depend on the context call
		expect(wrapper.text()).toContain("Batch list unavailable")
		const textInputs = wrapper.findAll('input[type="text"]')
		await textInputs[0].setValue("7")
		await findButton(wrapper, "Finish").trigger("click")
		await flushPromises()

		const payload = finishCall.mock.calls[0][0]
		expect(payload.batch_picks).toBeUndefined()
		expect(wrapper.emitted("production-created")).toHaveLength(1)
	})

	it("closes a production only after confirmation", async () => {
		respond(ACTIVE_URL, (_params, opts) => opts.onSuccess(ACTIVE))
		const closeCall = mockCall(CLOSE_URL, () =>
			Promise.resolve({ status: "Closed" }),
		)

		const wrapper = await mountOpenDialog()
		await showActiveList(wrapper)

		await findButton(wrapper, "Close").trigger("click")
		expect(closeCall).not.toHaveBeenCalled()

		// confirm inside the overlay (the row button underneath stays in the DOM)
		const overlay = wrapper.find("div.fixed")
		await overlay
			.findAll("button")
			.find((b) => b.text() === "Close")
			.trigger("click")
		await flushPromises()

		expect(closeCall).toHaveBeenCalledWith({ work_order: "WO-0001" })
		// produced 2 units — closing keeps them, so the catalog refresh fires
		expect(wrapper.emitted("production-created")).toHaveLength(1)
	})

	it("cancels a production only after confirmation", async () => {
		respond(ACTIVE_URL, (_params, opts) => opts.onSuccess(ACTIVE))
		const cancelCall = mockCall(CANCEL_URL, () =>
			Promise.resolve({ status: "Cancelled" }),
		)

		const wrapper = await mountOpenDialog()
		await showActiveList(wrapper)

		const row = wrapper.findAll("button").find((b) => b.text() === "Cancel")
		await row.trigger("click")
		expect(cancelCall).not.toHaveBeenCalled()

		const overlay = wrapper.find("div.fixed")
		await overlay
			.findAll("button")
			.find((b) => b.text() === "Cancel Production")
			.trigger("click")
		await flushPromises()

		expect(cancelCall).toHaveBeenCalledWith({ work_order: "WO-0001" })
		// cancelling reverses the produced units — refresh fires too
		expect(wrapper.emitted("production-created")).toHaveLength(1)
	})

	it("shows the empty state when no productions are running", async () => {
		respond(ACTIVE_URL, (_params, opts) =>
			opts.onSuccess({ pos_profile: "POS-1", company: "Co", productions: [] }),
		)
		const wrapper = await mountOpenDialog()
		await showActiveList(wrapper)
		expect(wrapper.text()).toContain("No active productions")
	})

	it("lists finished productions in the History tab without Load More when done", async () => {
		const historyCall = mockCall(HISTORY_URL, () => Promise.resolve(HISTORY))

		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "History").trigger("click")
		await flushPromises()

		expect(historyCall).toHaveBeenCalledWith({
			pos_profile: "POS-1",
			limit: 30,
			offset: 0,
			from_date: expect.any(String), // default period: last 7 days
			to_date: expect.any(String),
		})
		expect(wrapper.text()).toContain("Iced Latte")
		expect(wrapper.text()).toContain("Completed")
		expect(wrapper.text()).toContain("Planned")
		expect(wrapper.text()).toContain("2026-09-26 08:30") // calm locale-free stamp
		expect(wrapper.text()).not.toContain("Load More")
	})

	it("shows loss in history rows only when greater than zero", async () => {
		mockCall(HISTORY_URL, () =>
			Promise.resolve({
				...HISTORY,
				productions: [
					{
						...HISTORY.productions[0],
						produced_qty: 3,
						process_loss_qty: 2,
						pos_status: "Partially Completed",
					},
				],
			}),
		)
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "History").trigger("click")
		await flushPromises()

		expect(wrapper.text()).toContain("Partially Completed")
		expect(wrapper.text()).toContain("Loss")
		expect(wrapper.text()).toContain("2")
	})

	it("appends the next page when Load More is clicked", async () => {
		const historyCall = mockCall(HISTORY_URL, (params) =>
			Promise.resolve(
				params.offset === 0
					? { ...HISTORY, has_more: true }
					: {
							...HISTORY,
							productions: [
								{
									...HISTORY.productions[0],
									work_order: "WO-0008",
									production_item_name: "Old Bread",
								},
							],
							has_more: false,
						},
			),
		)

		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "History").trigger("click")
		await flushPromises()
		expect(wrapper.text()).toContain("Load More")

		await findButton(wrapper, "Load More").trigger("click")
		await flushPromises()

		expect(historyCall).toHaveBeenLastCalledWith(
			expect.objectContaining({
				pos_profile: "POS-1",
				limit: 30,
				offset: 1, // offset = rows already shown (page 1 returned 1 row)
			}),
		)
		// page 1 stays, page 2 is appended
		expect(wrapper.text()).toContain("Iced Latte")
		expect(wrapper.text()).toContain("Old Bread")
		expect(wrapper.text()).not.toContain("Load More")
	})

	it("shows the empty state for a fresh outlet's history", async () => {
		mockCall(HISTORY_URL, () =>
			Promise.resolve({
				pos_profile: "POS-1",
				company: "Co",
				productions: [],
				has_more: false,
			}),
		)
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "History").trigger("click")
		await flushPromises()
		expect(wrapper.text()).toContain("No production history yet")
	})

	it("Overview loads the production dashboard for the last 7 days", async () => {
		const dashCall = mockCall("pos_next.api.production.get_production_dashboard", () =>
			Promise.resolve({
				produced: 40, loss: 2, loss_pct: 4.8, runs: 4, active: 1,
				top_items: [{ item_code: "ICED-LATTE", item_name: "Iced Latte", produced: 40, loss: 2, runs: 4 }],
				daily: [{ day: "2026-10-08", produced: 40, loss: 2 }],
			}),
		)
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "Overview").trigger("click")
		await flushPromises()
		expect(dashCall).toHaveBeenCalledWith(expect.objectContaining({ pos_profile: "POS-1" }))
		expect(wrapper.text()).toContain("4.8%")
		expect(wrapper.text()).toContain("Most produced")
	})

	// Reject badge: the draft name rides on the row; the label maps status.
	// Only Draft offers "Submit report" — later states are already in the HO lane.
	it.each([
		["Draft", "Not submitted", "Submit report"],
		["Pending Approval", "Waiting for HO", null],
		["Awaiting Destruction", "Awaiting Destruction", null],
		["Completed", "Completed", null],
	])("history badge for %s shows %s", async (status, label, action) => {
		mockCall(HISTORY_URL, () =>
			Promise.resolve({
				...HISTORY,
				productions: [{ ...HISTORY.productions[0], reject_request: "PRJ-0001", reject_status: status }],
			}),
		)
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "History").trigger("click")
		await flushPromises()

		expect(wrapper.text()).toContain(label)
		const btn = findButton(wrapper, action || "____nope____")
		expect(btn?.exists() ?? false).toBe(!!action)
	})

	it("emits open-reject-request with the request name from the Submit report button", async () => {
		mockCall(HISTORY_URL, () =>
			Promise.resolve({
				...HISTORY,
				productions: [{ ...HISTORY.productions[0], reject_request: "PRJ-0001", reject_status: "Draft" }],
			}),
		)
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "History").trigger("click")
		await flushPromises()

		await findButton(wrapper, "Submit report").trigger("click")
		expect(wrapper.emitted("open-reject-request")).toEqual([["PRJ-0001"]])
	})

	it("no badge when the history row has no reject_request", async () => {
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "History").trigger("click")
		await flushPromises()

		expect(wrapper.text()).not.toContain("Not submitted")
		expect(wrapper.text()).not.toContain("Submit report")
	})

	it("reloads the history list for the chosen period", async () => {
		const historyCall = mockCall(HISTORY_URL, () => Promise.resolve(HISTORY))
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "History").trigger("click")
		await flushPromises()

		await wrapper.find("select").setValue("today")
		await flushPromises()

		const last = historyCall.mock.calls.at(-1)[0]
		expect(historyCall).toHaveBeenCalledTimes(2)
		expect(last.offset).toBe(0)
		expect(last.from_date).toBe(last.to_date) // today = one day
	})

	it("opens the detail page of a history row and goes back to the list", async () => {
		mockCall(HISTORY_URL, () => Promise.resolve(HISTORY))
		const detailCall = mockCall("pos_next.api.production.get_production_detail", () =>
			Promise.resolve({
				...HISTORY.productions[0],
				stock_uom: "Cup",
				materials: [
					{ item_code: "MILK", item_name: "Milk", stock_uom: "Litre", batch_no: "", written_off: false, qty: 6 },
					{ item_code: "SYRUP", item_name: "Syrup", stock_uom: "Litre", batch_no: "B-OLD", written_off: true, qty: 1 },
				],
				stock_entries: [
					{ name: "STE-0001", purpose: "Manufacture", cancelled: false, posted_at: "2026-09-26 09:00", remarks: "" },
					{ name: "STE-0002", purpose: "Manufacture", cancelled: true, posted_at: "2026-09-26 09:30", remarks: "" },
				],
				notes: ["<p>adonan pagi</p>"],
			}),
		)
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "History").trigger("click")
		await flushPromises()

		await findButton(wrapper, HISTORY.productions[0].work_order).trigger("click")
		await flushPromises()

		expect(detailCall).toHaveBeenCalledWith({ work_order: HISTORY.productions[0].work_order })
		const text = wrapper.text()
		expect(text).toContain("Materials used")
		expect(text).toContain("Milk")
		expect(text).toContain("6 Litre")
		expect(text).toContain("Written off")
		expect(text).toContain("STE-0002")
		expect(text).toContain("adonan pagi")
		expect(text).not.toContain("<p>")

		await wrapper.find('button[aria-label="Back"]').trigger("click")
		await flushPromises()
		expect(wrapper.text()).not.toContain("Materials used")
		expect(wrapper.text()).toContain("Iced Latte")
	})

	it("shows a retry when the history detail fails to load", async () => {
		mockCall(HISTORY_URL, () => Promise.resolve(HISTORY))
		let fail = true
		mockCall("pos_next.api.production.get_production_detail", () =>
			fail
				? Promise.reject(new Error("boom"))
				: Promise.resolve({ ...HISTORY.productions[0], materials: [], stock_entries: [], notes: [] }),
		)
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "History").trigger("click")
		await flushPromises()
		await findButton(wrapper, HISTORY.productions[0].work_order).trigger("click")
		await flushPromises()
		expect(wrapper.text()).toContain("boom")

		fail = false
		await findButton(wrapper, "Retry").trigger("click")
		await flushPromises()
		expect(wrapper.text()).toContain("No materials were consumed.")
	})
})
