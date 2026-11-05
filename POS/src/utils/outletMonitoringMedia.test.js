/**
 * @vitest-environment jsdom
 */
import { beforeEach, describe, expect, it, vi } from "vitest"

const compressImageMock = vi.hoisted(() => vi.fn())
const callMock = vi.hoisted(() => vi.fn(() => Promise.resolve(null)))

vi.mock("@/utils/compressImage", () => ({
	compressImage: (...args) => compressImageMock(...args),
}))

vi.mock("@/utils/apiWrapper", () => ({
	call: (...args) => callMock(...args),
}))

globalThis.__ = (msg, replacements = []) => {
	if (!Array.isArray(replacements) || !replacements.length) return msg
	let out = msg
	for (const [i, v] of replacements.entries()) {
		out = out.split(`{${i}}`).join(String(v))
	}
	return out
}

import {
	checkVideo,
	preparePhoto,
	removeOrphanFile,
	uploadPrivate,
} from "./outletMonitoringMedia"

describe("outletMonitoringMedia", () => {
	beforeEach(() => {
		vi.clearAllMocks()
	})

	it("preparePhoto rejects when compressImage returns the original file", async () => {
		const originalFile = new File(["dummy"], "sample.jpg", {
			type: "image/jpeg",
		})
		compressImageMock.mockResolvedValue(originalFile)

		await expect(
			preparePhoto(originalFile, {
				photo_quality: 80,
				photo_max_edge: 1600,
				thumbnail_max_edge: 400,
			}),
		).rejects.toThrow("Photo could not be processed")
	})

	it("preparePhoto returns jpeg main and thumb with configured edges", async () => {
		const originalFile = new File(["dummy"], "sample.png", {
			type: "image/png",
		})
		const mainFile = new File(["main"], "sample.jpg", {
			type: "image/jpeg",
		})
		const thumbFile = new File(["thumb"], "sample_thumb.jpg", {
			type: "image/jpeg",
		})

		compressImageMock
			.mockResolvedValueOnce(mainFile)
			.mockResolvedValueOnce(thumbFile)

		const result = await preparePhoto(originalFile, {
			photo_quality: 75,
			photo_max_edge: 1600,
			thumbnail_max_edge: 400,
		})

		expect(compressImageMock).toHaveBeenNthCalledWith(1, originalFile, {
			maxEdge: 1600,
			quality: 0.75,
		})
		expect(compressImageMock).toHaveBeenNthCalledWith(2, originalFile, {
			maxEdge: 400,
			quality: 0.75,
		})
		expect(result).toEqual({ main: mainFile, thumb: thumbFile })
	})

	it("checkVideo rejects too long video and disallowed webm", async () => {
		// Disallowed webm
		const webmFile = new File(["webm"], "video.webm", {
			type: "video/webm",
		})
		await expect(
			checkVideo(webmFile, { allow_webm: 0, video_max_mb: 20 }),
		).rejects.toThrow("Unsupported video format")

		// Allowed webm should pass type check
		const allowedWebm = new File(["webm"], "video.webm", {
			type: "video/webm",
		})
		allowedWebm.duration = 10
		await expect(
			checkVideo(allowedWebm, {
				allow_webm: 1,
				video_max_mb: 20,
				video_max_seconds: 30,
			}),
		).resolves.toEqual({ file: allowedWebm, poster: null })

		// Too long video
		const longMp4 = new File(["mp4"], "video.mp4", {
			type: "video/mp4",
		})
		longMp4.duration = 45
		await expect(
			checkVideo(longMp4, { video_max_seconds: 30, video_max_mb: 20 }),
		).rejects.toThrow("Video duration exceeds limit")

		// Max seconds override
		const normalMp4 = new File(["mp4"], "video.mp4", {
			type: "video/mp4",
		})
		normalMp4.duration = 20
		await expect(
			checkVideo(normalMp4, { video_max_seconds: 30, video_max_mb: 20 }, 15),
		).rejects.toThrow("Video duration exceeds limit")

		// Rejects NaN or non-finite duration
		const nanMp4 = new File(["mp4"], "video.mp4", {
			type: "video/mp4",
		})
		nanMp4.duration = Number.NaN
		await expect(
			checkVideo(nanMp4, { video_max_seconds: 30, video_max_mb: 20 }),
		).rejects.toThrow("Could not verify video duration")

		const infMp4 = new File(["mp4"], "video.mp4", {
			type: "video/mp4",
		})
		infMp4.duration = Number.POSITIVE_INFINITY
		await expect(
			checkVideo(infMp4, { video_max_seconds: 30, video_max_mb: 20 }),
		).rejects.toThrow("Could not verify video duration")
	})

	it("checkVideo revokes object URL in finally even when metadata inspection times out", async () => {
		const videoFile = new File(["mp4"], "video.mp4", {
			type: "video/mp4",
		})
		const revokeSpy = vi.fn()
		const createSpy = vi.fn(() => "blob:http://localhost/test-blob")
		globalThis.URL.createObjectURL = createSpy
		globalThis.URL.revokeObjectURL = revokeSpy

		await expect(
			checkVideo(videoFile, { video_max_seconds: 30, video_max_mb: 20 }),
		).rejects.toThrow("Could not verify video duration")

		expect(createSpy).toHaveBeenCalledWith(videoFile)
		expect(revokeSpy).toHaveBeenCalledWith("blob:http://localhost/test-blob")
	})

	it("uploadPrivate posts to upload_file with CSRF token and returns file info", async () => {
		const testFile = new File(["photo-data"], "photo.jpg", {
			type: "image/jpeg",
		})
		window.csrf_token = "test-csrf-token"

		const fetchMock = vi.fn().mockResolvedValue({
			ok: true,
			json: async () => ({
				message: {
					name: "FILE-12345",
					file_url: "/private/files/photo.jpg",
				},
			}),
		})
		globalThis.fetch = fetchMock

		const result = await uploadPrivate(testFile, "OM-REP-001")

		expect(fetchMock).toHaveBeenCalledWith("/api/method/upload_file", {
			method: "POST",
			headers: { "X-Frappe-CSRF-Token": "test-csrf-token" },
			body: expect.any(FormData),
		})
		expect(result).toEqual({
			name: "FILE-12345",
			file_url: "/private/files/photo.jpg",
		})
	})

	it("removeOrphanFile calls delete endpoint", async () => {
		callMock.mockResolvedValueOnce({ ok: true })
		await removeOrphanFile("FILE-12345")
		expect(callMock).toHaveBeenCalledWith(
			"pos_next.api.outlet_monitoring.remove_orphan_file",
			{ file_name: "FILE-12345" },
		)
	})
})
