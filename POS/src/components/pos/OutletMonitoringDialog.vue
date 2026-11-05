<template>
	<DialogHost
		v-model:show="dialogShow"
		:embedded="embedded"
		:options="{ title: dialogTitle, size: '4xl' }"
	>
		<template #body-content>
			<!-- LIST VIEW -->
			<div v-if="currentView === 'list'" class="mx-auto flex w-full max-w-4xl flex-col gap-4">
				<!-- Tabs: Tugas Saya (tasks) & Riwayat (history) -->
				<div class="flex border-b border-gray-200">
					<button
						type="button"
						data-testid="tab-tasks"
						class="min-h-[44px] md:min-h-[40px] px-5 text-sm font-medium border-b-2 transition-colors touch-manipulation flex items-center gap-2"
						:class="
							currentTab === 'tasks'
								? 'border-blue-600 text-blue-600'
								: 'border-transparent text-gray-500 hover:text-gray-700'
						"
						@click="switchTab('tasks')"
					>
						<span>{{ __("My Tasks") }}</span>
						<span
							v-if="store.count > 0"
							class="min-w-5 h-5 px-1.5 flex items-center justify-center text-[10px] font-bold rounded-full bg-orange-600 text-white"
						>
							{{ store.count }}
						</span>
					</button>
					<button
						type="button"
						data-testid="tab-history"
						class="min-h-[44px] md:min-h-[40px] px-5 text-sm font-medium border-b-2 transition-colors touch-manipulation flex items-center gap-2"
						:class="
							currentTab === 'history'
								? 'border-blue-600 text-blue-600'
								: 'border-transparent text-gray-500 hover:text-gray-700'
						"
						@click="switchTab('history')"
					>
						<span>{{ __("History") }}</span>
					</button>
				</div>

				<!-- TASKS TAB -->
				<div v-if="currentTab === 'tasks'" class="flex flex-col gap-3">
					<div
						v-if="store.loading"
						class="py-12 text-center text-sm text-gray-500"
					>
						{{ __("Loading tasks...") }}
					</div>

					<div
						v-else-if="store.tasks.length === 0"
						class="py-12 text-center text-sm text-gray-500"
						data-testid="empty-tasks"
					>
						<FeatherIcon name="check-circle" class="mx-auto h-8 w-8 text-gray-400 mb-2" />
						<p>{{ __("No tasks") }}</p>
					</div>

					<div
						v-for="task in store.tasks"
						:key="task.name"
						class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 p-4 rounded-xl border border-gray-200 bg-white shadow-sm"
					>
						<div class="flex flex-col gap-1 min-w-0">
							<div class="flex items-center gap-2 flex-wrap">
								<span class="font-semibold text-gray-900 text-base">
									{{ task.template }}
								</span>
								<span
									v-if="task.late"
									class="inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold bg-red-100 text-red-800"
								>
									{{ __("Late") }}
								</span>
								<span class="text-xs text-gray-500 font-medium">
									({{ task.cadence }})
								</span>
							</div>

							<div class="text-xs text-gray-500 flex flex-wrap gap-x-4 gap-y-1">
								<span v-if="task.due_at">
									{{ __("Due: {0}", [task.due_at]) }}
								</span>
								<span>
									{{
										__("{0} of {1} checkpoints completed", [
											task.required_done,
											task.required_total,
										])
									}}
								</span>
							</div>
						</div>

						<button
							type="button"
							data-testid="fill-report-btn"
							class="min-h-[44px] md:min-h-[40px] px-4 py-2 rounded-lg bg-blue-600 text-white font-medium text-sm hover:bg-blue-700 active:scale-95 transition-all shrink-0 inline-flex items-center justify-center gap-1.5 touch-manipulation"
							@click="openReport(task.name)"
						>
							<FeatherIcon name="edit-3" class="w-4 h-4" />
							<span>{{ __("Fill Report") }}</span>
						</button>
					</div>
				</div>

				<!-- HISTORY TAB -->
				<div v-if="currentTab === 'history'" class="flex flex-col gap-3">
					<div
						v-if="historyLoading"
						class="py-12 text-center text-sm text-gray-500"
					>
						{{ __("Loading history...") }}
					</div>

					<div
						v-else-if="historyItems.length === 0"
						class="py-12 text-center text-sm text-gray-500"
						data-testid="empty-history"
					>
						<FeatherIcon name="clock" class="mx-auto h-8 w-8 text-gray-400 mb-2" />
						<p>{{ __("No history") }}</p>
					</div>

					<div
						v-for="item in historyItems"
						:key="item.name"
						class="flex flex-col gap-2 p-4 rounded-xl border border-gray-200 bg-white shadow-sm"
					>
						<div class="flex flex-wrap items-center justify-between gap-2">
							<div class="flex items-center gap-2">
								<span class="font-semibold text-gray-900 text-base">
									{{ item.template }}
								</span>
								<span
									v-if="item.revision > 1"
									class="text-xs text-gray-500 font-medium"
								>
									(Rev {{ item.revision }})
								</span>
							</div>
							<span
								:class="[
									STATUS_PILL_BASE,
									getHistoryTone(item),
								]"
							>
								{{ __(getHistoryLabel(item)) }}
							</span>
						</div>

						<div class="text-xs text-gray-500">
							{{ item.operational_date }}
						</div>

						<div
							v-if="item.review_note"
							class="p-2.5 rounded-lg bg-gray-50 border border-gray-200 text-xs text-gray-700 mt-1"
						>
							<span class="font-medium text-gray-900">{{ __("HQ Note:") }}</span>
							{{ item.review_note }}
						</div>

						<div class="flex items-center justify-end gap-2 mt-2 pt-2 border-t border-gray-100">
							<button
								v-if="item.correction_requested && item.status === 'Reviewed'"
								type="button"
								data-testid="create-revision-btn"
								class="min-h-[44px] md:min-h-[40px] px-3.5 py-1.5 rounded-lg bg-orange-600 text-white text-xs font-semibold hover:bg-orange-700 active:scale-95 transition-all inline-flex items-center gap-1.5 touch-manipulation"
								@click="createRevision(item.name)"
							>
								<FeatherIcon name="repeat" class="w-3.5 h-3.5" />
								<span>{{ __("Create Revision") }}</span>
							</button>

							<button
								type="button"
								data-testid="view-report-btn"
								class="min-h-[44px] md:min-h-[40px] px-3.5 py-1.5 rounded-lg border border-gray-200 text-gray-700 text-xs font-medium hover:bg-gray-50 active:bg-gray-100 transition-colors inline-flex items-center gap-1.5 touch-manipulation"
								@click="openReport(item.name)"
							>
								<FeatherIcon name="eye" class="w-3.5 h-3.5 text-gray-500" />
								<span>{{ __("View") }}</span>
							</button>
						</div>
					</div>
				</div>
			</div>

			<!-- DOC VIEW (REPORT DETAIL) -->
			<div v-else-if="currentView === 'doc'" class="mx-auto flex w-full max-w-4xl flex-col gap-5">
				<!-- Header info -->
				<div class="flex items-center justify-between border-b border-gray-200 pb-3">
					<div class="flex items-center gap-3">
						<button
							type="button"
							class="p-2 -ms-2 rounded-lg text-gray-600 hover:bg-gray-100 active:bg-gray-200 transition-colors touch-manipulation"
							:aria-label="__('Back')"
							@click="currentView = 'list'"
						>
							<FeatherIcon name="arrow-left" class="w-5 h-5" />
						</button>
						<div>
							<h2 class="text-lg font-bold text-gray-900 flex items-center gap-2">
								<span>{{ activeReport?.template }}</span>
								<span
									v-if="activeReport?.revision > 1"
									class="text-xs text-gray-500 font-normal"
								>
									(Rev {{ activeReport.revision }})
								</span>
							</h2>
							<p class="text-xs text-gray-500 mt-0.5">
								{{ activeReport?.company }} · {{ activeReport?.pos_profile }} · {{ activeReport?.operational_date }}
							</p>
						</div>
					</div>

					<div class="flex items-center gap-2">
						<span
							v-if="activeReport?.status"
							:class="[
								STATUS_PILL_BASE,
								getReportStatusTone(activeReport),
							]"
						>
							{{ __(activeReport.status) }}
						</span>
					</div>
				</div>

				<div v-if="docLoading" class="py-12 text-center text-sm text-gray-500">
					{{ __("Loading report...") }}
				</div>

				<div v-else class="flex flex-col gap-6">
					<!-- Checkpoint rows -->
					<div
						v-for="cp in activeReport?.checkpoints || []"
						:key="cp.name"
						class="flex flex-col gap-3 p-4 rounded-xl border border-gray-200 bg-white"
					>
						<div class="flex flex-wrap items-center justify-between gap-2">
							<div class="flex items-center gap-2">
								<span class="font-semibold text-gray-900 text-sm">
									{{ cp.checkpoint_name || cp.checkpoint }}
								</span>
								<span
									v-if="cp.required"
									class="text-[11px] font-semibold text-red-600"
								>
									*{{ __("Required") }}
								</span>
							</div>

							<div class="flex items-center gap-2 flex-wrap">
								<!-- Evidence complete badge -->
								<span
									v-if="isCheckpointComplete(cp)"
									data-testid="evidence-complete-badge"
									:class="[
										STATUS_PILL_BASE,
										STATUS_TONES.blue,
									]"
								>
									<FeatherIcon name="check" class="w-3.5 h-3.5 me-1" />
									<span>{{ __("Evidence complete") }}</span>
								</span>

								<!-- Checkpoint assessment badge after review -->
								<span
									v-if="activeReport?.status === 'Reviewed' && cp.assessment"
									data-testid="checkpoint-assessment-badge"
									:class="[
										STATUS_PILL_BASE,
										getCheckpointAssessmentTone(cp.assessment),
									]"
								>
									<span>{{ getCheckpointAssessmentLabel(cp) }}</span>
								</span>
							</div>
						</div>

						<p v-if="cp.instruction" class="text-xs text-gray-600">
							{{ cp.instruction }}
						</p>

						<!-- Reviewer note for checkpoint -->
						<div
							v-if="activeReport?.status === 'Reviewed' && cp.assessment_note"
							data-testid="checkpoint-assessment-note"
							class="text-xs rounded-lg p-2.5 border mt-1"
							:class="
								cp.assessment === 'Fail'
									? 'bg-red-50 text-red-700 border-red-200'
									: 'bg-gray-50 text-gray-700 border-gray-200'
							"
						>
							<span class="font-medium text-gray-900">{{ __("Notes") }}: </span>
							<span>{{ cp.assessment_note }}</span>
						</div>

						<!-- Rules & media count -->
						<div class="text-[11px] text-gray-500">
							{{ cp.media_type }} · {{ __("Min: {0}, Max: {1}", [cp.min_count || 1, cp.max_count || 1]) }}
						</div>

						<!-- Media attachments grid -->
						<div class="flex flex-wrap gap-3 mt-1">
							<div
								v-for="med in getCheckpointMedia(cp)"
								:key="med.name"
								class="relative w-24 h-24 rounded-lg overflow-hidden border border-gray-200 bg-gray-50 flex items-center justify-center group"
							>
								<button
									type="button"
									class="w-full h-full flex items-center justify-center p-0 m-0 border-0 bg-transparent cursor-pointer focus:outline-none focus:ring-2 focus:ring-inset focus:ring-primary-500"
									:aria-label="med.media_type === 'Video' ? __('Preview Video') : __('Preview Photo')"
									data-testid="media-preview-btn"
									@click="previewMedia = med"
								>
									<img
										v-if="med.media_type === 'Photo'"
										:src="med.thumbnail_url || med.file_url"
										class="w-full h-full object-cover"
										alt="Evidence"
									/>
									<div
										v-else
										class="flex flex-col items-center justify-center text-gray-500"
									>
										<FeatherIcon name="video" class="w-6 h-6" />
										<span class="text-[9px] mt-1">{{ __("Video") }}</span>
									</div>
								</button>

								<!-- Delete button (only in Draft) -->
								<button
									v-if="activeReport?.status === 'Draft'"
									type="button"
									class="absolute top-1 end-1 p-1 rounded-full bg-black/60 text-white hover:bg-black/80 transition-colors z-10"
									:aria-label="__('Remove')"
									@click.stop="removeMedia(med)"
								>
									<FeatherIcon name="x" class="w-3.5 h-3.5" />
								</button>
							</div>

							<!-- Upload button if slots available and report is Draft -->
							<div
								v-if="
									activeReport?.status === 'Draft' &&
									getCheckpointMedia(cp).length < (cp.max_count || 1)
								"
								class="w-24 h-24 rounded-lg border-2 border-dashed border-gray-300 hover:border-gray-400 flex flex-col items-center justify-center transition-colors cursor-pointer relative"
							>
								<input
									type="file"
									:accept="getAcceptType(cp)"
									capture="environment"
									class="absolute inset-0 opacity-0 cursor-pointer w-full h-full"
									@change="onFileSelected($event, cp)"
								/>
								<FeatherIcon
									:name="cp.media_type === 'Video' ? 'video' : 'camera'"
									class="w-6 h-6 text-gray-400"
								/>
								<span class="text-[10px] text-gray-500 font-medium mt-1">
									{{ cp.media_type === 'Video' ? __('Add Video') : __('Add Photo') }}
								</span>
							</div>
						</div>
					</div>
				</div>
			</div>
		</template>

		<template #actions>
			<div
				v-if="currentView === 'doc' && activeReport?.status === 'Draft'"
				class="flex items-center justify-end gap-3"
			>
				<button
					type="button"
					class="min-h-[44px] md:min-h-[40px] px-4 py-2 rounded-lg border border-gray-200 text-gray-700 text-sm font-medium hover:bg-gray-50 active:bg-gray-100 transition-colors touch-manipulation"
					@click="currentView = 'list'"
				>
					{{ __("Cancel") }}
				</button>

				<Button
					data-testid="submit-report-btn"
					variant="solid"
					:disabled="!isSubmittable"
					:loading="submitting"
					class="min-h-[44px] md:min-h-[40px] px-5 text-sm font-medium touch-manipulation"
					@click="submitReport"
				>
					{{ __("Submit Report") }}
				</Button>
			</div>
		</template>
	</DialogHost>

	<!-- Preview modal -->
	<Teleport to="body">
		<div
			v-if="previewMedia"
			data-testid="outlet-media-preview-modal"
			class="fixed inset-0 z-[600] flex items-center justify-center bg-black/75 p-4 sm:p-6"
			role="dialog"
			aria-modal="true"
			:aria-label="previewMedia.media_type === 'Video' ? __('Video Evidence') : __('Photo Evidence')"
			@click.self="previewMedia = null"
		>
			<div
				class="relative max-w-3xl w-full max-h-[90vh] bg-white rounded-xl shadow-2xl flex flex-col overflow-hidden"
			>
				<!-- Header -->
				<div
					class="flex items-center justify-between px-4 py-3 border-b border-gray-200 bg-gray-50"
				>
					<span class="font-semibold text-gray-800 text-sm">
						{{
							previewMedia.media_type === "Video"
								? __("Video Evidence")
								: __("Photo Evidence")
						}}
					</span>
					<button
						type="button"
						data-testid="close-preview-btn"
						class="p-1 rounded-lg text-gray-500 hover:text-gray-700 hover:bg-gray-200 transition-colors"
						:aria-label="__('Close')"
						@click="previewMedia = null"
					>
						<FeatherIcon name="x" class="w-5 h-5" />
					</button>
				</div>

				<!-- Body -->
				<div
					class="flex-1 overflow-auto p-4 flex items-center justify-center bg-black/5 min-h-[200px]"
				>
					<img
						v-if="previewMedia.media_type === 'Photo'"
						data-testid="preview-image"
						:src="previewMedia.file_url"
						class="max-w-full max-h-[70vh] object-contain rounded"
						alt="Evidence Preview"
					/>
					<video
						v-else
						data-testid="preview-video"
						:src="previewMedia.file_url"
						controls
						preload="metadata"
						class="max-w-full max-h-[70vh] rounded bg-black"
					>
						{{ __("Your browser does not support the video tag.") }}
					</video>
				</div>

				<!-- Footer / Actions -->
				<div
					class="px-4 py-2 border-t border-gray-200 bg-gray-50 flex justify-end gap-2"
				>
					<a
						:href="previewMedia.file_url"
						target="_blank"
						rel="noopener noreferrer"
						class="text-xs text-primary-600 hover:underline flex items-center gap-1 py-1"
					>
						<FeatherIcon name="external-link" class="w-3.5 h-3.5" />
						<span>{{ __("Open in new tab") }}</span>
					</a>
				</div>
			</div>
		</div>
	</Teleport>
