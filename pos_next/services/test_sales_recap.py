# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Unit tests for the pure bucket helpers in sales_recap.

Mocked-frappe style — run via
pos_next/_pn_run_tests.py pos_next.services.test_sales_recap
"""

import datetime
import unittest

from pos_next.services.sales_recap import hourly_buckets, period_buckets


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


class TestPeriodBuckets(unittest.TestCase):
	def test_single_day_gives_24_hour_buckets(self):
		buckets = period_buckets("2026-09-20", "2026-09-20", now=_dt(2026, 9, 20, 8, 30))
		self.assertEqual(len(buckets), 24)
		self.assertEqual(buckets[0]["start"], _dt(2026, 9, 20, 0, 0))
		self.assertEqual(buckets[-1]["start"], _dt(2026, 9, 20, 23, 0))
		# only the bucket holding 08:30 is current
		self.assertEqual(
			[b["is_current"] for b in buckets],
			[hour == 8 for hour in range(24)],
		)

	def test_seven_days_gives_seven_daily_buckets(self):
		buckets = period_buckets("2026-09-14", "2026-09-20", now=_dt(2026, 9, 17, 12, 0))
		self.assertEqual(len(buckets), 7)
		self.assertEqual(buckets[0]["start"], _dt(2026, 9, 14, 0, 0))
		self.assertEqual(buckets[-1]["start"], _dt(2026, 9, 20, 0, 0))
		self.assertEqual(
			[b["is_current"] for b in buckets],
			[day == 3 for day in range(7)],  # 17th is the 4th bucket
		)

	def test_over_62_days_gives_monthly_buckets_clamped_to_24(self):
		# Jan 2024 .. Sep 2026 = 33 months; only the most recent 24 survive
		buckets = period_buckets("2024-01-05", "2026-09-20", now=_dt(2026, 9, 20, 8, 0))
		self.assertEqual(len(buckets), 24)
		self.assertEqual(buckets[0]["start"], datetime.datetime(2024, 10, 1))
		self.assertEqual(buckets[-1]["start"], datetime.datetime(2026, 9, 1))
		self.assertTrue(buckets[-1]["is_current"])
		self.assertFalse(any(b["is_current"] for b in buckets[:-1]))

	def test_bucket_type_boundaries(self):
		# a 62-day window stays daily, 63 days flips to months
		daily = period_buckets("2026-01-01", "2026-03-04", now=_dt(2026, 2, 1))
		self.assertEqual(len(daily), 63)  # 62-day span, both ends inclusive
		self.assertEqual(daily[1]["start"] - daily[0]["start"], datetime.timedelta(days=1))
		monthly = period_buckets("2026-01-01", "2026-03-05", now=_dt(2026, 2, 1))
		self.assertEqual([b["start"] for b in monthly], [_dt(2026, 1, 1), _dt(2026, 2, 1), _dt(2026, 3, 1)])

	def test_past_window_has_no_current_bucket(self):
		buckets = period_buckets("2026-01-01", "2026-01-31", now=_dt(2026, 9, 20, 8, 0))
		self.assertEqual(len(buckets), 31)
		self.assertFalse(any(b["is_current"] for b in buckets))
