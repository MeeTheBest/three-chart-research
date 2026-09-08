from __future__ import annotations

from datetime import date, datetime
from importlib.metadata import PackageNotFoundError, version
from typing import Literal
from zoneinfo import ZoneInfo

from lunar_python import Solar
from lunar_python.util import LunarUtil

from .time_normalizer import BirthTimeInput, NormalizedBirthTime, normalize_birth_time


DayBoundary = Literal["late_zi", "midnight"]
YunMethod = Literal["minute", "traditional_shichen"]

DAY_BOUNDARY_SECT: dict[DayBoundary, int] = {
    "late_zi": 1,
    "midnight": 2,
}
YUN_METHOD_SECT: dict[YunMethod, int] = {
    "traditional_shichen": 1,
    "minute": 2,
}


def _engine_version() -> str:
    try:
        return version("lunar_python")
    except PackageNotFoundError:
        return "unknown"


def _solar_from_datetime(value: datetime) -> Solar:
    return Solar.fromYmdHms(
        value.year,
        value.month,
        value.day,
        value.hour,
        value.minute,
        value.second,
    )


def _split_pillar(value: str) -> tuple[str, str]:
    if len(value) != 2:
        raise ValueError(f"unexpected pillar value: {value!r}")
    return value[0], value[1]


def _ten_god(day_stem: str, target_stem: str, is_day_master: bool = False) -> str:
    if is_day_master:
        return "日主"
    result = LunarUtil.SHI_SHEN.get(day_stem + target_stem)
    if result is None:
        raise ValueError(f"missing ten-god mapping: {day_stem}{target_stem}")
    return result


def _pillar_payload(label: str, ganzhi: str, day_stem: str) -> dict[str, object]:
    stem, branch = _split_pillar(ganzhi)
    hidden_stems = list(LunarUtil.ZHI_HIDE_GAN.get(branch, ()))
    return {
        "label": label,
        "ganzhi": ganzhi,
        "heavenlyStem": stem,
        "earthlyBranch": branch,
        "stemTenGod": _ten_god(day_stem, stem, label == "day"),
        "hiddenStems": [
            {
                "heavenlyStem": hidden,
                "tenGod": _ten_god(day_stem, hidden),
            }
            for hidden in hidden_stems
        ],
    }


def _solar_to_cst_datetime(solar: Solar) -> datetime:
    return datetime(
        solar.getYear(),
        solar.getMonth(),
        solar.getDay(),
        solar.getHour(),
        solar.getMinute(),
        solar.getSecond(),
        tzinfo=ZoneInfo("Asia/Shanghai"),
    )


def _jie_payload(jie, display_timezone: str) -> dict[str, str]:
    cst = _solar_to_cst_datetime(jie.getSolar())
    local = cst.astimezone(ZoneInfo(display_timezone))
    return {
        "name": jie.getName(),
        "referenceDatetime": cst.isoformat(timespec="seconds"),
        "utcDatetime": cst.astimezone(ZoneInfo("UTC")).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "birthplaceDatetime": local.isoformat(timespec="seconds"),
    }


_LIUHE = {frozenset(pair) for pair in ("子丑", "寅亥", "卯戌", "辰酉", "巳申", "午未")}
_LIUCHONG = {frozenset(pair) for pair in ("子午", "丑未", "寅申", "卯酉", "辰戌", "巳亥")}
_LIUHAI = {frozenset(pair) for pair in ("子未", "丑午", "寅巳", "卯辰", "申亥", "酉戌")}
_LIUPO = {frozenset(pair) for pair in ("子酉", "卯午", "辰丑", "未戌", "寅亥", "巳申")}
_SANHE = {
    frozenset("申子辰"): "水",
    frozenset("亥卯未"): "木",
    frozenset("寅午戌"): "火",
    frozenset("巳酉丑"): "金",
}
_SANHUI = {
    frozenset("亥子丑"): "水",
    frozenset("寅卯辰"): "木",
    frozenset("巳午未"): "火",
    frozenset("申酉戌"): "金",
}


def _mechanical_interactions(pillars: list[dict[str, object]]) -> dict[str, object]:
    pair_results: list[dict[str, object]] = []
    for left_index, left in enumerate(pillars):
        for right in pillars[left_index + 1 :]:
            branches = frozenset((left["earthlyBranch"], right["earthlyBranch"]))
            types: list[str] = []
            if branches in _LIUHE:
                types.append("六合")
            if branches in _LIUCHONG:
                types.append("六冲")
            if branches in _LIUHAI:
                types.append("六害")
            if branches in _LIUPO:
                types.append("六破")
            if types:
                pair_results.append(
                    {
                        "pillars": [left["label"], right["label"]],
                        "branches": [left["earthlyBranch"], right["earthlyBranch"]],
                        "types": types,
                    }
                )

    branch_set = frozenset(pillar["earthlyBranch"] for pillar in pillars)
    triples: list[dict[str, str]] = []
    for branches, element in _SANHE.items():
        if branches.issubset(branch_set):
            triples.append({"type": "三合", "branches": "".join(sorted(branches)), "element": element})
    for branches, element in _SANHUI.items():
        if branches.issubset(branch_set):
            triples.append({"type": "三会", "branches": "".join(sorted(branches)), "element": element})

    return {
        "pairInteractions": pair_results,
        "completeTripleInteractions": triples,
        "scopeNote": "Mechanical enumeration only; transformation success and interpretive priority belong to the Theory layer.",
    }