</template>

<script setup>
import DialogHost from "@/components/common/DialogHost.js"
import { useToast } from "@/composables/useToast"
import { useOutletMonitoringStore } from "@/stores/outletMonitoring"
import { call } from "@/utils/apiWrapper"
import {
	checkVideo,
	preparePhoto,
	removeOrphanFile,
	uploadPrivate,
} from "@/utils/outletMonitoringMedia"
import { STATUS_PILL_BASE, STATUS_TONES } from "@/utils/statusTone"
import { Button, FeatherIcon } from "frappe-ui"
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue"

const props = defineProps({
	modelValue: { type: Boolean, default: false },
	show: { type: Boolean, default: false },
	embedded: { type: Boolean, default: false },
	posProfile: { type: String, default: null },
	company: { type: String, default: null },
})

const emit = defineEmits(["update:modelValue", "update:show"])

const store = useOutletMonitoringStore()
const toast = useToast()

const dialogShow = computed({
	get: () => props.show || props.modelValue,
	set: (val) => {
		emit("update:show", val)
		emit("update:modelValue", val)
	},
})

const dialogTitle = computed(() => {
	if (currentView.value === "doc") {
		return activeReport.value?.template || __("Report Detail")
	}
	return __("Outlet Monitoring")
})

const currentView = ref("list") // "list" | "doc"
const currentTab = ref("tasks") // "tasks" | "history"
const activeReportName = ref(null)
const activeReport = ref(null)
const historyItems = ref([])
const historyLoading = ref(false)
const docLoading = ref(false)
const submitting = ref(false)
const previewMedia = ref(null)

