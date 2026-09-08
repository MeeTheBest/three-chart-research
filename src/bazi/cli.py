from __future__ import annotations

import argparse
import json
from datetime import datetime

from .engine import calculate_bazi_raw
from .time_normalizer import BirthTimeInput


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Calculate auditable Bazi Raw Data")
    parser.add_argument("--datetime", required=True, help="Local civil datetime, YYYY-MM-DDTHH:MM[:SS]")
    parser.add_argument("--gender", required=True, choices=("男", "女"))
    parser.add_argument("--timezone", required=True, help="IANA timezone, e.g. Asia/Hong_Kong")
    parser.add_argument("--location", default="")
    parser.add_argument("--latitude", type=float)
    parser.add_argument("--longitude", type=float)
    parser.add_argument("--time-standard", choices=("civil", "true_solar"), default="civil")
    parser.add_argument("--fold", type=int, choices=(0, 1))
    parser.add_argument("--day-boundary", choices=("late_zi", "midnight"), default="late_zi")
    parser.add_argument("--yun-method", choices=("minute", "traditional_shichen"), default="minute")
    return parser


def main() -> None:
    args = _parser().parse_args()
    birth = BirthTimeInput(
        local_datetime=datetime.fromisoformat(args.datetime),
        timezone=args.timezone,
        location=args.location,
        latitude=args.latitude,
        longitude=args.longitude,
        time_standard=args.time_standard,
        fold=args.fold,
    )
    result = calculate_bazi_raw(
        birth,
        args.gender,
        day_boundary=args.day_boundary,
        yun_method=args.yun_method,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
