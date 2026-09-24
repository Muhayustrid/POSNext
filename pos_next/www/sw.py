# Copyright (c) 2026, POS Next contributors
# License: MIT. See LICENSE

# COR-FE-05: serve the built PWA service worker at /sw.js with a
# "Service-Worker-Allowed: /" header so it can be registered with scope "/"
# and control the cashier page at /pos. The static file at
# /assets/pos_next/pos/sw.js can only ever claim its own directory scope.
#
# Pattern copied from frappe/www/website_script.py: a www/<name>.py controller
# colocated with a www/<name>.js Jinja template. TemplatePage resolves the
# route (StaticPage refuses .js files), the colocated module supplies
# get_context, and the response content type is derived from the .js path.
# Accessible to guests by default, like every www page.

import os

import frappe

base_template_path = "www/sw.js"

# The rendered SW changes on every build; never let the website renderer cache
# it in Redis (same reasoning as website_script.py).
no_cache = True

# The browser should revalidate the SW script on every update check instead of
# serving it from HTTP cache, so new deployments are picked up promptly
# (service worker update checks otherwise honor the cache for up to 24 hours).
cache_headers = {"Cache-Control": "no-cache"}


def get_context(context):
	# Never wrap the JS output in the HTML base template, even if a
	# base_template_map hook matches this path.
	context.base_template = None

	sw_path = os.path.join(frappe.get_app_path("pos_next"), "public", "pos", "sw.js")
	try:
		with open(sw_path, encoding="utf-8") as sw_file:
			context.service_worker_source = sw_file.read()
	except OSError:
		# No build yet: an explicit 404 beats a 500 error page for a script
		# URL (registration failure is already handled client-side).
		frappe.throw(frappe._("Service worker bundle not built yet"), frappe.NotFound)

	frappe.local.response_headers.update(cache_headers)
	# Required by navigator.serviceWorker.register("/sw.js", { scope: "/" });
	# without it the browser rejects any scope wider than the script directory.
	frappe.local.response_headers["Service-Worker-Allowed"] = "/"
