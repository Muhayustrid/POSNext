import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { compressImage } from "./compressImage"

describe("compressImage", () => {
	const originalCreateImageBitmap = globalThis.createImageBitmap

	afterEach(() => {
		if (originalCreateImageBitmap) {
			globalThis.createImageBitmap = originalCreateImageBitmap
		} else {
			globalThis.createImageBitmap = undefined
		}
		vi.restoreAllMocks()
	})

	it("returns original file if file is not an image", async () => {
		const txtFile = new File(["hello"], "hello.txt", { type: "text/plain" })
		const result = await compressImage(txtFile)
		expect(result).toBe(txtFile)
	})

	it("returns original file if createImageBitmap is not supported", async () => {
		globalThis.createImageBitmap = undefined
		const imgFile = new File(["fake"], "photo.png", { type: "image/png" })
		const result = await compressImage(imgFile)
		expect(result).toBe(imgFile)
	})

	it("scales dimensions and converts to JPEG when image exceeds maxEdge", async () => {
		globalThis.createImageBitmap = vi.fn().mockResolvedValue({
			width: 3200,
			height: 1600,
		})

		const drawImageSpy = vi.fn()
		const toBlobSpy = vi.fn((callback, mime, quality) => {
			callback(new Blob(["compressed"], { type: "image/jpeg" }))
		})

		const originalCreateElement = document.createElement.bind(document)
		vi.spyOn(document, "createElement").mockImplementation((tag) => {
			if (tag === "canvas") {
				return {
					width: 0,
					height: 0,
					getContext: vi.fn().mockReturnValue({ drawImage: drawImageSpy }),
					toBlob: toBlobSpy,
				}
			}
			return originalCreateElement(tag)
		})

		const imgFile = new File(["data"], "shelf.png", { type: "image/png" })
		const result = await compressImage(imgFile, { maxEdge: 1600, quality: 0.8 })

		expect(drawImageSpy).toHaveBeenCalled()
		expect(toBlobSpy).toHaveBeenCalledWith(
			expect.any(Function),
			"image/jpeg",
			0.8,
		)
		expect(result.type).toBe("image/jpeg")
		expect(result.name).toBe("shelf.jpg")
	})

	it("falls back to original file if canvas.toBlob fails", async () => {
		globalThis.createImageBitmap = vi.fn().mockResolvedValue({
			width: 800,
			height: 600,
		})

		vi.spyOn(document, "createElement").mockImplementation((tag) => {
			if (tag === "canvas") {
				return {
					width: 0,
					height: 0,
					getContext: vi.fn().mockReturnValue({ drawImage: vi.fn() }),
					toBlob: vi.fn((cb) => cb(null)),
				}
			}
			return document.createElement(tag)
		})

		const imgFile = new File(["data"], "photo.jpg", { type: "image/jpeg" })
		const result = await compressImage(imgFile)
		expect(result).toBe(imgFile)
	})
})
