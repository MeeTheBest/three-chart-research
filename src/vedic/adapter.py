from __future__ import annotations

import importlib
import importlib.metadata
import json
import sys
import datetime as datetime_module
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

import pytz


SCHEMA_VERSION = "vedic.raw.v1"
DEFAULT_SKILL_DIR = Path(__file__).resolve().parents[2] / "skill-packs" / "v1" / "vedic-astrology"
SIGNS = [
    "Aries",
    "Taurus",
    "Gemini",
    "Cancer",
    "Leo",
    "Virgo",
    "Libra",
    "Scorpio",
    "Sagittarius",
    "Capricorn",
    "Aquarius",
    "Pisces",
]
PLANETS = ["Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn", "Rahu", "Ketu"]
DIVISION_KEYS = ("D1", "D4", "D5", "D9", "D10")


@dataclass(frozen=True)
class SkillModules:
    engine: Any
    formatter: Any


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def load_skill_modules(skill_dir: Path = DEFAULT_SKILL_DIR) -> SkillModules:
    scripts_dir = skill_dir / "scripts"
    if not scripts_dir.is_dir():
        raise FileNotFoundError(f"Vedic calculator scripts not found: {scripts_dir}")
    scripts_text = str(scripts_dir)
    if scripts_text not in sys.path:
        sys.path.insert(0, scripts_text)
    return SkillModules(
        engine=importlib.import_module("engine"),
        formatter=importlib.import_module("formatter"),
    )


def _localize(local_datetime: datetime, timezone_name: str) -> datetime:
    zone = pytz.timezone(timezone_name)
    try:
        return zone.localize(local_datetime, is_dst=None)
    except pytz.NonExistentTimeError as exc:
        raise ValueError(f"nonexistent local time caused by DST: {local_datetime} {timezone_name}") from exc
    except pytz.AmbiguousTimeError as exc:
        raise ValueError(f"ambiguous local time requires an explicit fold: {local_datetime} {timezone_name}") from exc


def _dasha_metrics(chart: dict[str, Any]) -> dict[str, Any]:
    dashas = chart.get("dashas", [])
    antardashas = [ad for md in dashas for ad in md.get("antardashas", [])]
    pratyantardashas = [
        pd
        for md in dashas
        for ad in md.get("antardashas", [])
        for pd in ad.get("pratyantardashas", [])
    ]

    continuous = True
    for md in dashas:
        ads = md.get("antardashas", [])
        continuous = continuous and bool(ads)
        continuous = continuous and all(
            ads[index].get("end_time") == ads[index + 1].get("start_time")
            for index in range(len(ads) - 1)
        )
        for ad in ads:
            pds = ad.get("pratyantardashas", [])
            continuous = continuous and bool(pds)
            continuous = continuous and all(
                pds[index].get("end_time") == pds[index + 1].get("start_time")
                for index in range(len(pds) - 1)
            )
            if pds:
                continuous = continuous and pds[0].get("start_time") == ad.get("start_time")
                continuous = continuous and pds[-1].get("end_time") == ad.get("end_time")

    return {
        "mahadashaCount": len(dashas),
        "antardashaCount": len(antardashas),
        "pratyantardashaCount": len(pratyantardashas),
        "continuousWithinParents": continuous,
    }


def validate_chart(chart: dict[str, Any]) -> dict[str, Any]:
    sav_total = sum(chart.get("sav", {}).get(sign, 0) for sign in SIGNS)
    planet_names = sorted(chart.get("planets", {}).keys())
    rahu = chart.get("planets", {}).get("Rahu", {}).get("longitude")
    ketu = chart.get("planets", {}).get("Ketu", {}).get("longitude")
    node_separation = None
    if rahu is not None and ketu is not None:
        node_separation = abs(float(rahu) - float(ketu))
        if node_separation > 180:
            node_separation = 360 - node_separation

    lagna = chart.get("lagna", {})
    dasha = _dasha_metrics(chart)
    checks = {
        "savTotal337": sav_total == 337,
        "nineGrahasPresent": planet_names == sorted(PLANETS),
        "lagnaValid": lagna.get("sign") in SIGNS and isinstance(lagna.get("longitude"), (int, float)),
        "rahuKetuOpposite": node_separation is not None and abs(node_separation - 180) < 0.01,
        "dashaShapeComplete": (
            dasha["mahadashaCount"],
            dasha["antardashaCount"],
            dasha["pratyantardashaCount"],
        )
        == (9, 81, 729),
        "dashaContinuousWithinParents": dasha["continuousWithinParents"],
        "divisionAuditAvailable": chart.get("divisional_boundary_audit", {}).get("status") == "ok",
    }
    return {
        "status": "pass" if all(checks.values()) else "fail",
        "checks": checks,
        "observed": {
            "savTotal": sav_total,
            "planetNames": planet_names,
            "lagnaSign": lagna.get("sign"),
            "rahuKetuSeparationDegrees": node_separation,
            "dasha": dasha,
        },
    }


