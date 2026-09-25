# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""REL-06/07/08 regression: release hygiene guards (pure file checks, no DB).

- REL-06: pos_next/__init__.py, root package.json and POS/package.json must
  carry the same version, and that version must not be below the highest
  patch directory series (patches/v2_12_0 => 2.12.0).
- REL-07: pypdf must stay out of pyproject dependencies (0 imports).
- REL-08: translations/id.csv must not contain duplicate source keys (the
  trailing-space variants masked stale translations).

Run via pos_next/_pn_run_tests.py pos_next.tests.test_release_hygiene
"""

import csv
import json
import re
import unittest
from collections import Counter
from pathlib import Path

import frappe

APP_PATH = Path(frappe.get_app_path("pos_next"))

VERSION_RE = re.compile(r"^v(\d+)_(\d+)_(\d+)$")


def _parse_version(text):
	parts = text.strip().split(".")
	if len(parts) != 3 or not all(p.isdigit() for p in parts):
		raise AssertionError(f"unexpected version format: {text!r}")
	return tuple(int(p) for p in parts)


class TestReleaseHygiene(unittest.TestCase):
	def test_versions_are_synced_across_manifests(self):
		init_version = None
		init_file = (APP_PATH / "__init__.py").read_text(encoding="utf-8")
		match = re.search(r'^__version__\s*=\s*"([^"]+)"', init_file, re.M)
		if match:
			init_version = match.group(1)
		self.assertIsNotNone(init_version, "pos_next/__init__.py must define __version__")

		repo_root = APP_PATH.parent
		root_manifest = json.loads((repo_root / "package.json").read_text(encoding="utf-8"))
		pos_manifest = json.loads((repo_root / "POS" / "package.json").read_text(encoding="utf-8"))

		self.assertEqual(init_version, root_manifest.get("version"), "root package.json out of sync")
		self.assertEqual(init_version, pos_manifest.get("version"), "POS/package.json out of sync")

	def test_app_version_not_below_latest_patch_series(self):
		init_file = (APP_PATH / "__init__.py").read_text(encoding="utf-8")
		app_version = _parse_version(re.search(r'^__version__\s*=\s*"([^"]+)"', init_file, re.M).group(1))

		series = []
		for entry in (APP_PATH / "patches").iterdir():
			match = VERSION_RE.match(entry.name)
			if entry.is_dir() and match:
				series.append(tuple(int(g) for g in match.groups()))
		self.assertTrue(series, "no patch directories found")

		latest = max(series)
		self.assertGreaterEqual(
			app_version,
			latest,
			f"app version {app_version} is below patch series {latest}",
		)

	def test_pypdf_not_declared(self):
		pyproject = (APP_PATH.parent / "pyproject.toml").read_text(encoding="utf-8")
		self.assertNotRegex(pyproject, re.compile(r"^\s*\"pypdf", re.M), "pypdf dependency must stay removed")

	def test_translation_file_has_no_duplicate_keys(self):
		csv_path = APP_PATH / "translations" / "id.csv"
		with open(csv_path, newline="", encoding="utf-8") as handle:
			rows = list(csv.reader(handle))

		keys = [row[0] for row in rows[1:] if row and row[0]]
		duplicates = sorted(key for key, count in Counter(keys).items() if count > 1)
		self.assertEqual(
			duplicates,
			[],
			"duplicate translation keys (check trailing spaces before adding a 'fixed' copy)",
		)
