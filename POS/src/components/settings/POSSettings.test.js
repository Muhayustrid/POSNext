/**
 * @vitest-environment jsdom
 */
import { mount } from "@vue/test-utils";
import { call } from "frappe-ui";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { nextTick, ref } from "vue";

vi.mock("frappe-ui", () => ({
	call: vi.fn().mockResolvedValue({ message: [] }),
	createResource: vi.fn((opts = {}) => ({
		data: null,
		loading: false,
		error: null,
		fetch: vi.fn().mockImplementation(() => {
			opts.onSuccess?.({ print_driver: "imin", imin_eod_print_mode: "bitmap" });
			return Promise.resolve({ print_driver: "imin", imin_eod_print_mode: "bitmap" });
		}),
		reload: vi.fn().mockImplementation(() => {
			opts.onSuccess?.({ print_driver: "imin", imin_eod_print_mode: "bitmap" });
			return Promise.resolve({ print_driver: "imin", imin_eod_print_mode: "bitmap" });
		}),
		submit: vi.fn().mockResolvedValue({}),
	})),
	Button: {
		name: "Button",
		template: `<button v-bind="$attrs" @click="$emit('click')"><slot /></button>`,
	},
	FeatherIcon: {
		name: "FeatherIcon",
		template: "<i />",
	},
}));

vi.mock("@/utils/offline/workerClient", () => ({
	offlineWorker: {
		getStockSyncStatus: vi.fn().mockResolvedValue({
			enabled: false,
			warehouse: null,
			itemCount: 0,
			intervalMs: 60000,
			lastSync: null,
			running: false,
		}),
		configureStockSync: vi.fn().mockResolvedValue(true),
		startStockSync: vi.fn().mockResolvedValue(true),
		stopStockSync: vi.fn().mockResolvedValue(true),
	},
}));

vi.mock("@/composables/usePOSEvents", () => ({
	usePOSEvents: () => ({
		detectSettingsChanges: vi.fn(),
		updateSettingsSnapshot: vi.fn(),
		emitStockSyncConfigured: vi.fn(),
	}),
}));

vi.mock("@/composables/useToast", () => ({
	useToast: () => ({
		showSuccess: vi.fn(),
		showError: vi.fn(),
	}),
}));

vi.mock("@/composables/useQzTray", () => ({
	useQzTray: () => ({
		qzConnected: ref(false),
		qzConnecting: ref(false),
		qzCertStatus: ref(null),
		printers: ref([]),
		selectedPrinter: ref(""),
		loadingPrinters: ref(false),
		printerOptions: ref([]),
		certLoading: ref(false),
		certReady: ref(false),
		handleConnect: vi.fn(),
		refreshPrinters: vi.fn(),
		generateCertificate: vi.fn(),
		downloadCertificate: vi.fn(),
	}),
}));

vi.mock("@/utils/print/transport", () => ({
	ensureIminSdk: vi.fn().mockResolvedValue(true),
	getTransport: vi.fn(() => ({
		probe: vi.fn().mockResolvedValue({ connected: true }),
		getStatus: vi.fn().mockReturnValue({ ready: true }),
	})),
	initTransportFromServer: vi.fn().mockResolvedValue(true),
}));

vi.hoisted(() => {
	globalThis.__ = (message, replacements = []) => {
		if (!Array.isArray(replacements) || !replacements.length) return message;
		let out = message;
		for (const [i, v] of replacements.entries()) {
			out = out.split(`{${i}}`).join(String(v));
		}
		return out;
	};
});

import POSSettings from "./POSSettings.vue";