def _dayun_payload(term_eight_char, gender: str, method: YunMethod, display_timezone: str) -> dict[str, object]:
    gender_code = 1 if gender == "男" else 0
    yun = term_eight_char.getYun(gender_code, YUN_METHOD_SECT[method])
    start_reference = _solar_to_cst_datetime(yun.getStartSolar())
    start_local = start_reference.astimezone(ZoneInfo(display_timezone))
    steps = []
    for item in yun.getDaYun(11):
        ganzhi = item.getGanZhi()
        if not ganzhi:
            continue
        steps.append(
            {
                "ganzhi": ganzhi,
                "startYear": item.getStartYear(),
                "endYear": item.getEndYear(),
                "startNominalAge": item.getStartAge(),
                "endNominalAge": item.getEndAge(),
            }
        )

    return {
        "direction": "顺行" if yun.isForward() else "逆行",
        "method": method,
        "startOffset": {
            "years": yun.getStartYear(),
            "months": yun.getStartMonth(),
            "days": yun.getStartDay(),
            "hours": yun.getStartHour(),
        },
        "startReferenceDatetime": start_reference.isoformat(timespec="seconds"),
        "startBirthplaceDatetime": start_local.isoformat(timespec="seconds"),
        "steps": steps,
    }


def _timing_ranges(target_dates: list[date]) -> list[dict[str, str]]:
    ranges: list[dict[str, str]] = []
    for target_date in sorted(set(target_dates)):
        solar = Solar.fromYmdHms(target_date.year, target_date.month, target_date.day, 12, 0, 0)
        eight_char = solar.getLunar().getEightChar()
        payload = {
            "startDate": target_date.isoformat(),
            "endDate": target_date.isoformat(),
            "yearGanzhi": eight_char.getYear(),
            "monthGanzhi": eight_char.getMonth(),
        }
        if ranges and all(
            ranges[-1][key] == payload[key] for key in ("yearGanzhi", "monthGanzhi")
        ):
            ranges[-1]["endDate"] = payload["endDate"]
        else:
            ranges.append(payload)
    return ranges


def calculate_bazi_raw(
    birth: BirthTimeInput,
    gender: str,
    *,
    day_boundary: DayBoundary = "late_zi",
    yun_method: YunMethod = "minute",
    target_dates: list[date] | None = None,
) -> dict[str, object]:
    """Calculate JSON-safe Bazi Raw Data without strength/pattern/yongshen claims."""

    if gender not in ("男", "女"):
        raise ValueError("gender must be '男' or '女'")
    if day_boundary not in DAY_BOUNDARY_SECT:
        raise ValueError("day_boundary must be 'late_zi' or 'midnight'")
    if yun_method not in YUN_METHOD_SECT:
        raise ValueError("yun_method must be 'minute' or 'traditional_shichen'")

    normalized: NormalizedBirthTime = normalize_birth_time(birth)

    # lunar-python stores solar-term timestamps in China Standard Time. Use the
    # same instant in that reference zone for year/month and Yun calculations.
    term_solar = _solar_from_datetime(normalized.term_reference_datetime)
    term_lunar = term_solar.getLunar()
    term_eight_char = term_lunar.getEightChar()

    # Day/hour follow the explicitly selected birthplace clock and day boundary.
    local_solar = _solar_from_datetime(normalized.calculation_local_datetime)
    local_lunar = local_solar.getLunar()
    local_eight_char = local_lunar.getEightChar()
    local_eight_char.setSect(DAY_BOUNDARY_SECT[day_boundary])

    ganzhi_values = {
        "year": term_eight_char.getYear(),
        "month": term_eight_char.getMonth(),
        "day": local_eight_char.getDay(),
        "hour": local_eight_char.getTime(),
    }
    day_stem = ganzhi_values["day"][0]
    pillars = [
        _pillar_payload(label, ganzhi_values[label], day_stem)
        for label in ("year", "month", "day", "hour")
    ]

    warnings = list(normalized.warnings)
    if normalized.original.timezone != "Asia/Shanghai":
        warnings.append(
            "Year/month use the birth instant converted to lunar-python's Asia/Shanghai solar-term reference; day/hour use the selected birthplace clock."
        )
    if normalized.calculation_local_datetime.hour == 23:
        warnings.append("The two supported day-boundary rules produce different day pillars at 23:00-23:59.")

    return {
        "schemaVersion": "bazi.raw.v1",
        "engine": {
            "name": "lunar-python",
            "version": _engine_version(),
            "role": "deterministic-calendar-engine",
        },
        "ruleProfile": {
            "dayBoundary": day_boundary,
            "dayBoundarySect": DAY_BOUNDARY_SECT[day_boundary],
            "yunMethod": yun_method,
            "yunMethodSect": YUN_METHOD_SECT[yun_method],
            "timeStandard": normalized.original.time_standard,
            "solarTermReferenceTimezone": "Asia/Shanghai",
        },
        "inputAudit": {
            **normalized.to_audit_dict(),
            "gender": gender,
            "warnings": warnings,
        },
        "calendarRawData": {
            "pillarsText": " ".join(ganzhi_values[label] for label in ("year", "month", "day", "hour")),
            "dayMaster": day_stem,
            "pillars": pillars,
            "lunarDateAtBirthplaceClock": local_lunar.toString(),
            "surroundingMonthJie": {
                "previous": _jie_payload(term_lunar.getPrevJie(), birth.timezone),
                "next": _jie_payload(term_lunar.getNextJie(), birth.timezone),
            },
            "interactions": _mechanical_interactions(pillars),
            "dayun": _dayun_payload(term_eight_char, gender, yun_method, birth.timezone),
            "timingRanges": _timing_ranges(target_dates or []),
        },
        "analysisBoundary": {
            "excludedFromRawData": ["dayMasterStrength", "pattern", "yongshen", "xishen", "jishen", "eventPredictions"],
            "reason": "These are school-dependent Theory/Inference outputs, not calendar facts.",
        },
    }
