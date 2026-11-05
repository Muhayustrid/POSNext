import { call } from "@/utils/apiWrapper"
import { defineStore } from "pinia"
import { ref } from "vue"

export const useOutletMonitoringStore = defineStore("outletMonitoring", () => {
	const enabled = ref(false)
	const config = ref({})
	const tasks = ref([])
	const count = ref(0)
	const loading = ref(false)
	const activeProfile = ref(null)

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

	return {
		enabled,
		config,
		tasks,
		count,
		loading,
		activeProfile,
		refresh,
		bindRealtime,
		unbindRealtime,
	}
})
