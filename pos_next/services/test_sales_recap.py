# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Unit tests for the pure hourly-bucket helper in sales_recap.

Mocked-frappe style — run via
pos_next/_pn_run_tests.py pos_next.services.test_sales_recap
"""

import datetime
import unittest

from pos_next.services.sales_recap import hourly_buckets


def _dt(*args):
	return datetime.datetime(*args)


class TestHourlyBuckets(unittest.TestCase):
	def test_day_shift_three_buckets_last_is_current(self):
		buckets = hourly_buckets(_dt(2026, 9, 20, 8, 0), _dt(2026, 9, 20, 10, 30))
		self.assertEqual(
			[b["start"] for b in buckets],
			[_dt(2026, 9, 20, 8, 0), _dt(2026, 9, 20, 9, 0), _dt(2026, 9, 20, 10, 0)],
		)
		self.assertEqual([b["is_current"] for b in buckets], [False, False, True])

	def test_midnight_crossing_chronological(self):
		buckets = hourly_buckets(_dt(2026, 9, 19, 22, 0), _dt(2026, 9, 20, 2, 15))
		self.assertEqual(len(buckets), 5)
		self.assertEqual(
			[b["start"] for b in buckets],
			[
				_dt(2026, 9, 19, 22, 0),
				_dt(2026, 9, 19, 23, 0),
				_dt(2026, 9, 20, 0, 0),
				_dt(2026, 9, 20, 1, 0),
				_dt(2026, 9, 20, 2, 0),
			],
		)
		self.assertFalse(any(b["is_current"] for b in buckets[:-1]))
		self.assertTrue(buckets[-1]["is_current"])

	def test_exact_hour_boundary_no_trailing_bucket(self):
		# 11:00 falls exactly on a boundary: the boundary hour is the current
		# (zero-elapsed) bucket and nothing trails it
		buckets = hourly_buckets(_dt(2026, 9, 20, 8, 0), _dt(2026, 9, 20, 11, 0))
		self.assertEqual(len(buckets), 4)
		self.assertEqual(buckets[-1]["start"], _dt(2026, 9, 20, 11, 0))
		self.assertTrue(buckets[-1]["is_current"])

	def test_long_shift_clamped_to_24_recent_buckets(self):
		buckets = hourly_buckets(_dt(2026, 9, 18, 8, 0), _dt(2026, 9, 19, 15, 30))
		self.assertEqual(len(buckets), 24)
		# most recent 24 kept: first bucket starts 8h in, last holds now
		self.assertEqual(buckets[0]["start"], _dt(2026, 9, 18, 16, 0))
		self.assertEqual(buckets[-1]["start"], _dt(2026, 9, 19, 15, 0))
		self.assertTrue(buckets[-1]["is_current"])
		self.assertFalse(any(b["is_current"] for b in buckets[:-1]))

	def test_just_opened_single_current_bucket(self):
		buckets = hourly_buckets(_dt(2026, 9, 20, 8, 0), _dt(2026, 9, 20, 8, 0))
		self.assertEqual(len(buckets), 1)
		self.assertEqual(buckets[0]["start"], _dt(2026, 9, 20, 8, 0))
		self.assertTrue(buckets[0]["is_current"])
