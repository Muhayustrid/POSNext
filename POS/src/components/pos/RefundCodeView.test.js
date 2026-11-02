import { flushPromises, mount } from "@vue/test-utils"
import { beforeEach, describe, expect, it, vi } from "vitest"

const callMock = vi.fn()
vi.mock("@/utils/apiWrapper", () => ({ call: (...a) => callMock(...a) }))
vi.mock("@/composables/useToast", () => ({
	useToast: () => ({ showError: vi.fn(), showSuccess: vi.fn() }),
}))
vi.mock("frappe-ui", () => ({
	Button: { template: "<button @click=\"$emit('click')\"><slot /></button>" },
	FeatherIcon: { template: "<i />" },
}))

import RefundCodeView from "./RefundCodeView.vue"

const tr = (s, args) => (args ? s.replace("{0}", args[0]) : s)
const opts = (company) => ({
	props: { company },
	global: { mocks: { __: tr } },
})

const HISTORY =
	"pos_next.pos_next.doctype.pos_refund_code.pos_refund_code.get_my_daily_codes"
const GENERATE =
	"pos_next.pos_next.doctype.pos_refund_code.pos_refund_code.generate_daily_code"

describe("RefundCodeView history", () => {
	beforeEach(() => {
		callMock.mockReset()
		globalThis.__ = tr
	})

	it("lists the user's codes for the company and reloads after generating", async () => {
		callMock.mockImplementation(async (method) =>
			method === HISTORY
				? [
						{
							code: "ABC123",
							status: "Active",
							creation: "2026-11-02 21:30:11.5",
							used_count: 2,
						},
					]
				: { code: "NEW999", company: "Best Test", valid_upto: "2026-11-02" },
		)
		const wrapper = mount(RefundCodeView, opts("Best Test"))
		await flushPromises()
		const history = wrapper.find('[data-test="refund-code-history"]')
		expect(history.text()).toContain("ABC123")
		expect(history.text()).toContain("2026-11-02 21:30 · Used 2x")
		expect(callMock).toHaveBeenCalledWith(HISTORY, { company: "Best Test" })

		await wrapper.find('[data-test="refund-code-generate"]').trigger("click")
		await flushPromises()
		expect(callMock).toHaveBeenCalledWith(GENERATE, { company: "Best Test" })
		expect(callMock.mock.calls.filter(([m]) => m === HISTORY)).toHaveLength(2)
	})

	it("reloads the list when the company changes", async () => {
		callMock.mockResolvedValue([])
		const wrapper = mount(RefundCodeView, opts("A"))
		await flushPromises()
		await wrapper.setProps({ company: "B" })
		await flushPromises()
		expect(callMock).toHaveBeenLastCalledWith(HISTORY, { company: "B" })
		expect(wrapper.text()).toContain("No codes generated yet")
	})
})
