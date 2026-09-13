from __future__ import annotations

import json
import unittest
from datetime import date, datetime

from src.bazi import BirthTimeInput, calculate_bazi_raw, normalize_birth_time


def birth(
    value: str,
    timezone: str = "Asia/Hong_Kong",
    **kwargs,
) -> BirthTimeInput:
    return BirthTimeInput(
        local_datetime=datetime.fromisoformat(value),
        timezone=timezone,
        **kwargs,
    )


class TimeNormalizationTests(unittest.TestCase):
    def test_summer_and_winter_use_actual_offset(self):
        for month, offset in ((7, -240), (1, -300)):
            n = normalize_birth_time(birth(f"2026-{month:02}-01T15:00:00", "America/New_York", longitude=-74, time_standard="true_solar"))
            audit = n.to_audit_dict()
            self.assertEqual(audit["utcOffsetMinutes"], offset)
            self.assertAlmostEqual(n.true_solar_correction_minutes, -296 - offset + audit["equationOfTimeMinutes"], places=5)

    def test_leap_day_fractional_zone_and_cross_date(self):
        n = normalize_birth_time(birth("2024-02-29T00:01:00", "Asia/Kolkata", longitude=70, time_standard="true_solar"))
        audit = n.to_audit_dict()
        self.assertEqual(audit["utcOffsetMinutes"], 330)
        self.assertEqual(n.calculation_local_datetime.date().isoformat(), "2024-02-28")
        self.assertTrue(audit["crossedCivilDate"])
        self.assertEqual(audit["originalLocalDatetime"], "2024-02-29T00:01:00")

    def test_same_instant_same_solar_time(self):
        a = normalize_birth_time(birth("2026-07-01T15:00:00", "America/New_York", longitude=-74, time_standard="true_solar"))
        b = normalize_birth_time(birth("2026-07-01T19:00:00", "UTC", longitude=-74, time_standard="true_solar"))
        self.assertEqual(a.calculation_local_datetime, b.calculation_local_datetime)
        self.assertEqual(a.utc_datetime, b.utc_datetime)

    def test_equation_sign_and_time_resolution(self):
        from src.bazi.time_normalizer import _equation_of_time_minutes
        from datetime import UTC
        feb = _equation_of_time_minutes(datetime(2026, 2, 11, tzinfo=UTC))
        nov = _equation_of_time_minutes(datetime(2026, 11, 3, tzinfo=UTC))
        self.assertTrue(-15 < feb < -13)
        self.assertTrue(15 < nov < 18)
        self.assertNotEqual(feb, _equation_of_time_minutes(datetime(2026, 2, 11, 12, tzinfo=UTC)))

    def test_invalid_longitude(self):
        for longitude in (181, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                normalize_birth_time(birth("2026-01-01T12:00:00", longitude=longitude, time_standard="true_solar"))

    def test_noaa_independent_approximation_over_leap_and_normal_year(self):
        # NOAA General Solar Position Calculations, solareqns.PDF.
        # A coarse independent sign/season check, NOT a seconds-level accuracy claim.
        from math import pi, sin, cos
        from datetime import UTC, timedelta
        from src.bazi.time_normalizer import _equation_of_time_minutes
        for year, days in ((2024, 366), (2026, 365)):
            for day in range(1, days + 1, 7):
                instant = datetime(year, 1, 1, 12, tzinfo=UTC) + timedelta(days=day - 1)
                gamma = 2 * pi / days * (day - 1)
                reference = 229.18 * (0.000075 + 0.001868*cos(gamma) - 0.032077*sin(gamma) - 0.014615*cos(2*gamma) - 0.040849*sin(2*gamma))
                self.assertLess(abs(_equation_of_time_minutes(instant) - reference), 1.0)

    def test_corrected_boundary_warning_does_not_mean_missing_input(self):
        from datetime import timedelta
        seed = normalize_birth_time(birth("2026-01-01T13:00:00", longitude=120, time_standard="true_solar"))
        adjusted = datetime(2026, 1, 1, 13) - timedelta(minutes=seed.true_solar_correction_minutes)
        n = normalize_birth_time(BirthTimeInput(adjusted, "Asia/Hong_Kong", longitude=120, time_standard="true_solar"))
        self.assertTrue(n.shichen_boundary_sensitive)
        self.assertLess(n.minutes_to_nearest_shichen_boundary, 2)

    def test_rejects_nonexistent_dst_time(self) -> None:
        with self.assertRaisesRegex(ValueError, "nonexistent local time"):
            normalize_birth_time(birth("2024-03-10T02:30:00", "America/New_York"))

    def test_requires_fold_for_ambiguous_dst_time(self) -> None:
        with self.assertRaisesRegex(ValueError, "ambiguous local time"):
            normalize_birth_time(birth("2024-11-03T01:30:00", "America/New_York"))

        first = normalize_birth_time(
            birth("2024-11-03T01:30:00", "America/New_York", fold=0)
        )
        second = normalize_birth_time(
            birth("2024-11-03T01:30:00", "America/New_York", fold=1)
        )
        self.assertNotEqual(first.utc_datetime, second.utc_datetime)

    def test_true_solar_time_requires_longitude(self) -> None:
        with self.assertRaisesRegex(ValueError, "longitude is required"):
            normalize_birth_time(
                birth("2000-08-16T03:00:00", time_standard="true_solar")
            )


class BaziEngineTests(unittest.TestCase):
    def test_documented_normal_case(self) -> None:
        result = calculate_bazi_raw(
            birth(
                "2000-08-16T03:00:00",
                location="香港",
                latitude=22.3193,
                longitude=114.1694,
            ),
            "女",
        )

        self.assertEqual(result["engine"]["version"], "1.4.8")
        self.assertEqual(
            result["calendarRawData"]["pillarsText"],
            "庚辰 甲申 丙午 庚寅",
        )
        self.assertEqual(
            result["calendarRawData"]["dayun"]["startReferenceDatetime"],
            "2003-06-25T23:00:00+08:00",
        )
        self.assertEqual(
            result["calendarRawData"]["dayun"]["steps"][0]["ganzhi"],
            "癸未",
        )
        json.dumps(result, ensure_ascii=False)

    def test_lichun_is_minute_precise(self) -> None:
        before = calculate_bazi_raw(birth("2024-02-04T16:20:00"), "男")
        after = calculate_bazi_raw(birth("2024-02-04T16:30:00"), "男")

        self.assertEqual(before["calendarRawData"]["pillarsText"], "癸卯 乙丑 戊戌 庚申")
        self.assertEqual(after["calendarRawData"]["pillarsText"], "甲辰 丙寅 戊戌 庚申")

    def test_same_lichun_instant_works_in_new_york(self) -> None:
        before = calculate_bazi_raw(
            birth("2024-02-04T03:20:00", "America/New_York", fold=0),
            "男",
        )
        after = calculate_bazi_raw(
            birth("2024-02-04T03:30:00", "America/New_York", fold=0),
            "男",
        )

        before_pillars = before["calendarRawData"]["pillars"]
        after_pillars = after["calendarRawData"]["pillars"]
        self.assertEqual([p["ganzhi"] for p in before_pillars[:2]], ["癸卯", "乙丑"])
        self.assertEqual([p["ganzhi"] for p in after_pillars[:2]], ["甲辰", "丙寅"])

    def test_day_boundary_profiles_preserve_the_conflict(self) -> None:
        source = birth("1990-01-01T23:30:00")
        late_zi = calculate_bazi_raw(source, "女", day_boundary="late_zi")
        midnight = calculate_bazi_raw(source, "女", day_boundary="midnight")

        self.assertEqual(late_zi["calendarRawData"]["pillars"][2]["ganzhi"], "丁卯")
        self.assertEqual(midnight["calendarRawData"]["pillars"][2]["ganzhi"], "丙寅")
        self.assertNotEqual(
            late_zi["calendarRawData"]["pillars"][2]["ganzhi"],
            midnight["calendarRawData"]["pillars"][2]["ganzhi"],
        )
        self.assertIn(
            "different day pillars",
            late_zi["inputAudit"]["warnings"][-1],
        )

    def test_theory_outputs_are_not_in_raw_data(self) -> None:
        result = calculate_bazi_raw(birth("2000-08-16T03:00:00"), "女")
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn('"dayMasterStrength":', serialized)
        self.assertNotIn('"yongshen":', serialized)
        self.assertIn("dayMasterStrength", result["analysisBoundary"]["excludedFromRawData"])

    def test_target_month_is_split_at_solar_term_boundary(self) -> None:
        dates = [date(2025, 5, day) for day in range(1, 32)]
        result = calculate_bazi_raw(
            birth("1982-06-12T06:50:00"),
            "男",
            target_dates=dates,
        )
        self.assertEqual(
            result["calendarRawData"]["timingRanges"],
            [
                {"startDate": "2025-05-01", "endDate": "2025-05-05", "yearGanzhi": "乙巳", "monthGanzhi": "庚辰"},
                {"startDate": "2025-05-06", "endDate": "2025-05-31", "yearGanzhi": "乙巳", "monthGanzhi": "辛巳"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
