#!/usr/bin/env python3
"""BaziChart to blind school Bazi converter."""
from .calculator import BaziChart
from .blind_school import BlindSchoolBazi, Bazi as BsBazi, Pillar as BsPillar
from .blind_school import analyze_bazi_full as bs_analyze
from .blind_school import print_bazi_analysis as bs_print
def chart_to_blind_bazi(chart: BaziChart) -> BsBazi:
    """Convert a BaziChart (from calculator) to a blind school Bazi instance.
    Usage:
        chart = compute_bazi(...)
        bs_bazi = chart_to_blind_bazi(chart)
        bs = BlindSchoolBazi(bs_bazi)
        report = bs.full_report()
        # or just:
        report = analyze_from_chart(chart)
    """
    pillars = []
    for p in [chart.year_pillar, chart.month_pillar, chart.day_pillar, chart.hour_pillar]:
        pillars.append(BsPillar(p.ganzhi[0], p.ganzhi[1]))
    return BsBazi(*pillars)
def analyze_from_chart(chart: BaziChart) -> dict:
    """One-call: compute BaziChart -> blind school analysis."""
    bs_bazi = chart_to_blind_bazi(chart)
    return bs_analyze(bs_bazi)
def print_from_chart(chart: BaziChart) -> str:
    """One-call: compute BaziChart -> blind school text report."""
    import io
    import sys
    from contextlib import redirect_stdout
    bs_bazi = chart_to_blind_bazi(chart)
    buf = io.StringIO()
    with redirect_stdout(buf):
        bs_print(bs_bazi)
    return buf.getvalue()