function onGlobalKeydown(e) {
	if (e.key === "Escape" && previewMedia.value) {
		previewMedia.value = null
		e.stopPropagation()
	}
}

onMounted(() => {
	if (typeof window !== "undefined") {
		window.addEventListener("keydown", onGlobalKeydown)
	}
})

onBeforeUnmount(() => {
	if (typeof window !== "undefined") {
		window.removeEventListener("keydown", onGlobalKeydown)
	}
})

watch(
	() => dialogShow.value,
	(isOpen) => {
		if (isOpen) {
			currentView.value = "list"
			store.refresh(props.posProfile)
			if (currentTab.value === "history") {
				loadHistory()
			}
		} else {
			previewMedia.value = null
		}
	},
	{ immediate: true },
)

function switchTab(tab) {
	currentTab.value = tab
	if (tab === "tasks") {
		store.refresh(props.posProfile)
	} else if (tab === "history") {
		loadHistory()
	}
}

async function loadHistory() {
	historyLoading.value = true
	try {
		const res = await call("pos_next.api.outlet_monitoring.get_history", {
			pos_profile: props.posProfile,
			start: 0,
			page_length: 30,
		})
		historyItems.value = res?.items || []
	} catch {
		historyItems.value = []
	} finally {
		historyLoading.value = false
	}
}