describe("POSSettings iMin printing relayout and EOD print mode", () => {
	let pinia;

	beforeEach(() => {
		pinia = createPinia();
		setActivePinia(pinia);
		vi.clearAllMocks();
	});

	function mountSettings(props = {}) {
		return mount(POSSettings, {
			props: {
				modelValue: true,
				embedded: true,
				posProfile: "TEST_PROFILE",
				...props,
			},
			global: {
				plugins: [pinia],
				stubs: {
					"router-link": true,
				},
				config: {
					globalProperties: {
						__: globalThis.__,
					},
				},
			},
		});
	}

	it("renders iMin receipt and EOD sections with multi-column responsive grid", async () => {
		const wrapper = mountSettings();
		await nextTick();
		await nextTick();

		// Switch to Printing tab
		const printingTabBtn = wrapper
			.findAll("button")
			.find((b) => b.text().includes("Printing"));
		expect(printingTabBtn).toBeDefined();
		await printingTabBtn.trigger("click");
		await nextTick();

		// Check subheadings in Receipt and EOD sections
		const subheadings = wrapper
			.findAll("h5")
			.map((h) => h.text().trim());
		expect(subheadings).toContain("Kertas & Mode");
		expect(subheadings).toContain("Rangkap & Jeda");
		expect(subheadings).toContain("Ukuran Teks");
		expect(subheadings).toContain("Margin & Kertas");
		expect(subheadings).toContain("Mode & Rangkap");

		// Check responsive grid containers
		const grids = wrapper.findAll(".grid.grid-cols-1.md\\:grid-cols-2.xl\\:grid-cols-3");
		expect(grids.length).toBeGreaterThanOrEqual(7);

		// Check EOD print mode select exists with text and bitmap options
		const labels = wrapper.findAll("label").map((l) => l.text().trim());
		expect(labels).toContain("Mode Cetak EOD");

		// Check CheckboxField has full col-span across breakpoints
		const fullWidthCheckboxes = wrapper.findAll(".col-span-1.md\\:col-span-2.xl\\:col-span-3");
		expect(fullWidthCheckboxes.length).toBeGreaterThanOrEqual(2);

		wrapper.unmount();
	});

	it("defaults imin_eod_print_mode to bitmap and updates when selection changes", async () => {
		const wrapper = mountSettings();
		await nextTick();
		await nextTick();

		// Switch to Printing tab
		const printingTabBtn = wrapper
			.findAll("button")
			.find((b) => b.text().includes("Printing"));
		await printingTabBtn.trigger("click");
		await nextTick();

		// Find Mode Cetak EOD select field
		const eodSelectLabel = wrapper
			.findAll("label")
			.find((l) => l.text().trim() === "Mode Cetak EOD");
		expect(eodSelectLabel).toBeDefined();

		const eodSelectContainer = eodSelectLabel.element.closest("div");
		const select = eodSelectContainer.querySelector("select");
		expect(select.value).toBe("bitmap");

		// Change select to text
		select.value = "text";
		select.dispatchEvent(new Event("change"));
		await nextTick();

		expect(select.value).toBe("text");

		wrapper.unmount();
	});

	it("persists imin_eod_print_mode when saving settings", async () => {
		const wrapper = mountSettings();
		await nextTick();
		await nextTick();

		// Switch to Printing tab
		const printingTabBtn = wrapper
			.findAll("button")
			.find((b) => b.text().includes("Printing"));
		await printingTabBtn.trigger("click");
		await nextTick();

		// Select text mode for EOD
		const eodSelectLabel = wrapper
			.findAll("label")
			.find((l) => l.text().trim() === "Mode Cetak EOD");
		const select = eodSelectLabel.element.closest("div").querySelector("select");
		select.value = "text";
		select.dispatchEvent(new Event("change"));
		await nextTick();

		// Click Save Changes button
		const saveBtn = wrapper
			.findAll("button")
			.find((b) => b.text().includes("Save Changes"));
		expect(saveBtn).toBeDefined();
		await saveBtn.trigger("click");
		await nextTick();

		expect(call).toHaveBeenCalledWith(
			"pos_next.pos_next.doctype.pos_settings.pos_settings.update_pos_settings",
			expect.objectContaining({
				pos_profile: "TEST_PROFILE",
				settings: expect.objectContaining({
					imin_eod_print_mode: "text",
				}),
			})
		);

		wrapper.unmount();
	});
});
