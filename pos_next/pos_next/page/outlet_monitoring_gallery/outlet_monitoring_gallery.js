// Copyright (c) 2026, BrainWise and contributors
// For license information, please see license.txt

{% include "pos_next/public/js/outlet_monitoring_utils.js" %}

const GALLERY_UTILS = outletMonitoringUtils;

frappe.pages["outlet-monitoring-gallery"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Outlet Monitoring Gallery"),
		single_column: true,
	});

	new OutletMonitoringGallery(page);
};

class OutletMonitoringGallery {
	constructor(page) {
		this.page = page;
		this.start = 0;
		this.items = [];
		this.has_more = false;
		this.loading = false;
		this.request_seq = 0;

		this.setup_fields();
		this.setup_dom();
		this.load_settings_and_refresh();
	}

	setup_fields() {
		this.company_field = this.page.add_field({
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			change: () => this.reload(),
		});

		this.pos_profile_field = this.page.add_field({
			fieldname: "pos_profile",
			label: __("POS Profile"),
			fieldtype: "Link",
			options: "POS Profile",
			get_query: () => {
				const company = this.company_field?.get_value();
				return company ? { filters: { company } } : {};
			},
			change: () => this.reload(),
		});

		this.period_field = this.page.add_field({
			fieldname: "period",
			label: __("Period"),
			fieldtype: "Select",
			options: [
				"Today",
				"Yesterday",
				"This Week",
				"Last Week",
				"This Month",
				"Last Month",
				"Custom",
			],
			default: "Today",
			change: () => {
				this.toggle_custom_dates();
				this.reload();
			},
		});

		this.from_date_field = this.page.add_field({
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			change: () => this.reload(),
		});

		this.to_date_field = this.page.add_field({
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			change: () => this.reload(),
		});

		this.category_field = this.page.add_field({
			fieldname: "category",
			label: __("Category"),
			fieldtype: "Link",
			options: "POS Monitoring Category",
			change: () => this.reload(),
		});

		this.checkpoint_field = this.page.add_field({
			fieldname: "checkpoint",
			label: __("Checkpoint"),
			fieldtype: "Link",
			options: "POS Monitoring Checkpoint",
			change: () => this.reload(),
		});

		this.assessment_field = this.page.add_field({
			fieldname: "assessment",
			label: __("Assessment"),
			fieldtype: "Select",
			options: ["", "Pass", "Fail", "Pending", "N/A"],
			change: () => this.reload(),
		});

		this.media_type_field = this.page.add_field({
			fieldname: "media_type",
			label: __("Media Type"),
			fieldtype: "Select",
			options: ["", "Photo", "Video"],
			change: () => this.reload(),
		});

		this.page.set_secondary_action(__("Refresh"), () => this.reload());
		this.toggle_custom_dates();
	}

	toggle_custom_dates() {
		const isCustom = this.period_field?.get_value() === "Custom";
		if (this.from_date_field?.$wrapper) {
			this.from_date_field.$wrapper.toggle(isCustom);
		}
		if (this.to_date_field?.$wrapper) {
			this.to_date_field.$wrapper.toggle(isCustom);
		}
	}

	setup_dom() {
		this.$container = $(`
			<div class="outlet-monitoring-gallery-container">
				<div class="outlet-gallery-grid"></div>
				<div class="outlet-gallery-empty text-muted text-center p-5" style="display: none;">
					<p>${__("No media found")}</p>
				</div>
				<div class="outlet-gallery-footer text-center p-4">
					<button class="btn btn-default btn-load-more" style="display: none;">
						${__("Load more")}
					</button>
				</div>
			</div>
		`).appendTo(this.page.main);

		this.$grid = this.$container.find(".outlet-gallery-grid");
		this.$empty = this.$container.find(".outlet-gallery-empty");
		this.$loadMore = this.$container.find(".btn-load-more");

		this.$loadMore.on("click", () => this.fetch_more());

		this.$grid.on("click", ".outlet-gallery-card", (e) => {
			const $card = $(e.currentTarget);
			const report = $card.attr("data-report");
			const mediaRow = $card.attr("data-media-row");
			const item = this.items.find(
				(i) => i.report === report && String(i.media_row) === String(mediaRow)
			);
			if (item) {
				this.show_detail_dialog(item);
			}
		});
	}

	load_settings_and_refresh() {
		frappe.db
			.get_single_value("POS Monitoring Settings", "gallery_default_period")
			.then((defaultPeriod) => {
				if (defaultPeriod && this.period_field) {
					this.period_field.set_value(defaultPeriod);
				}
				this.reload();
			})
			.catch(() => {
				this.reload();
			});
	}

	reload() {
		this.start = 0;
		this.items = [];
		this.$grid.empty();
		this.fetch(true);
	}

