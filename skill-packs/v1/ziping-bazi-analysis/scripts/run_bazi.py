#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
VENDOR_DIR = SCRIPT_DIR / "vendor"
if VENDOR_DIR.exists():
    sys.path.insert(0, str(VENDOR_DIR))

try:
    from bazi import analyze_from_chart, compute_bazi, print_from_chart
except ModuleNotFoundError as exc:
    if exc.name == "sxtwl":
        raise SystemExit(
            "Missing dependency sxtwl. Install it with:\n"
            f"{sys.executable} -m pip install --target {VENDOR_DIR} "
            f"-r {SCRIPT_DIR / 'requirements.txt'}"
        ) from exc
    raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calculate and report a Ziping Bazi chart.")
    parser.add_argument("--date", required=True, help="Gregorian birth date: YYYY-MM-DD")
    parser.add_argument("--time", required=True, help="Birth time in 24-hour form: HH:MM")
    parser.add_argument("--gender", required=True, choices=["男", "女"])
    parser.add_argument("--timezone", type=float, default=8.0, help="UTC offset, e.g. 8")
    parser.add_argument("--latitude", type=float, default=39.9)
    parser.add_argument("--longitude", type=float, default=116.4)
    parser.add_argument("--location", default="")
    parser.add_argument("--reference-date", help="Reference date for current luck cycle: YYYY-MM-DD")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    return parser.parse_args()


def json_default(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def main() -> None:
    args = parse_args()
    birth_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    birth_time = datetime.strptime(args.time, "%H:%M").time()
    reference_date = (
        datetime.strptime(args.reference_date, "%Y-%m-%d").date()
        if args.reference_date
        else None
    )
    chart = compute_bazi(
        birth_date.year,
        birth_date.month,
        birth_date.day,
        birth_time.hour,
        birth_time.minute,
        args.gender,
        args.timezone,
        args.latitude,
        args.longitude,
        args.location,
        reference_date,
    )

    if args.format == "json":
        payload = asdict(chart)
        payload["blind_school_analysis"] = analyze_from_chart(chart)
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=json_default))
        return

    pillars = " ".join(
        [
            chart.year_pillar.ganzhi,
            chart.month_pillar.ganzhi,
            chart.day_pillar.ganzhi,
            chart.hour_pillar.ganzhi,
        ]
    )
    print(f"【出生信息】{chart.birth_datetime_str} {chart.gender} {chart.location_name}")
    print(f"【八字】{pillars}")
    print(
        f"【日主】{chart.day_master}{chart.day_master_wuxing} "
        f"({chart.day_master_strength}，{chart.day_master_vitality})"
    )
    print(f"【格局】{chart.pattern}（{chart.pattern_type}）")
    print(
        f"【喜忌】用神 {chart.yongshen}；喜神 {chart.xishen}；"
        f"忌神 {chart.jishen}；调候 {chart.tiaohoushen}"
    )
    print(
        f"【大运】{chart.dayun_direction}，约 {chart.dayun_start_age:.2f} 岁起运；"
        f"当前流年 {chart.current_liunian_year} {chart.current_liunian}"
    )
    print("\n【子平计算解读】")
    print(chart.reading_zh)
    print("\n【盲派参考报告】")
    print(print_from_chart(chart))


if __name__ == "__main__":
    main()
