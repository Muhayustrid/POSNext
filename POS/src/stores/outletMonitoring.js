import { call } from "@/utils/apiWrapper"
import { parseServerDatetime } from "@/utils/shiftSchedule"
import { defineStore } from "pinia"
import { ref } from "vue"

export const useOutletMonitoringStore = defineStore("outletMonitoring", () => {
	const enabled = ref(false)
	const config = ref({})
	const tasks = ref([])
	const count = ref(0)
	const loading = ref(false)
	const activeProfile = ref(null)
	const manualTemplates = ref([])
	const referenceCandidates = ref([])
	const lastStartedReport = ref(null)
	// Server clock anchor (ms, site-local) at the moment get_my_tasks landed;
	// opens_at is compared against this, never against the device clock.
	const serverNowMs = ref(0)
	const receivedAtMs = ref(0)

	let debounceTimer = null

	async function refresh(posProfile) {
		const profile = posProfile || activeProfile.value
		if (posProfile) {
			activeProfile.value = posProfile
		}
		loading.value = true
		try {
			const res = await call("pos_next.api.outlet_monitoring.get_my_tasks", {
				pos_profile: profile,
			})
			if (res) {
				enabled.value = Boolean(res.enabled)
				config.value = res.config || {}
				tasks.value = res.tasks || []
				// count equals obligations/tasks count, not sum of checkpoints
				count.value =
					typeof res.count === "number" ? res.count : tasks.value.length
				// A bad/absent server_now must not wipe a still-usable anchor.
				// The pair is only moved together: bumping receivedAtMs alone
				// would rewind the derived server time.
				const anchor = parseServerDatetime(res.server_now)
				if (anchor != null) {
					serverNowMs.value = anchor
					receivedAtMs.value = Date.now()
				}
			}
		} catch {
			// Suppress refresh errors while offline
		} finally {
			loading.value = false
		}
	}

	function onRealtimeUpdate() {
		if (debounceTimer) {
			clearTimeout(debounceTimer)
		}
		debounceTimer = setTimeout(() => {
			refresh()
		}, 1000)
	}

	function bindRealtime() {
		if (typeof window !== "undefined" && window.frappe?.realtime) {
			window.frappe.realtime.on("outlet_monitoring_updated", onRealtimeUpdate)
		}
	}

	function unbindRealtime() {
		if (debounceTimer) {
			clearTimeout(debounceTimer)
			debounceTimer = null
		}
		if (typeof window !== "undefined" && window.frappe?.realtime) {
			window.frappe.realtime.off("outlet_monitoring_updated", onRealtimeUpdate)
		}
	}

	async function loadManualTemplates(posProfile) {
		const profile = posProfile || activeProfile.value
		// Both manual endpoints return plain arrays (API contract, §4.8).
		const res = await call(
			"pos_next.api.outlet_monitoring.get_manual_templates",
			{ pos_profile: profile },
		)
		manualTemplates.value = res || []
		return manualTemplates.value
	}

	async function loadReferenceCandidates(posProfile, template, txt) {
		const profile = posProfile || activeProfile.value
		const res = await call(
			"pos_next.api.outlet_monitoring.get_reference_candidates",
			{ pos_profile: profile, template, txt },
		)
		referenceCandidates.value = res || []
		return referenceCandidates.value
	}

	async function startManualReport(
		posProfile,
		template,
		referenceDoctype,
		referenceName,
	) {
		const profile = posProfile || activeProfile.value
		const res = await call(
			"pos_next.api.outlet_monitoring.start_manual_report",
			{
				pos_profile: profile,
				template,
				reference_doctype: referenceDoctype,
				reference_name: referenceName,
			},
		)
		lastStartedReport.value = res || null
		return res
	}

	return {
		enabled,
		config,
		tasks,
		count,
		loading,
		activeProfile,
		manualTemplates,
		referenceCandidates,
		lastStartedReport,
		serverNowMs,
		receivedAtMs,
		refresh,
		loadManualTemplates,
		loadReferenceCandidates,
		startManualReport,
		bindRealtime,
		unbindRealtime,
	}
})