	get_filters() {
		const filters = {};
		const company = this.company_field?.get_value();
		if (company) filters.company = company;

		const pos_profile = this.pos_profile_field?.get_value();
		if (pos_profile) filters.pos_profile = pos_profile;

		const period = this.period_field?.get_value();
		if (period === "Custom") {
			const fromDate = this.from_date_field?.get_value();
			const toDate = this.to_date_field?.get_value();
			if (fromDate) filters.from_date = fromDate;
			if (toDate) filters.to_date = toDate;
		} else if (period) {
			const resolved = GALLERY_UTILS.resolvePeriod(period);
			if (resolved) {
				filters.from_date = resolved.from_date;
				filters.to_date = resolved.to_date;
			}
		}

		const category = this.category_field?.get_value();
		if (category) filters.category = category;

		const checkpoint = this.checkpoint_field?.get_value();
		if (checkpoint) filters.checkpoint = checkpoint;

		const assessment = this.assessment_field?.get_value();
		if (assessment) filters.assessment = assessment;

		const media_type = this.media_type_field?.get_value();
		if (media_type) filters.media_type = media_type;

		return filters;
	}

	fetch(force = false) {
		if (this.loading && !force) return;
		this.loading = true;
		const seq = ++this.request_seq;

		frappe.call({
			method: "pos_next.api.outlet_monitoring_review.get_gallery",
			args: {
				filters: this.get_filters(),
				start: this.start,
			},
			callback: (r) => {
				if (seq !== this.request_seq) return;
				this.loading = false;
				if (r.exc) return;
				const data = r.message || { items: [], has_more: false };
				const newItems = data.items || [];
				this.items = this.items.concat(newItems);
				this.has_more = Boolean(data.has_more);

				this.render_items(newItems);
			},
		});
	}

	fetch_more() {
		if (this.loading) return;
		this.start = this.items.length;
		this.fetch();
	}

	render_items(newItems) {
		if (this.items.length === 0) {
			this.$empty.show();
			this.$grid.hide();
			this.$loadMore.hide();
			return;
		}

		this.$empty.hide();
		this.$grid.show();

		const html = newItems
			.map((item) => GALLERY_UTILS.galleryCardHtml(item))
			.join("");
		this.$grid.append(html);

		if (this.has_more) {
			this.$loadMore.show();
		} else {
			this.$loadMore.hide();
		}
	}

	show_detail_dialog(item) {
		const isVideo = String(item.media_type || "").toLowerCase() === "video";
		const checkpointName = GALLERY_UTILS.escapeHtml(item.checkpoint_name || "");
		const report = GALLERY_UTILS.escapeHtml(item.report || "");
		const company = GALLERY_UTILS.escapeHtml(item.company || "");
		const profile = GALLERY_UTILS.escapeHtml(item.pos_profile || "");
		const operationalDate = GALLERY_UTILS.escapeHtml(item.operational_date || "");
		const uploadedAt = GALLERY_UTILS.escapeHtml(item.uploaded_at || "");
		const submittedBy = GALLERY_UTILS.escapeHtml(item.submitted_by || "");
		const assessment = GALLERY_UTILS.escapeHtml(item.assessment || "Pending");
		const tone = GALLERY_UTILS.assessmentTone(item.assessment);
		const fileUrl = GALLERY_UTILS.escapeHtml(item.file_url || "");

		let mediaEl = "";
		if (isVideo) {
			mediaEl = `
				<div class="gallery-dialog-media text-center mb-3">
					<video controls autoplay preload="metadata" src="${fileUrl}" style="max-width: 100%; max-height: 480px; border-radius: 6px; display: block; margin: 0 auto;"></video>
				</div>
			`;
		} else {
			mediaEl = `
				<div class="gallery-dialog-media text-center mb-3">
					<a href="${fileUrl}" target="_blank" rel="noopener noreferrer">
						<img src="${fileUrl}" style="max-width: 100%; max-height: 480px; object-fit: contain; border-radius: 6px; display: block; margin: 0 auto;" alt="${checkpointName}">
					</a>
				</div>
			`;
		}

		const bodyHtml = `
			<div class="outlet-gallery-detail">
				${mediaEl}
				<div class="gallery-dialog-meta p-2" style="background: #fafbfc; border-radius: 6px;">
					<div class="d-flex justify-content-between align-items-center mb-2">
						<h5 class="mb-0">${checkpointName}</h5>
						<span class="gallery-card-badge badge-${tone}">${assessment}</span>
					</div>
					<table class="table table-sm table-borderless mb-0">
						<tbody>
							<tr><td class="text-muted" style="width: 120px;">${__("Report")}</td><td><a href="/app/pos-monitoring-report/${encodeURIComponent(item.report)}">${report}</a></td></tr>
							<tr><td class="text-muted">${__("Company")}</td><td>${company}</td></tr>
							${profile ? `<tr><td class="text-muted">${__("POS Profile")}</td><td>${profile}</td></tr>` : ""}
							<tr><td class="text-muted">${__("Operational Date")}</td><td>${operationalDate}</td></tr>
							${uploadedAt ? `<tr><td class="text-muted">${__("Uploaded At")}</td><td>${uploadedAt}</td></tr>` : ""}
							${submittedBy ? `<tr><td class="text-muted">${__("Submitted By")}</td><td>${submittedBy}</td></tr>` : ""}
						</tbody>
					</table>
				</div>
			</div>
		`;

		const dialog = new frappe.ui.Dialog({
			title: `${checkpointName || __("Checkpoint")} - ${report}`,
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "detail_html",
					options: bodyHtml,
				},
			],
			primary_action_label: __("View Report"),
			primary_action: () => {
				dialog.hide();
				frappe.set_route("Form", "POS Monitoring Report", item.report);
			},
		});

		dialog.show();
	}
}
