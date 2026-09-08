from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import cos, pi, sin
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


TimeStandard = Literal["civil", "true_solar"]
TERM_REFERENCE_TIMEZONE = "Asia/Shanghai"


@dataclass(frozen=True)
class BirthTimeInput:
    """A wall-clock birth time plus the rules needed to resolve it."""

    local_datetime: datetime
    timezone: str
    location: str = ""
    latitude: float | None = None
    longitude: float | None = None
    time_standard: TimeStandard = "civil"
    fold: int | None = None


@dataclass(frozen=True)
class NormalizedBirthTime:
    original: BirthTimeInput
    local_aware_datetime: datetime
    utc_datetime: datetime
    calculation_local_datetime: datetime
    term_reference_datetime: datetime
    true_solar_correction_minutes: float
    minutes_to_nearest_shichen_boundary: int
    shichen_boundary_sensitive: bool
    warnings: tuple[str, ...]

    def to_audit_dict(self) -> dict[str, object]:
        return {
            "originalLocalDatetime": self.original.local_datetime.isoformat(timespec="seconds"),
            "timezone": self.original.timezone,
            "location": self.original.location or None,
            "latitude": self.original.latitude,
            "longitude": self.original.longitude,
            "timeStandard": self.original.time_standard,
            "dstFold": self.original.fold,
            "resolvedLocalDatetime": self.local_aware_datetime.isoformat(timespec="seconds"),
            "utcDatetime": self.utc_datetime.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "calculationLocalDatetime": self.calculation_local_datetime.isoformat(timespec="seconds"),
            "termReferenceDatetime": self.term_reference_datetime.isoformat(timespec="seconds"),
            "termReferenceTimezone": TERM_REFERENCE_TIMEZONE,
            "trueSolarCorrectionMinutes": round(self.true_solar_correction_minutes, 3),
            "minutesToNearestShichenBoundary": self.minutes_to_nearest_shichen_boundary,
            "shichenBoundarySensitive": self.shichen_boundary_sensitive,
            "warnings": list(self.warnings),
        }


def _resolve_local_datetime(value: datetime, timezone: str, fold: int | None) -> datetime:
    if value.tzinfo is not None:
        raise ValueError("local_datetime must be timezone-naive; pass timezone separately")
    if fold not in (None, 0, 1):
        raise ValueError("fold must be 0, 1, or omitted")

    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown IANA timezone: {timezone}") from exc

    candidates: list[datetime] = []
    for candidate_fold in (0, 1):
        aware = value.replace(tzinfo=zone, fold=candidate_fold)
        round_trip = aware.astimezone(UTC).astimezone(zone)
        if round_trip.replace(tzinfo=None) == value:
            candidates.append(aware)

    unique: dict[tuple[object, object], datetime] = {
        (candidate.utcoffset(), candidate.dst()): candidate for candidate in candidates
    }
    candidates = list(unique.values())

    if not candidates:
        raise ValueError(
            f"nonexistent local time caused by a timezone transition: {value.isoformat()} {timezone}"
        )
    if len(candidates) > 1 and fold is None:
        raise ValueError(
            f"ambiguous local time; pass fold=0 or fold=1: {value.isoformat()} {timezone}"
        )

    selected_fold = 0 if fold is None else fold
    for candidate in candidates:
        if candidate.fold == selected_fold:
            return candidate
    return candidates[0]


def _equation_of_time_minutes(day_of_year: int) -> float:
    """Approximate equation of time; sufficient for boundary sensitivity scans."""

    angle = 2 * pi * (day_of_year - 81) / 364
    return 9.87 * sin(2 * angle) - 7.53 * cos(angle) - 1.5 * sin(angle)


def _true_solar_correction_minutes(aware: datetime, longitude: float) -> float:
    dst = aware.dst() or timedelta(0)
    standard_offset = (aware.utcoffset() - dst).total_seconds() / 3600
    standard_meridian = standard_offset * 15
    longitude_correction = 4 * (longitude - standard_meridian)
    equation_of_time = _equation_of_time_minutes(aware.timetuple().tm_yday)
    return longitude_correction + equation_of_time


def _boundary_distance_minutes(value: datetime) -> int:
    minute_of_day = value.hour * 60 + value.minute
    boundaries = [0, 60, 180, 300, 420, 540, 660, 780, 900, 1020, 1140, 1260, 1380, 1440]
    return min(abs(minute_of_day - boundary) for boundary in boundaries)


def normalize_birth_time(value: BirthTimeInput, sensitivity_minutes: int = 15) -> NormalizedBirthTime:
    if value.time_standard not in ("civil", "true_solar"):
        raise ValueError("time_standard must be 'civil' or 'true_solar'")

    aware = _resolve_local_datetime(value.local_datetime, value.timezone, value.fold)
    utc_datetime = aware.astimezone(UTC)
    calculation_datetime = value.local_datetime
    correction = 0.0
    warnings: list[str] = []

    if value.time_standard == "true_solar":
        if value.longitude is None:
            raise ValueError("longitude is required when time_standard='true_solar'")
        correction = _true_solar_correction_minutes(aware, value.longitude)
        calculation_datetime = value.local_datetime + timedelta(minutes=correction)
        warnings.append(
            "True solar time uses an approximate equation-of-time formula; retain the civil-time chart as a comparison."
        )

    distance = _boundary_distance_minutes(calculation_datetime)
    sensitive = distance <= sensitivity_minutes
    if sensitive:
        warnings.append(
            "Calculation time is close to a shichen boundary; compare the adjacent-hour chart."
        )

    if calculation_datetime.date() != value.local_datetime.date():
        warnings.append("Time normalization crossed a civil-date boundary.")

    term_reference = utc_datetime.astimezone(ZoneInfo(TERM_REFERENCE_TIMEZONE))
    return NormalizedBirthTime(
        original=value,
        local_aware_datetime=aware,
        utc_datetime=utc_datetime,
        calculation_local_datetime=calculation_datetime,
        term_reference_datetime=term_reference,
        true_solar_correction_minutes=correction,
        minutes_to_nearest_shichen_boundary=distance,
        shichen_boundary_sensitive=sensitive,
        warnings=tuple(warnings),
    )