def _engine_metadata(skill_dir: Path) -> dict[str, Any]:
    return {
        "adapterSchema": SCHEMA_VERSION,
        "calculator": "vedic-calculator",
        "calculatorVersion": "0.7",
        "skillPath": str(skill_dir),
        "pythonPackages": {
            "pyswisseph": _package_version("pyswisseph"),
            "PyJHora": _package_version("PyJHora"),
            "pytz": _package_version("pytz"),
        },
        "licenseNotice": "The local vedic-astrology calculator/PyJHora stack includes AGPL-3.0 components; review licensing before closed-source commercial deployment.",
    }


def _calculation_config() -> dict[str, Any]:
    return {
        "zodiac": "sidereal",
        "ayanamsa": "TRUE_CITRA",
        "nodeMode": "mean",
        "houseSystem": "whole-sign",
        "timeInterpretation": "local wall-clock time resolved through IANA timezone history",
        "runtimeTransitsIncluded": False,
    }


def _rule_profile() -> dict[str, Any]:
    """Expose the calculation settings under the cross-engine Raw contract."""
    return {
        "id": "vedic-sidereal-true-citra-mean-node-whole-sign-v1",
        **_calculation_config(),
    }


def _exact_input_audit(
    *, chart: dict[str, Any], validation: dict[str, Any], uncertainty_minutes: int
) -> dict[str, Any]:
    division_audit = chart.get("divisional_boundary_audit", {})
    division_stability = {
        key: bool(division_audit.get("charts", {}).get(key, {}).get("stable"))
        for key in DIVISION_KEYS
    }
    unstable = [key for key, stable in division_stability.items() if not stable]
    issues = [
        "Birthplace coordinates may be a city or regional reference point; they are not asserted facility coordinates."
    ]
    if unstable:
        issues.append(
            "The declared birth-time uncertainty changes these divisional ascendants: "
            + ", ".join(unstable)
        )
    return {
        "timezoneResolved": True,
        "timezoneAuthority": "IANA timezone history via pytz",
        "coordinatesUsed": True,
        "coordinatePrecision": "provided-reference-point",
        "birthTimeUncertaintyMinutes": uncertainty_minutes,
        "divisionStability": division_stability,
        "validationStatus": validation.get("status"),
        "canonicalChartAvailable": True,
        "issues": issues,
    }


def _range_input_audit(*, stability: dict[str, Any], minute_count: int) -> dict[str, Any]:
    unstable = [key for key, result in stability.items() if not result.get("stable")]
    issues = [
        "No canonical birth minute was selected from the supplied range.",
        "Birthplace coordinates may be a city or regional reference point; they are not asserted facility coordinates.",
    ]
    if unstable:
        issues.append("Time range changes these divisional ascendants: " + ", ".join(unstable))
    return {
        "timezoneResolved": True,
        "timezoneAuthority": "IANA timezone history via pytz",
        "coordinatesUsed": True,
        "coordinatePrecision": "provided-reference-point",
        "sampleStepMinutes": 1,
        "sampleCount": minute_count,
        "divisionStability": {
            key: bool(result.get("stable")) for key, result in stability.items()
        },
        "canonicalChartAvailable": False,
        "issues": issues,
    }


def _freeze_dasha_markers(chart: dict[str, Any], reference_date: date) -> None:
    reference = datetime.combine(reference_date, time(12, 0))
    for md in chart.get("dashas", []):
        md["is_current"] = False
        for ad in md.get("antardashas", []):
            ad_start = datetime.strptime(ad["start_time"], "%Y-%m-%d %H:%M")
            ad_end = datetime.strptime(ad["end_time"], "%Y-%m-%d %H:%M")
            ad["is_current"] = ad_start <= reference < ad_end
            md["is_current"] = md["is_current"] or ad["is_current"]
            for pd in ad.get("pratyantardashas", []):
                pd_start = datetime.strptime(pd["start_time"], "%Y-%m-%d %H:%M")
                pd_end = datetime.strptime(pd["end_time"], "%Y-%m-%d %H:%M")
                pd["is_current"] = pd_start <= reference < pd_end