async function openReport(reportName) {
	activeReportName.value = reportName
	currentView.value = "doc"
	await loadReport(reportName)
}

async function loadReport(reportName) {
	docLoading.value = true
	try {
		const res = await call("pos_next.api.outlet_monitoring.get_report", {
			name: reportName,
		})
		activeReport.value = res || null
	} catch (err) {
		toast.showError(err?.message || __("Failed to load report"))
	} finally {
		docLoading.value = false
	}
}

function getCheckpointMedia(cp) {
	if (!activeReport.value?.media) return []
	return activeReport.value.media.filter(
		(m) => m.checkpoint_row === cp.name && !m.purged,
	)
}

function isCheckpointComplete(cp) {
	const count = getCheckpointMedia(cp).length
	const min = cp.min_count || 1
	return count >= min
}

const isSubmittable = computed(() => {
	if (!activeReport.value || activeReport.value.status !== "Draft") return false
	if (submitting.value) return false
	const checkpoints = activeReport.value.checkpoints || []
	for (const cp of checkpoints) {
		if (cp.required && !isCheckpointComplete(cp)) {
			return false
		}
	}
	return true
})

function getAcceptType(cp) {
	if (cp.media_type === "Video") return "video/mp4,video/webm"
	if (cp.media_type === "Photo") return "image/jpeg,image/png,image/*"
	return "image/*,video/mp4,video/webm"
}

