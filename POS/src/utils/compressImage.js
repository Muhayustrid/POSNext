/**
 * Compresses an image File on-device before upload.
 * Scales dimensions to maxEdge (longest side) and outputs JPEG with the specified quality.
 * On any failure or if file is not an image, returns the original file.
 *
 * @param {File} file
 * @param {object} [options]
 * @param {number} [options.maxEdge=1600]
 * @param {number} [options.quality=0.8]
 * @returns {Promise<File>}
 */
export async function compressImage(
	file,
	{ maxEdge = 1600, quality = 0.8 } = {},
) {
	if (!file || !file.type || !file.type.startsWith("image/")) {
		return file
	}
	if (typeof globalThis.createImageBitmap !== "function") {
		return file
	}
	try {
		const bitmap = await globalThis.createImageBitmap(file)
		let { width, height } = bitmap
		if (width > maxEdge || height > maxEdge) {
			if (width >= height) {
				height = Math.round((height * maxEdge) / width)
				width = maxEdge
			} else {
				width = Math.round((width * maxEdge) / height)
				height = maxEdge
			}
		}
		const canvas = document.createElement("canvas")
		canvas.width = width
		canvas.height = height
		const ctx = canvas.getContext("2d")
		if (!ctx) return file
		ctx.drawImage(bitmap, 0, 0, width, height)

		const blob = await new Promise((resolve) => {
			canvas.toBlob(resolve, "image/jpeg", quality)
		})
		if (!blob) return file

		const name = file.name
			? `${file.name.replace(/\.[^/.]+$/, "")}.jpg`
			: "photo.jpg"
		return new File([blob], name, {
			type: "image/jpeg",
			lastModified: Date.now(),
		})
	} catch {
		return file
	}
}
