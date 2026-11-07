// Copyright (c) 2026, BrainWise and contributors
// For license information, please see license.txt

/**
 * Pure helpers for the Outlet Monitoring Desk form and Gallery page.
 * No frappe dependency so this file doubles as a node unit-test module.
 */
(function (root, factory) {
	const api = factory();
	if (typeof module === "object" && module.exports) {
		module.exports = api;
	} else {
		root.outletMonitoringUtils = api;
	}
})(typeof self !== "undefined" ? self : this, function () {
	function escapeHtml(value) {
		if (value === null || value === undefined) return "";
		return String(value).replace(/[&<>"']/g, (ch) => (
			{ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
		));
	}

	function parseDate(d) {
		if (!d) return new Date();
		if (d instanceof Date) return new Date(d.getFullYear(), d.getMonth(), d.getDate());
		if (typeof d === "string") {
			const parts = d.split("-").map(Number);
			if (parts.length === 3 && !isNaN(parts[0]) && !isNaN(parts[1]) && !isNaN(parts[2])) {
				return new Date(parts[0], parts[1] - 1, parts[2]);
			}
		}
		const dt = new Date(d);
		return new Date(dt.getFullYear(), dt.getMonth(), dt.getDate());
	}

	function formatDate(dt) {
		const y = dt.getFullYear();
		const m = String(dt.getMonth() + 1).padStart(2, "0");
		const d = String(dt.getDate()).padStart(2, "0");
		return `${y}-${m}-${d}`;
	}

	/**
	 * Resolves period name to { from_date, to_date } ISO strings (YYYY-MM-DD).
	 * Weeks start on Monday. Custom or unknown returns null.
	 */
	function resolvePeriod(period, today) {
		if (!period || typeof period !== "string" || period === "Custom") return null;
		const dt = parseDate(today);

		switch (period) {
			case "Today": {
				const s = formatDate(dt);
				return { from_date: s, to_date: s };
			}
			case "Yesterday": {
				const yday = new Date(dt.getFullYear(), dt.getMonth(), dt.getDate() - 1);
				const s = formatDate(yday);
				return { from_date: s, to_date: s };
			}
			case "This Week": {
				const day = dt.getDay();
				const diffToMonday = day === 0 ? 6 : day - 1;
				const mon = new Date(dt.getFullYear(), dt.getMonth(), dt.getDate() - diffToMonday);
				return { from_date: formatDate(mon), to_date: formatDate(dt) };
			}
			case "Last Week": {
				const day = dt.getDay();
				const diffToMonday = day === 0 ? 6 : day - 1;
				const monThisWeek = new Date(dt.getFullYear(), dt.getMonth(), dt.getDate() - diffToMonday);
				const monLastWeek = new Date(monThisWeek.getFullYear(), monThisWeek.getMonth(), monThisWeek.getDate() - 7);
				const sunLastWeek = new Date(monThisWeek.getFullYear(), monThisWeek.getMonth(), monThisWeek.getDate() - 1);
				return { from_date: formatDate(monLastWeek), to_date: formatDate(sunLastWeek) };
			}
			case "This Month": {
				const first = new Date(dt.getFullYear(), dt.getMonth(), 1);
				return { from_date: formatDate(first), to_date: formatDate(dt) };
			}
			case "Last Month": {
				const first = new Date(dt.getFullYear(), dt.getMonth() - 1, 1);
				const last = new Date(dt.getFullYear(), dt.getMonth(), 0);
				return { from_date: formatDate(first), to_date: formatDate(last) };
			}
			default:
				return null;
		}
	}

	/**
	 * Maps a label snapshot color to a gallery badge class.
	 * Null/unknown colors fall back to the gray "Not assessed" badge.
	 * shortcut: tone comes straight from the label palette color, upgrade to
	 * per-label custom hues only if HQ asks for non-palette colors.
	 */
	function labelBadgeClass(color) {
		if (!color) return "badge-gray";
		const v = String(color).trim().toLowerCase();
		if (["green", "red", "yellow", "orange", "blue", "gray", "purple"].includes(v)) {
			return `badge-${v}`;
		}
		return "badge-gray";
	}

	/**
	 * Builds HTML for a single gallery card.
	 * Text is escaped. Videos render an SVG play placeholder, NEVER a <video> tag.
	 */
	function galleryCardHtml(item) {
		if (!item) return "";
		const report = escapeHtml(item.report || "");
		const mediaRow = escapeHtml(item.media_row || "");
		const checkpointName = escapeHtml(item.checkpoint_name || "");
		const company = escapeHtml(item.company || "");
		const profile = escapeHtml(item.pos_profile || "");
		const operationalDate = escapeHtml(item.operational_date || "");
		const uploadedAt = escapeHtml(item.uploaded_at || "");
		const submittedBy = escapeHtml(item.submitted_by || "");
		const category = escapeHtml(item.category || "");
		// Label text and badge color come from the server-resolved snapshot
		// (label/color); items without a label show a gray "Not assessed" badge.
		const labelText = item.label
			? escapeHtml(item.label)
			: typeof __ === "function"
				? __("Not assessed")
				: "Not assessed";
		const badgeClass = labelBadgeClass(item.color);
		const isVideo = String(item.media_type || "").toLowerCase() === "video";

		let mediaHtml = "";
		if (isVideo) {
			const thumb = item.thumbnail_url
				? `<img loading="lazy" class="gallery-card-thumb" src="${escapeHtml(item.thumbnail_url)}" alt="${checkpointName}">`
				: "";
			mediaHtml = `
				<div class="gallery-video-placeholder">
					${thumb}
					<div class="video-play-overlay">
						<svg class="play-icon" viewBox="0 0 24 24" width="40" height="40" fill="currentColor">
							<path d="M8 5v14l11-7z"/>
						</svg>
					</div>
				</div>
			`;
		} else {
			const src = escapeHtml(item.thumbnail_url || item.file_url || "");
			mediaHtml = `
				<div class="gallery-photo-container">
					<img loading="lazy" class="gallery-card-thumb" src="${src}" alt="${checkpointName}">
				</div>
			`;
		}

		return `
			<div class="outlet-gallery-card" data-report="${report}" data-media-row="${mediaRow}">
				<div class="gallery-card-media">
					${mediaHtml}
					<span class="gallery-card-badge ${badgeClass}">${labelText}</span>
				</div>
				<div class="gallery-card-body">
					<div class="gallery-card-title" title="${checkpointName}">${checkpointName}</div>
					<div class="gallery-card-meta text-muted small">
						<div class="gallery-card-company">${company}${profile ? ` · ${profile}` : ""}</div>
						<div class="gallery-card-time">${operationalDate || uploadedAt}${submittedBy ? ` · ${submittedBy}` : ""}</div>
					</div>
				</div>
			</div>
		`.trim();
	}

	/**
	 * Builds HTML preview for media rows in the Desk report form.
	 * Purged rows show retention notice. Videos render <video controls preload="metadata">.
	 */
	function mediaPreviewHtml(mediaRows) {
		if (!mediaRows || !mediaRows.length) {
			return `<div class="text-muted p-2">No media uploaded</div>`;
		}
		const items = mediaRows.map((row) => {
			const rowId = escapeHtml(row.name || row.checkpoint_row || "");
			if (row.purged) {
				return `
					<div class="media-preview-item media-preview-purged" data-row="${rowId}" style="border: 1px dashed #d1d8dd; padding: 10px; border-radius: 4px; background: #fafbfc;">
						<div class="text-muted small">
							Media removed by retention
						</div>
					</div>
				`;
			}
			const isVideo = String(row.media_type || "").toLowerCase() === "video";
			const fileUrl = escapeHtml(row.file_url || "");
			if (isVideo) {
				return `
					<div class="media-preview-item media-preview-video" data-row="${rowId}">
						<video controls preload="metadata" src="${fileUrl}" style="max-width: 280px; max-height: 200px; border-radius: 4px; display: block;"></video>
					</div>
				`;
			}
			const src = escapeHtml(row.file_url || row.thumbnail_url || "");
			return `
				<div class="media-preview-item media-preview-photo" data-row="${rowId}">
					<a href="${fileUrl}" target="_blank" rel="noopener noreferrer">
						<img loading="lazy" src="${src}" style="max-width: 280px; max-height: 200px; object-fit: contain; border-radius: 4px; display: block;" alt="Evidence">
					</a>
				</div>
			`;
		});

		return `
			<div class="outlet-monitoring-media-preview-grid" style="display: flex; flex-wrap: wrap; gap: 12px; margin-top: 8px;">
				${items.join("")}
			</div>
		`.trim();
	}

	return {
		escapeHtml,
		parseDate,
		formatDate,
		resolvePeriod,
		labelBadgeClass,
		galleryCardHtml,
		mediaPreviewHtml,
	};
});
