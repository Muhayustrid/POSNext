// Copyright (c) 2026, BrainWise and contributors
// For license information, please see license.txt

frappe.ui.form.on("POS Monitoring Report", {
	refresh(frm) {
		render_media_preview(frm);
		setup_review_buttons(frm);
		setup_label_query(frm);
	},
});

function setup_label_query(frm) {
	// Reviewer label choices come from the row snapshot via a server query,
	// which still lists labels that were disabled after the snapshot was taken
	// (the standard Link search hides disabled labels).
	frm.set_query("label", "checkpoints", (doc, cdt, cdn) => ({
		query: "pos_next.api.outlet_monitoring_review.label_options_query",
		filters: { report: doc.name, checkpoint_row: cdn },
	}));
}

function is_reviewer() {
	if (frappe.session.user === "Administrator") return true;
	if (frappe.user.has_role("System Manager")) return true;
	if (frappe.user.has_role("POSNext Monitoring Reviewer")) return true;
	return false;
}

function setup_review_buttons(frm) {
	if (!is_reviewer()) return;

	// "Finalize Review" button (Submitted, reviewer)
	if (frm.doc.status === "Submitted" && !frm.is_new()) {
		frm.add_custom_button(__("Finalize Review"), () => {
			const proceed = () => {
				frappe.call({
					method: "pos_next.api.outlet_monitoring_review.finalize_review",
					args: { name: frm.doc.name },
					freeze: true,
					freeze_message: __("Finalizing review..."),
					callback(r) {
						if (!r.exc) {
							frappe.show_alert({
								message: __("Review finalized"),
								indicator: "green",
							});
							frm.reload_doc();
						}
					},
				});
			};

			if (frm.is_dirty()) {
				frm.save("Update").then(proceed);
			} else {
				proceed();
			}
		}).addClass("btn-primary");
	}

	// "Waive" button (Submitted or Missed, reviewer)
	if (["Submitted", "Missed"].includes(frm.doc.status) && !frm.is_new()) {
		frm.add_custom_button(__("Waive"), () => {
			frappe.prompt(
				[
					{
						fieldname: "reason",
						fieldtype: "Small Text",
						label: __("Waive Reason"),
						reqd: 1,
					},
				],
				(values) => {
					frappe.call({
						method: "pos_next.api.outlet_monitoring_review.waive",
						args: {
							name: frm.doc.name,
							reason: values.reason,
						},
						freeze: true,
						freeze_message: __("Waiving report..."),
						callback(r) {
							if (!r.exc) {
								frappe.show_alert({
									message: __("Report waived"),
									indicator: "orange",
								});
								frm.reload_doc();
							}
						},
					});
				},
				__("Waive Monitoring Report"),
				__("Waive")
			);
		});
	}
}

function render_media_preview(frm) {
	const render = () => {
		const utils = window.outletMonitoringUtils;
		const html = utils
			? utils.mediaPreviewHtml(frm.doc.media || [])
			: `<div class="text-muted p-2">Loading preview...</div>`;

		let $target = frm.fields_dict.media_preview?.$wrapper;
		if (!$target || !$target.length) {
			if (frm.fields_dict.media?.$wrapper) {
				let $holder = frm.fields_dict.media.$wrapper.find(".outlet-monitoring-media-preview-container");
				if (!$holder.length) {
					$holder = $('<div class="outlet-monitoring-media-preview-container" style="margin-bottom: 15px;"></div>').prependTo(frm.fields_dict.media.$wrapper);
				}
				$target = $holder;
			}
		}

		if ($target && $target.length) {
			$target.html(html);
		}
	};

	if (window.outletMonitoringUtils) {
		render();
	} else {
		frappe.require("/assets/pos_next/js/outlet_monitoring_utils.js", () => {
			render();
		});
	}
}
