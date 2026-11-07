/**
 * @vitest-environment jsdom
 */
import { beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

const callMock = vi.hoisted(() => vi.fn(() => Promise.resolve(null)))

vi.mock("@/utils/apiWrapper", () => ({
	call: (...args) => callMock(...args),
}))

import { useOutletMonitoringStore } from "./outletMonitoring"

describe("useOutletMonitoringStore", () => {
	beforeEach(() => {
		setActivePinia(createPinia())
		vi.clearAllMocks()
	})

	it("store count equals number of tasks, not checkpoints", async () => {
		callMock.mockResolvedValueOnce({
			enabled: true,
			config: { show_menu_badge: 1 },
			count: 2,
			tasks: [
				{
					name: "REP-001",
					template: "Daily Opening",
					required_total: 5,
					required_done: 2,
				},
				{
					name: "REP-002",
					template: "Cleanliness Check",
					required_total: 4,
					required_done: 4,
				},
			],
		})

		const store = useOutletMonitoringStore()
		await store.refresh("POS Profile 1")

		expect(store.enabled).toBe(true)
		expect(store.tasks.length).toBe(2)
		// Count is 2 (tasks count), not 9 (sum of checkpoints)
		expect(store.count).toBe(2)
	})

	it("store disabled keeps menu hidden", async () => {
		callMock.mockResolvedValueOnce({
			enabled: false,
			config: {},
			count: 0,
			tasks: [],
		})

		const store = useOutletMonitoringStore()
		await store.refresh("POS Profile 1")

		expect(store.enabled).toBe(false)
		expect(store.count).toBe(0)
		expect(store.tasks).toEqual([])
	})

	it("Mulai Laporan: loads manual templates, candidates, then opens Draft report", async () => {
		const store = useOutletMonitoringStore()
		callMock.mockImplementation((method) => {
			if (method.endsWith("get_manual_templates")) {
				return Promise.resolve([
					{ name: "TPL-UNBOX", template_name: "Unboxing Barang" },
				])
			}
			if (method.endsWith("get_reference_candidates")) {
				return Promise.resolve([
					{
						doctype: "Purchase Receipt",
						name: "PR-001",
						posting_date: "2026-11-06",
						sender: "Supplier A",
						item_count: 3,
					},
				])
			}
			if (method.endsWith("start_manual_report")) {
				return Promise.resolve({
					name: "OM-MAN-001",
					template: "Unboxing Barang",
					status: "Draft",
					checkpoints: [],
				})
			}
			return Promise.resolve(null)
		})

		const templates = await store.loadManualTemplates("POS-1")
		expect(callMock).toHaveBeenCalledWith(
			"pos_next.api.outlet_monitoring.get_manual_templates",
			{ pos_profile: "POS-1" },
		)
		expect(templates).toHaveLength(1)
		expect(store.manualTemplates).toHaveLength(1)

		const candidates = await store.loadReferenceCandidates(
			"POS-1",
			"TPL-UNBOX",
			"PR-00",
		)
		expect(callMock).toHaveBeenCalledWith(
			"pos_next.api.outlet_monitoring.get_reference_candidates",
			{ pos_profile: "POS-1", template: "TPL-UNBOX", txt: "PR-00" },
		)
		expect(candidates).toHaveLength(1)
		expect(candidates[0]).not.toHaveProperty("grand_total")
		expect(store.referenceCandidates).toHaveLength(1)

		const report = await store.startManualReport(
			"POS-1",
			"TPL-UNBOX",
			"Purchase Receipt",
			"PR-001",
		)
		expect(callMock).toHaveBeenCalledWith(
			"pos_next.api.outlet_monitoring.start_manual_report",
			{
				pos_profile: "POS-1",
				template: "TPL-UNBOX",
				reference_doctype: "Purchase Receipt",
				reference_name: "PR-001",
			},
		)
		expect(report.name).toBe("OM-MAN-001")
		expect(store.lastStartedReport?.name).toBe("OM-MAN-001")
	})

	it("keeps the clock unanchored when server_now is missing", async () => {
		callMock.mockResolvedValueOnce({ enabled: true, count: 0, tasks: [] })

		const store = useOutletMonitoringStore()
		await store.refresh("POS Profile 1")

		expect(store.serverNowMs).toBe(0)
		expect(store.receivedAtMs).toBe(0)
	})

	it("keeps the clock unanchored when server_now is invalid", async () => {
		callMock.mockResolvedValueOnce({
			enabled: true,
			count: 0,
			tasks: [],
			server_now: "not-a-datetime",
		})

		const store = useOutletMonitoringStore()
		await store.refresh("POS Profile 1")

		expect(store.serverNowMs).toBe(0)
		expect(store.receivedAtMs).toBe(0)
	})

	it("a later bad response does not rewind a valid anchor", async () => {
		const store = useOutletMonitoringStore()
		callMock.mockResolvedValueOnce({
			enabled: true,
			count: 0,
			tasks: [],
			server_now: "2026-11-07 08:00:00",
		})
		await store.refresh("POS Profile 1")
		const anchoredServerNow = store.serverNowMs
		const anchoredReceivedAt = store.receivedAtMs
		expect(anchoredServerNow).toBeGreaterThan(0)

		callMock.mockResolvedValueOnce({ enabled: true, count: 0, tasks: [] })
		await store.refresh("POS Profile 1")

		expect(store.serverNowMs).toBe(anchoredServerNow)
		expect(store.receivedAtMs).toBe(anchoredReceivedAt)
	})

	it("consumes the live get_manual_templates / get_reference_candidates arrays", async () => {
		// Literal response bodies captured from the real endpoints on
		// fix.localhost by a rollback-only probe. Artifact:
		// .superpowers/sdd/2026-11-06-outlet-monitoring-labels/artifacts/
		// manual-endpoints-live.json (get_manual_templates,
		// get_reference_candidates, start_manual_report).
		const liveTemplates = [
			{
				name: "Tmpl Live Unboxing 31c5cd",
				template_name: "Tmpl Live Unboxing 31c5cd",
			},
		]
		const liveCandidates = [
			{
				doctype: "Purchase Receipt",
				name: "MAT-PRE-31c5cd",
				posting_date: "2026-11-07",
				sender: "PT Sumber Pangan",
				item_count: 4,
			},
			{
				doctype: "Delivery Note",
				name: "MAT-DN-31c5cd",
				posting_date: "2026-11-07",
				sender: "_Test Company 2",
				item_count: 2,
			},
		]
		const store = useOutletMonitoringStore()
		callMock.mockImplementation((method) => {
			if (method.endsWith("get_manual_templates")) {
				return Promise.resolve(liveTemplates)
			}
			if (method.endsWith("get_reference_candidates")) {
				return Promise.resolve(liveCandidates)
			}
			return Promise.resolve(null)
		})

		const templates = await store.loadManualTemplates("POS-1")
		expect(templates).toEqual(liveTemplates)
		expect(store.manualTemplates).toEqual(liveTemplates)

		const candidates = await store.loadReferenceCandidates(
			"POS-1",
			"Tmpl Live Unboxing 31c5cd",
			null,
		)
		expect(candidates).toEqual(liveCandidates)
		expect(store.referenceCandidates).toEqual(liveCandidates)
	})
})
