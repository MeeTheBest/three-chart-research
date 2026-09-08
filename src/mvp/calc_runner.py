"""One-shot deterministic calculator for the local website service.

The process receives one validated birth payload on stdin and never writes it to
disk.  The server starts this runner with the appropriate isolated Python
environment for Bazi or Vedic calculation.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
BAZI_SKILL_RUNNER = ROOT / "skill-packs" / "v1" / "ziping-bazi-analysis" / "scripts" / "run_bazi.py"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def payload() -> dict[str, Any]:
    value = json.load(sys.stdin)
    if not isinstance(value, dict):
        raise ValueError("request payload must be an object")
    return value


def _original_bazi_cross_check(value: dict[str, Any], primary: dict[str, Any]) -> dict[str, Any]:
    """Run the unmodified Skill calculator, but keep theory out of Raw Data.

    The original JSON also contains heuristic strength/pattern/yongshen and a
    blind-school report. Those are interpretive Theory outputs, so the outer
    research contract must not relabel them as deterministic Raw Data.
    """
    calculation = datetime.fromisoformat(primary["inputAudit"]["calculationLocalDatetime"])
    local = datetime.fromisoformat(f"{value['date']}T{value['time']}").replace(tzinfo=ZoneInfo(value["timezone"]))
    offset = local.utcoffset()
    if offset is None:
        raise ValueError("unable to resolve UTC offset for original Bazi Skill cross-check")
    command = [
        sys.executable, str(BAZI_SKILL_RUNNER),
        "--date", calculation.date().isoformat(),
        "--time", calculation.strftime("%H:%M"),
        "--gender", value["gender"],
        "--timezone", str(offset.total_seconds() / 3600),
        "--latitude", str(value.get("latitude") if value.get("latitude") is not None else 0),
        "--longitude", str(value.get("longitude") if value.get("longitude") is not None else 0),
        "--location", value["place"],
        "--format", "json",
    ]
    try:
        completed = subprocess.run(command, capture_output=True, check=True, timeout=45, cwd=ROOT)
        skill = json.loads(completed.stdout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        detail = getattr(exc, "stderr", b"")
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", "replace")
        return {
            "available": False,
            "source": "skill-packs/v1/ziping-bazi-analysis/scripts/run_bazi.py",
            "engine": "sxtwl",
            "issues": [f"原 Skill 排盘交叉核验未完成：{str(detail).strip()[-240:] or type(exc).__name__}"],
        }

    skill_pillars = " ".join(skill[f"{name}_pillar"]["stem"] + skill[f"{name}_pillar"]["branch"] for name in ("year", "month", "day", "hour"))
    primary_calendar = primary["calendarRawData"]
    primary_steps = [item["ganzhi"] for item in primary_calendar["dayun"]["steps"]]
    skill_steps = [item["ganzhi"] for item in skill.get("dayun_steps", [])]
    normalized_skill_direction = str(skill.get("dayun_direction", "")).translate(str.maketrans({"順": "顺", "逆": "逆"}))
    checks = {
        "pillars": skill_pillars == primary_calendar["pillarsText"],
        "dayunDirection": normalized_skill_direction == primary_calendar["dayun"]["direction"],
        "dayunSequence": skill_steps[: len(primary_steps)] == primary_steps[: len(skill_steps)],
    }
    issues: list[str] = []
    if not checks["pillars"]:
        issues.append("两套确定性引擎的四柱不一致，解释层不得继续给出确定结论。")
    if not checks["dayunDirection"] or not checks["dayunSequence"]:
        issues.append("两套引擎的大运方向或序列不一致，时间结论必须降级。")
    # The bundled Skill exposes both an age/year result and a field named
    # dayun_start_date. Keep them visible because they can contradict each
    # other; do not silently choose one.
    reported_start_year = skill.get("dayun_steps", [{}])[0].get("year_start") if skill.get("dayun_steps") else None
    if skill.get("dayun_start_date") and reported_start_year and not str(skill["dayun_start_date"]).startswith(str(reported_start_year)):
        issues.append("原 Skill 的 dayun_start_date 与其首步大运年份内部不一致；该日期字段不参与结论。")
    return {
        "available": True,
        "source": "skill-packs/v1/ziping-bazi-analysis/scripts/run_bazi.py",
        "engine": "sxtwl",
        "calculationDatetime": calculation.isoformat(timespec="minutes"),
        "pillarsText": skill_pillars,
        "dayunDirection": normalized_skill_direction,
        "dayunStartAge": skill.get("dayun_start_age"),
        "dayunStartDateReportedBySkill": skill.get("dayun_start_date"),
        "dayunSequence": skill_steps,
        "checks": checks,
        "issues": issues,
        "excludedInterpretiveFields": ["day_master_strength", "pattern", "yongshen", "blind_school_analysis"],
    }


def bazi(value: dict[str, Any]) -> dict[str, Any]:
    from src.bazi.engine import calculate_bazi_raw
    from src.bazi.time_normalizer import BirthTimeInput

    result = calculate_bazi_raw(
        BirthTimeInput(
            local_datetime=datetime.fromisoformat(f"{value['date']}T{value['time']}"),
            timezone=value["timezone"],
            location=value["place"],
            latitude=value.get("latitude"),
            longitude=value.get("longitude"),
            time_standard=value.get("baziTimeStandard", "civil"),
        ),
        value["gender"],
    )
    result["calendarRawData"]["engineCrossCheck"] = _original_bazi_cross_check(value, result)
    return result


def vedic(value: dict[str, Any]) -> dict[str, Any]:
    if value.get("latitude") is None or value.get("longitude") is None:
        raise ValueError("Vedic calculation requires city-reference latitude and longitude")
    from src.vedic.adapter import calculate_exact_raw

    return calculate_exact_raw(
        case_id=value["caseId"],
        birth_date=date.fromisoformat(value["date"]),
        birth_time=datetime.strptime(value["time"], "%H:%M").time(),
        timezone_name=value["timezone"],
        latitude=float(value["latitude"]),
        longitude=float(value["longitude"]),
        place_label=value["place"],
        gender=value["gender"],
        reference_date=date.today(),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("system", choices=("bazi", "vedic"))
    system = parser.parse_args().system
    result = bazi(payload()) if system == "bazi" else vedic(payload())
    json.dump(result, sys.stdout, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()
