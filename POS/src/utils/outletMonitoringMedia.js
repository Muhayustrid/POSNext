import { call } from "@/utils/apiWrapper"
import { compressImage } from "@/utils/compressImage"
import { __ } from "@/utils/translation"

/**
 * Prepares a photo File on-device for upload: scales main photo and thumbnail to JPEG.
 * Throws if compression fails, returns the original file, or output is not image/jpeg.
 *
 * @param {File} file
 * @param {object} [config]
 * @returns {Promise<{main: File, thumb: File}>}
 */
export async function preparePhoto(file, config = {}) {
	const quality =
		typeof config.photo_quality === "number" ? config.photo_quality / 100 : 0.8
	const maxEdge = config.photo_max_edge || 1600
	const thumbEdge = config.thumbnail_max_edge || 400

	const main = await compressImage(file, { maxEdge, quality })
	if (!main || main === file || main.type !== "image/jpeg") {
		throw new Error(__("Photo could not be processed"))
	}

	const thumb = await compressImage(file, { maxEdge: thumbEdge, quality })
	if (!thumb || thumb === file || thumb.type !== "image/jpeg") {
		throw new Error(__("Photo could not be processed"))
	}

	return { main, thumb }
}

/**
 * Validates a video File: checks format (mp4 or allow_webm), max MB, and duration.
 * Generates video poster frame on a best-effort basis.
 *
 * @param {File} file
 * @param {object} [config]
 * @param {number|null} [maxSeconds]
 * @returns {Promise<{file: File, poster: File|null}>}
 */
export async function checkVideo(file, config = {}, maxSeconds = null) {
	if (!file) {
		throw new Error(__("No video file provided"))
	}

	const isMp4 = file.type === "video/mp4"
	const isWebm = file.type === "video/webm"
	if (!isMp4 && !(isWebm && Boolean(config.allow_webm))) {
		throw new Error(__("Unsupported video format"))
	}

	const maxMb =
		typeof config.video_max_mb === "number" ? config.video_max_mb : 20
	if (file.size > maxMb * 1024 * 1024) {
		throw new Error(__("Video size exceeds limit"))
	}

	const limitSeconds =
		typeof maxSeconds === "number" && maxSeconds > 0
			? maxSeconds
			: config.video_max_seconds || 30

	let duration =
		typeof file.duration === "number" &&
		Number.isFinite(file.duration) &&
		file.duration > 0
			? file.duration
			: null
	const poster = null

	if (
		duration === null &&
		typeof document !== "undefined" &&
		typeof URL !== "undefined" &&
		typeof URL.createObjectURL === "function"
	) {
		let url = null
		try {
			const video = document.createElement("video")
			video.preload = "metadata"
			url = URL.createObjectURL(file)
			video.src = url

			await new Promise((resolve) => {
				let done = false
				const cleanup = () => {
					if (done) return
					done = true
					video.removeEventListener("loadedmetadata", onLoaded)
					video.removeEventListener("error", onError)
					clearTimeout(timer)
				}
				const onLoaded = () => {
					cleanup()
					resolve()
				}
				const onError = () => {
					cleanup()
					resolve()
				}
				const timer = setTimeout(() => {
					cleanup()
					resolve()
				}, 600)

				video.addEventListener("loadedmetadata", onLoaded)
				video.addEventListener("error", onError)
			})

			if (Number.isFinite(video.duration) && video.duration > 0) {
				duration = video.duration
			}
		} finally {
			if (url && typeof URL.revokeObjectURL === "function") {
				URL.revokeObjectURL(url)
			}
		}
	}

	if (!Number.isFinite(duration) || duration <= 0) {
		throw new Error(__("Could not verify video duration"))
	}

	if (duration > limitSeconds) {
		throw new Error(__("Video duration exceeds limit"))
	}

	return { file, poster }
}

/**
 * Uploads a private file attached to a POS Monitoring Report.
 *
 * @param {File} file
 * @param {string} reportDocname
 * @returns {Promise<{name: string, file_url: string}>}
 */
export async function uploadPrivate(file, reportDocname) {
	const form = new FormData()
	form.append("file", file)
	form.append("is_private", "1")
	form.append("doctype", "POS Monitoring Report")
	form.append("docname", reportDocname)

	const csrfToken = (typeof window !== "undefined" && window.csrf_token) || ""
	const response = await fetch("/api/method/upload_file", {
		method: "POST",
		headers: {
			"X-Frappe-CSRF-Token": csrfToken,
		},
		body: form,
	})

	if (!response.ok) {
		throw new Error(__("Upload failed"))
	}

	const data = await response.json()
	const msg = data?.message
	if (!msg?.name || !msg?.file_url) {
		throw new Error(__("Upload failed"))
	}

	return {
		name: msg.name,
		file_url: msg.file_url,
	}
}

/**
 * Removes an orphan file on the server if register_media fails.
 *
 * @param {string} fileName
 * @returns {Promise<void>}
 */
export async function removeOrphanFile(fileName) {
	if (!fileName) return
	try {
		await call("pos_next.api.outlet_monitoring.remove_orphan_file", {
			file_name: fileName,
		})
	} catch {
		// Suppress error on orphan cleanup
	}
}