async function onFileSelected(event, cp) {
	const file = event.target.files?.[0]
	event.target.value = ""
	if (!file) return
	await handleMediaUpload(file, cp)
}

async function handleMediaUpload(file, cp) {
	const reportName = activeReport.value?.name
	if (!reportName) return

	const uploadedOrphans = []
	try {
		if (cp.media_type === "Video" || file.type?.startsWith("video/")) {
			const { file: checkedFile } = await checkVideo(
				file,
				store.config,
				cp.video_max_seconds,
			)
			const vidRes = await uploadPrivate(checkedFile, reportName)
			uploadedOrphans.push(vidRes.name)

			await call("pos_next.api.outlet_monitoring.register_media", {
				report: reportName,
				checkpoint_row: cp.name,
				file_name: vidRes.name,
			})
		} else {
			const { main, thumb } = await preparePhoto(file, store.config)
			const mainRes = await uploadPrivate(main, reportName)
			uploadedOrphans.push(mainRes.name)
			const thumbRes = await uploadPrivate(thumb, reportName)
			uploadedOrphans.push(thumbRes.name)

			await call("pos_next.api.outlet_monitoring.register_media", {
				report: reportName,
				checkpoint_row: cp.name,
				file_name: mainRes.name,
				thumbnail_file_name: thumbRes.name,
			})
		}
		toast.showSuccess(__("Media uploaded"))
		await loadReport(reportName)
	} catch (err) {
		for (const orphan of uploadedOrphans) {
			await removeOrphanFile(orphan)
		}
		toast.showError(err?.message || __("Failed to upload media"))
	}
}