@contextmanager
def _frozen_today(reference_date: date):
    original_date = datetime_module.date

    class FrozenDate(original_date):
        @classmethod
        def today(cls):
            return cls(reference_date.year, reference_date.month, reference_date.day)

    datetime_module.date = FrozenDate
    try:
        yield
    finally:
        datetime_module.date = original_date


def calculate_exact_raw(
    *,
    case_id: str,
    birth_date: date,
    birth_time: time,
    timezone_name: str,
    latitude: float,
    longitude: float,
    place_label: str,
    gender: str,
    uncertainty_minutes: int = 1,
    reference_date: date = date(2026, 8, 16),
    skill_dir: Path = DEFAULT_SKILL_DIR,
) -> dict[str, Any]:
    modules = load_skill_modules(skill_dir)
    local_naive = datetime.combine(birth_date, birth_time)
    localized = _localize(local_naive, timezone_name)
    chart = modules.engine.calculate_full_chart(
        year=birth_date.year,
        month=birth_date.month,
        day=birth_date.day,
        hour=birth_time.hour,
        minute=birth_time.minute,
        lat=latitude,
        lon=longitude,
        tz_str=timezone_name,
        uncertainty_minutes=uncertainty_minutes,
    )

    # The Skill engine also calculates "now" transits. They are deliberately
    # excluded from a frozen natal benchmark because they make identical input
    # produce different output on different run dates.
    natal_chart = deepcopy(chart)
    natal_chart.pop("transits", None)
    _freeze_dasha_markers(natal_chart, reference_date)
    validation = validate_chart(natal_chart)
    if validation["status"] != "pass":
        raise ValueError(f"Vedic chart validation failed for {case_id}: {validation['checks']}")

    return {
        "schemaVersion": SCHEMA_VERSION,
        "mode": "exact",
        "caseId": case_id,
        "input": {
            "gender": gender,
            "localDatetime": local_naive.isoformat(timespec="minutes"),
            "timezone": timezone_name,
            "utcDatetime": localized.astimezone(timezone.utc).isoformat(timespec="minutes").replace("+00:00", "Z"),
            "utcOffset": str(localized.utcoffset()),
            "dstOffset": str(localized.dst()),
            "place": place_label,
            "latitude": latitude,
            "longitude": longitude,
            "uncertaintyMinutes": uncertainty_minutes,
            "referenceDate": reference_date.isoformat(),
        },
        "engine": _engine_metadata(skill_dir),
        "configuration": _calculation_config(),
        "ruleProfile": _rule_profile(),
        "inputAudit": _exact_input_audit(
            chart=natal_chart,
            validation=validation,
            uncertainty_minutes=uncertainty_minutes,
        ),
        "validation": validation,
        "chart": natal_chart,
        "evidenceBoundary": {
            "layer": "Raw Data",
            "containsInterpretation": False,
            "answerKeyRead": False,
            "pollutionRisk": "P0 for computation; the separate contest question set remains P3",
        },
    }


def render_structured_data(raw: dict[str, Any], skill_dir: Path = DEFAULT_SKILL_DIR) -> str:
    if raw.get("mode") != "exact":
        raise ValueError("structured_data.md requires one exact chart; range scans have no canonical chart")
    modules = load_skill_modules(skill_dir)
    item = raw["input"]
    local_datetime = datetime.fromisoformat(item["localDatetime"])
    meta = {
        "dob": local_datetime.date().isoformat(),
        "time": local_datetime.strftime("%H:%M"),
        "place": item["place"],
        "lat": item["latitude"],
        "lon": item["longitude"],
        "time_precision": f"精确到分钟（按±{item['uncertaintyMinutes']}分钟审计）",
        "time_source": "大赛题面",
        "effective_precision": f"±{item['uncertaintyMinutes']}分钟",
    }
    user_info = {"gender": item["gender"], "relationship": "未提供"}
    reference_date = date.fromisoformat(item["referenceDate"])
    with _frozen_today(reference_date):
        rendered = modules.formatter.format_structured_data(raw["chart"], None, meta, user_info)
    return rendered.replace(
        "## 元信息\n",
        f"## 元信息\n\n> 冻结计算参考日：{reference_date.isoformat()}\n",
        1,
    )


