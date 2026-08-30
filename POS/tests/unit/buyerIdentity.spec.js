import { mount } from "@vue/test-utils";
import { h, reactive } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The store modules are stubbed here rather than going through Pinia:
// usePOSCartStore's setup pulls in useInvoice and the offline worker client,
// which cannot load under jsdom. The component only reads the documented
// surface (settingsStore flags, cartStore.buyerName, shiftStore.currentShift).
const settingsStore = reactive({ enableBuyerIdentity: false, requireBuyerName: false });
const cartStore = reactive({ buyerName: "" });
const shiftStore = reactive({ currentShift: null });

vi.mock("@/stores/posSettings", () => ({ usePOSSettingsStore: () => settingsStore }));
vi.mock("@/stores/posCart", () => ({ usePOSCartStore: () => cartStore }));
vi.mock("@/stores/posShift", () => ({ usePOSShiftStore: () => shiftStore }));

const { default: BuyerIdentityFields } = await import(
	"@/components/sale/BuyerIdentityFields.vue"
);
const { getNextQueueNumber, isBuyerIdentityEnabled, isBuyerNameRequiredButMissing } =
	await import("@/utils/buyerIdentity");

/**
 * Host replicating the exact parent gate used by InvoiceCart/PaymentDialog:
 * the buyer-identity UI renders only when `enableBuyerIdentity` is on.
 */
const Host = {
	setup() {
		return () =>
			settingsStore.enableBuyerIdentity
				? h(BuyerIdentityFields, {
						modelValue: cartStore.buyerName,
						"onUpdate:modelValue": (val) => {
							cartStore.buyerName = val;
						},
					})
				: null;
	},
};

function mountHost() {
	return mount(Host, {
		global: {
			// Templates resolve `__` through the Vue instance (globalProperties),
			// so the frappe i18n shim from tests/setup.js must be exposed here too.
			config: {
				globalProperties: { __: globalThis.__ },
			},
		},
	});
}

describe("buyer identity UI (OpenSpec task 2.9)", () => {
	beforeEach(() => {
		settingsStore.enableBuyerIdentity = false;
		settingsStore.requireBuyerName = false;
		cartStore.buyerName = "";
		shiftStore.currentShift = null;
	});

	it("disabled profile shows NO buyer-name input and NO queue chip (asserts absence)", async () => {
		const wrapper = mountHost();
		expect(wrapper.find('[data-test="buyer-name-input"]').exists()).toBe(false);
		expect(wrapper.find('[data-test="queue-chip"]').exists()).toBe(false);

		// Flip the same setting on: the gate is reactive, so this proves the
		// earlier absence was the disabled state, not a broken selector.
		settingsStore.enableBuyerIdentity = true;
		shiftStore.currentShift = { current_queue_number: 4 };
		await wrapper.vm.$nextTick();
		expect(wrapper.find('[data-test="buyer-name-input"]').exists()).toBe(true);
		expect(wrapper.find('[data-test="queue-chip"]').exists()).toBe(true);
	});

	it("enabled profile renders a free-text 60-char input and the next-queue chip", async () => {
		settingsStore.enableBuyerIdentity = true;
		shiftStore.currentShift = { current_queue_number: 4 };
		const wrapper = mountHost();

		const input = wrapper.find('[data-test="buyer-name-input"]');
		expect(input.exists()).toBe(true);
		expect(input.attributes("maxlength")).toBe("60");
		expect(input.attributes("type")).toBe("text");

		const chip = wrapper.find('[data-test="queue-chip"]');
		// Chip shows the NEXT number for the open shift: current_queue_number + 1
		expect(chip.text()).toContain("5");
	});

	it("typing in the field updates the shared cart state", async () => {
		settingsStore.enableBuyerIdentity = true;
		const wrapper = mountHost();
		await wrapper.find('[data-test="buyer-name-input"]').setValue("Budi");
		expect(cartStore.buyerName).toBe("Budi");
	});

	it("require_buyer_name: missing name blocks the field (red state + hint)", () => {
		settingsStore.enableBuyerIdentity = true;
		settingsStore.requireBuyerName = true;
		const wrapper = mountHost();
		expect(wrapper.find('[data-test="buyer-name-required-hint"]').exists()).toBe(true);
		expect(wrapper.find('[data-test="buyer-name-input"]').attributes("aria-required")).toBe(
			"true"
		);
	});

	it("require_buyer_name blocks submit while the name is empty or whitespace-only", () => {
		// This is the exact predicate PaymentDialog's canComplete consumes.
		expect(
			isBuyerNameRequiredButMissing({
				enableBuyerIdentity: true,
				requireBuyerName: true,
				buyerName: "",
			})
		).toBe(true);
		expect(
			isBuyerNameRequiredButMissing({
				enableBuyerIdentity: true,
				requireBuyerName: true,
				buyerName: "   ",
			})
		).toBe(true);
	});

	it("require_buyer_name unblocks once a name is present or the feature is off", () => {
		expect(
			isBuyerNameRequiredButMissing({
				enableBuyerIdentity: true,
				requireBuyerName: true,
				buyerName: "Budi",
			})
		).toBe(false);
		expect(
			isBuyerNameRequiredButMissing({
				enableBuyerIdentity: true,
				requireBuyerName: false,
				buyerName: "",
			})
		).toBe(false);
		expect(
			isBuyerNameRequiredButMissing({
				enableBuyerIdentity: false,
				requireBuyerName: true,
				buyerName: "",
			})
		).toBe(false);
	});

	it("queue chip number derives from the shift counter; null without a shift", () => {
		expect(getNextQueueNumber({ current_queue_number: 0 })).toBe(1);
		expect(getNextQueueNumber({ current_queue_number: 17 })).toBe(18);
		expect(getNextQueueNumber(null)).toBeNull();
		expect(getNextQueueNumber({})).toBeNull();
	});

	it("isBuyerIdentityEnabled mirrors the setting truthiness", () => {
		expect(isBuyerIdentityEnabled(1)).toBe(true);
		expect(isBuyerIdentityEnabled(0)).toBe(false);
		expect(isBuyerIdentityEnabled(undefined)).toBe(false);
	});
});
