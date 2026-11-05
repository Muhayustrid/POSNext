// Copyright (c) 2026, BrainWise and contributors
// Node unit tests for outlet monitoring pure helpers.
// Run: node pos_next/tests/js/outlet_monitoring_utils.test.cjs
const assert = require("node:assert/strict");
const { test } = require("node:test");
const utils = require("../../public/js/outlet_monitoring_utils.js");

test("resolvePeriod This Week starts Monday", () => {
	// 2026-11-05 is Thursday -> Monday is 2026-11-02, to_date is today 2026-11-05
	const res = utils.resolvePeriod("This Week", "2026-11-05");
	assert.deepEqual(res, {
		from_date: "2026-11-02",
		to_date: "2026-11-05",
	});
});

test("resolvePeriod Last Month", () => {
	// today 2026-11-05 -> full prior month October
	const res = utils.resolvePeriod("Last Month", "2026-11-05");
	assert.deepEqual(res, {
		from_date: "2026-10-01",
		to_date: "2026-10-31",
	});
});

test("resolvePeriod handles Today, Yesterday, Last Week, This Month, and Custom", () => {
	const today = "2026-11-05";
	assert.deepEqual(utils.resolvePeriod("Today", today), {
		from_date: "2026-11-05",
		to_date: "2026-11-05",
	});
	assert.deepEqual(utils.resolvePeriod("Yesterday", today), {
		from_date: "2026-11-04",
		to_date: "2026-11-04",
	});
	assert.deepEqual(utils.resolvePeriod("Last Week", today), {
		from_date: "2026-10-26",
		to_date: "2026-11-01",
	});
	assert.deepEqual(utils.resolvePeriod("This Month", today), {
		from_date: "2026-11-01",
		to_date: "2026-11-05",
	});
	assert.equal(utils.resolvePeriod("Custom", today), null);
	assert.equal(utils.resolvePeriod(null, today), null);
	assert.equal(utils.resolvePeriod("Unknown", today), null);
});

test("galleryCardHtml never emits <video> and escapes names", () => {
	const item = {
		report: "OMR-261105-0001",
		media_row: "row-1",
		file_url: "/private/files/test.mp4",
		thumbnail_url: null,
		media_type: "Video",
		assessment: "Pass",
		checkpoint_name: "<script>alert('xss')</script>",
		category: "Kebersihan & Kerapian",
		company: "Roti & Kopi",
		pos_profile: "Kasir 1",
		operational_date: "2026-11-05",
		uploaded_at: "2026-11-05 10:00:00",
		submitted_by: "cashier@example.com",
	};
	const html = utils.galleryCardHtml(item);
	assert.equal(html.includes("<video"), false, "galleryCardHtml must NEVER emit <video> tag");
	assert.equal(html.includes("<script>"), false, "checkpoint_name must be escaped");
	assert.equal(html.includes("&lt;script&gt;alert(&#39;xss&#39;)&lt;/script&gt;"), true);
	assert.equal(html.includes("Roti &amp; Kopi"), true, "company must be escaped");
});

test("galleryCardHtml video uses play placeholder", () => {
	const videoItem = {
		report: "OMR-261105-0002",
		media_row: "row-2",
		file_url: "/private/files/sample.mp4",
		thumbnail_url: null,
		media_type: "Video",
		assessment: "Pending",
		checkpoint_name: "Grooming Kasir",
		company: "Ropi Pusat",
		operational_date: "2026-11-05",
	};
	const html = utils.galleryCardHtml(videoItem);
	assert.equal(html.includes("<svg"), true, "video card must include SVG play placeholder");
	assert.equal(html.includes("play-icon") || html.includes("video-placeholder"), true);

	const photoItem = {
		report: "OMR-261105-0003",
		media_row: "row-3",
		file_url: "/private/files/sample.jpg",
		thumbnail_url: "/private/files/sample-thumb.jpg",
		media_type: "Photo",
		assessment: "Pass",
		checkpoint_name: "Display Roti",
		company: "Ropi Pusat",
		operational_date: "2026-11-05",
	};
	const photoHtml = utils.galleryCardHtml(photoItem);
	assert.equal(photoHtml.includes("<img loading=\"lazy\""), true);
	assert.equal(photoHtml.includes("sample-thumb.jpg"), true);
});

test("mediaPreviewHtml purged", () => {
	const rows = [
		{
			checkpoint_row: "cp-1",
			file_url: "/private/files/purged.jpg",
			thumbnail_url: null,
			media_type: "Photo",
			purged: 1,
		},
		{
			checkpoint_row: "cp-2",
			file_url: "/private/files/active.jpg",
			thumbnail_url: null,
			media_type: "Photo",
			purged: 0,
		},
		{
			checkpoint_row: "cp-3",
			file_url: "/private/files/active.mp4",
			thumbnail_url: null,
			media_type: "Video",
			purged: 0,
		},
	];
	const html = utils.mediaPreviewHtml(rows);
	assert.equal(html.includes("Media removed by retention"), true, "Purged row must show retention notice");
	assert.equal(html.includes("<img"), true, "Active photo must render img");
	assert.equal(html.includes("<video controls preload=\"metadata\""), true, "Active video must render video controls");
});

test("assessmentTone maps values accurately", () => {
	assert.equal(utils.assessmentTone("Pass"), "green");
	assert.equal(utils.assessmentTone("Sesuai SOP"), "green");
	assert.equal(utils.assessmentTone("Fail"), "red");
	assert.equal(utils.assessmentTone("Tidak Sesuai SOP"), "red");
	assert.equal(utils.assessmentTone("N/A"), "gray");
	assert.equal(utils.assessmentTone("Tidak Berlaku"), "gray");
	assert.equal(utils.assessmentTone("Pending"), "orange");
	assert.equal(utils.assessmentTone(null), "gray");
	assert.equal(utils.assessmentTone(""), "gray");
});