def _parse_hhmm(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def _compress_samples(samples: list[dict[str, Any]], start_datetime: datetime) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for index, sample in enumerate(samples):
        signature = {key: sample.get(key) for key in DIVISION_KEYS}
        local_dt = start_datetime + timedelta(minutes=index)
        if not segments or segments[-1]["signature"] != signature:
            segments.append(
                {
                    "startLocalDatetime": local_dt.isoformat(timespec="minutes"),
                    "endLocalDatetime": local_dt.isoformat(timespec="minutes"),
                    "signature": signature,
                }
            )
        else:
            segments[-1]["endLocalDatetime"] = local_dt.isoformat(timespec="minutes")
    return segments


def scan_time_range(
    *,
    case_id: str,
    birth_date: date,
    start_time: time,
    end_time: time,
    timezone_name: str,
    latitude: float,
    longitude: float,
    place_label: str,
    gender: str,
    skill_dir: Path = DEFAULT_SKILL_DIR,
) -> dict[str, Any]:
    start_datetime = datetime.combine(birth_date, start_time)
    end_datetime = datetime.combine(birth_date, end_time)
    if end_datetime < start_datetime:
        end_datetime += timedelta(days=1)
    minute_count = int((end_datetime - start_datetime).total_seconds() // 60) + 1
    midpoint = start_datetime + timedelta(minutes=(minute_count - 1) // 2)
    span = max(
        int((midpoint - start_datetime).total_seconds() // 60),
        int((end_datetime - midpoint).total_seconds() // 60),
    )

    modules = load_skill_modules(skill_dir)
    audit = modules.engine.calc_divisional_boundary_audit(
        midpoint.year,
        midpoint.month,
        midpoint.day,
        midpoint.hour,
        midpoint.minute,
        latitude,
        longitude,
        timezone_name,
        uncertainty_minutes=span,
    )
    if audit.get("status") != "ok":
        raise ValueError(f"division time scan unavailable for {case_id}: {audit}")

    filtered: list[dict[str, Any]] = []
    for sample in audit["samples"]:
        local_dt = midpoint + timedelta(minutes=sample["offset_minutes"])
        if start_datetime <= local_dt <= end_datetime:
            filtered.append(sample)
    if len(filtered) != minute_count:
        raise ValueError(f"time scan sample mismatch for {case_id}: {len(filtered)} != {minute_count}")

    stability = {}
    for key in DIVISION_KEYS:
        observed = list(dict.fromkeys(sample.get(key) for sample in filtered if sample.get(key) is not None))
        stability[key] = {"stable": len(observed) == 1, "observedSigns": observed}

    start_aware = _localize(start_datetime, timezone_name)
    end_aware = _localize(end_datetime, timezone_name)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "mode": "time-range-sensitivity",
        "caseId": case_id,
        "input": {
            "gender": gender,
            "localDate": birth_date.isoformat(),
            "localTimeRange": [start_time.strftime("%H:%M"), end_time.strftime("%H:%M")],
            "timezone": timezone_name,
            "utcRange": [
                start_aware.astimezone(timezone.utc).isoformat(timespec="minutes").replace("+00:00", "Z"),
                end_aware.astimezone(timezone.utc).isoformat(timespec="minutes").replace("+00:00", "Z"),
            ],
            "place": place_label,
            "latitude": latitude,
            "longitude": longitude,
            "sampleStepMinutes": 1,
            "sampleCount": minute_count,
        },
        "engine": _engine_metadata(skill_dir),
        "configuration": _calculation_config(),
        "ruleProfile": _rule_profile(),
        "inputAudit": _range_input_audit(stability=stability, minute_count=minute_count),
        "stability": stability,
        "segments": _compress_samples(filtered, start_datetime),
        "canonicalChart": None,
        "integrationRule": "Do not select a single birth minute. Only use claims invariant across all relevant time segments; otherwise retain conditional branches or mark unable to judge.",
        "evidenceBoundary": {
            "layer": "Raw Data / time sensitivity",
            "containsInterpretation": False,
            "answerKeyRead": False,
            "pollutionRisk": "P0 for computation; the separate contest question set remains P3",
        },
    }


def dump_json(value: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