async function removeMedia(med) {
	try {
		await call("pos_next.api.outlet_monitoring.remove_media", {
			report: activeReport.value.name,
			media_row: med.name,
		})
		await loadReport(activeReport.value.name)
	} catch (err) {
		toast.showError(err?.message || __("Failed to remove media"))
	}
}

async function createRevision(name) {
	try {
		const res = await call("pos_next.api.outlet_monitoring.create_revision", {
			name,
		})
		if (res?.name) {
			await openReport(res.name)
		}
	} catch (err) {
		toast.showError(err?.message || __("Failed to create revision"))
	}
}

async function submitReport() {
	if (!isSubmittable.value) return
	submitting.value = true
	try {
		await call("pos_next.api.outlet_monitoring.submit_report", {
			name: activeReport.value.name,
		})
		toast.showSuccess(__("Report submitted successfully"))
		await store.refresh(props.posProfile)
		currentView.value = "list"
	} catch (err) {
		toast.showError(err?.message || __("Failed to submit report"))
	} finally {
		submitting.value = false
	}
}

function getReportStatusTone(rep) {
	if (!rep) return STATUS_TONES.gray
	if (rep.status === "Submitted") return STATUS_TONES.blue
	if (rep.status === "Reviewed") return STATUS_TONES.green
	if (rep.status === "Missed") return STATUS_TONES.red
	if (rep.status === "Waived") return STATUS_TONES.gray
	return STATUS_TONES.orange
}

function getHistoryLabel(item) {
	if (item.status === "Submitted") return "Pending Review"
	if (item.status === "Reviewed") {
		if (item.review_result === "Pass") return "Compliant"
		if (item.correction_requested) return "Needs Correction"
		return "Non-Compliant"
	}
	if (item.status === "Missed") return "Missed"
	if (item.status === "Waived") return "Waived"
	return item.status
}

function getHistoryTone(item) {
	if (item.status === "Submitted") return STATUS_TONES.blue
	if (item.status === "Reviewed") {
		if (item.review_result === "Pass") return STATUS_TONES.green
		if (item.correction_requested) return STATUS_TONES.amber
		return STATUS_TONES.red
	}
	if (item.status === "Missed") return STATUS_TONES.red
	if (item.status === "Waived") return STATUS_TONES.gray
	return STATUS_TONES.orange
}

function getCheckpointAssessmentLabel(cp) {
	if (cp.assessment === "Pass") return cp.pass_label || __("Pass")
	if (cp.assessment === "Fail") return cp.fail_label || __("Fail")
	if (cp.assessment === "N/A") return cp.na_label || __("N/A")
	return cp.assessment || ""
}

function getCheckpointAssessmentTone(assessment) {
	if (assessment === "Pass") return STATUS_TONES.green
	if (assessment === "Fail") return STATUS_TONES.red
	if (assessment === "N/A") return STATUS_TONES.gray
	return STATUS_TONES.blue
}

defineExpose({
	handleMediaUpload,
	isCheckpointComplete,
	isSubmittable,
	openReport,
	loadReport,
})
</script>
