"""Versioned, verbatim Skill routing for staged model execution.

The source packs are snapshots, never summaries. A stage receives the root
Skill plus the exact original references required by that route. Long Vedic
Core documents are sliced only at original Markdown headings; their wording is
not rewritten.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = ROOT / "skill-packs" / "v1"
SKILL_PACK_VERSION = "skill-packs/v1"


@dataclass(frozen=True)
class RuleSource:
    path: str
    start_heading: str | None = None
    end_heading: str | None = None


@dataclass(frozen=True)
class SkillStage:
    id: str
    title: str
    refs: tuple[str, ...]
    rules: tuple[RuleSource, ...]
    coverage: tuple[str, ...]
    min_chars_per_coverage: int = 0
    # Some complete-Skill routes contain many peer entities (for example the
    # twelve Ziwei palaces). Keep the original rules intact, but let the
    # executor freeze small, independently auditable groups instead of asking
    # one model response to transcribe the whole chart at once.
    palace_names: tuple[str, ...] = ()


def _src(path: str, start: str | None = None, end: str | None = None) -> RuleSource:
    return RuleSource(path, start, end)


ZIWEI_ROOT = _src("ziwei-doushu/SKILL.md")
BAZI_ROOT = _src("ziping-bazi-analysis/SKILL.md")
BAZI_METHOD = _src("ziping-bazi-analysis/references/methodology.md")
VEDIC_ROOT = _src("vedic-astrology/SKILL.md")
VEDIC_COMMON = _src("vedic-astrology/resources/core.md", None, "## Step 0: 身份概览")


STAGES: dict[str, tuple[SkillStage, ...]] = {
    "ziwei": (
        SkillStage("input", "排盘与输入审计", ("/inputAudit", "/chart/soulPalaceEarthlyBranch", "/chart/bodyPalaceEarthlyBranch"),
                   (ZIWEI_ROOT, _src("ziwei-doushu/ETHICS.md")), ("输入时间", "命宫", "身宫", "五行局")),
        SkillStage("palaces-core", "命、财、官、迁四宫与三方四正", ("/chart/palaces",),
                   (ZIWEI_ROOT, _src("ziwei-doushu/references/shier-gong.md"), _src("ziwei-doushu/references/xingqing-mingli.md")),
                   ("命宫", "财帛宫", "官禄宫", "迁移宫"), palace_names=("命宫", "财帛宫", "官禄宫", "迁移宫")),
        SkillStage("palaces-relations", "兄、夫、子、友四宫与关系结构", ("/chart/palaces",),
                   (ZIWEI_ROOT, _src("ziwei-doushu/references/shier-gong.md"), _src("ziwei-doushu/references/xingqing-mingli.md")),
                   ("兄弟宫", "夫妻宫", "子女宫", "交友宫"), palace_names=("兄弟宫", "夫妻宫", "子女宫", "交友宫")),
        SkillStage("palaces-inner", "疾、田、福、父四宫与内在环境", ("/chart/palaces",),
                   (ZIWEI_ROOT, _src("ziwei-doushu/references/shier-gong.md"), _src("ziwei-doushu/references/xingqing-mingli.md")),
                   ("疾厄宫", "田宅宫", "福德宫", "父母宫"), palace_names=("疾厄宫", "田宅宫", "福德宫", "父母宫")),
        SkillStage("stars", "主星、辅煞、四化与格局", ("/chart/palaces",),
                   (ZIWEI_ROOT, _src("ziwei-doushu/references/shisi-zhuixing.md"), _src("ziwei-doushu/references/fuzhu-xing.md"),
                    _src("ziwei-doushu/references/sha-xing.md"), _src("ziwei-doushu/references/sihua.md"), _src("ziwei-doushu/references/geju.md")),
                   ("十四主星", "辅曜", "煞曜", "四化", "格局", "破格信号")),
        SkillStage("themes", "人格、事业财富、关系与健康专题", ("/chart/palaces",),
                   (ZIWEI_ROOT, _src("ziwei-doushu/references/hunyin.md"), _src("ziwei-doushu/references/shiye-caifu.md"),
                    _src("ziwei-doushu/references/jiankang.md"), _src("ziwei-doushu/references/xingqing-mingli.md")),
                   ("人格", "事业", "财富", "关系", "健康传统象意")),
        SkillStage("timing", "大限、流年与触发层", ("/chart/palaces",),
                   (ZIWEI_ROOT, _src("ziwei-doushu/references/daxian-liunian.md"), _src("ziwei-doushu/references/sihua.md")),
                   ("本命底盘", "大限", "流年", "反向时间信号")),
    ),
    "bazi": (
        SkillStage("input", "输入、历法与节气审计", ("/inputAudit", "/calendarRawData/surroundingMonthJie", "/calendarRawData/engineCrossCheck"),
                   (BAZI_ROOT, BAZI_METHOD), ("公历与时区", "节气边界", "时辰边界", "双引擎核验")),
        SkillStage("pillars", "四柱顺序与日主十神", ("/calendarRawData/pillars", "/calendarRawData/dayMaster"),
                   (BAZI_ROOT, BAZI_METHOD), ("年柱", "月柱", "日柱", "时柱", "日主")),
        SkillStage("strength", "旺衰、调候、格局与用神", ("/calendarRawData/pillars", "/calendarRawData/surroundingMonthJie"),
                   (BAZI_ROOT, BAZI_METHOD), ("月令", "根气", "生扶克泄耗", "调候", "格局", "用神", "反向信号")),
        SkillStage("interactions", "十神组合与刑冲合害", ("/calendarRawData/interactions", "/calendarRawData/pillars"),
                   (BAZI_ROOT, BAZI_METHOD), ("十神组合", "穿害", "冲", "合", "破", "刑", "合化条件")),
        SkillStage("blind", "病药、墓库、神煞与盲派补充", ("/calendarRawData/pillars", "/calendarRawData/interactions"),
                   (BAZI_ROOT, BAZI_METHOD), ("病", "药", "墓库", "神煞", "流派冲突")),
        SkillStage("health", "健康传统象意与日常照护", ("/calendarRawData/pillars", "/calendarRawData/interactions", "/calendarRawData/surroundingMonthJie"),
                   (BAZI_ROOT, BAZI_METHOD), ("健康传统象意", "生活情境", "日常照护")),
        SkillStage("timing", "大运、流年与原局触发", ("/calendarRawData/dayun", "/calendarRawData/timingRanges", "/calendarRawData/pillars"),
                   (BAZI_ROOT, BAZI_METHOD), ("原局", "大运", "流年", "用忌变化", "重复触发", "反向时间信号")),
    ),
    "vedic": (
        SkillStage("input", "Calculator：计算、校验与分盘稳定性", ("/input", "/inputAudit", "/validation", "/chart/divisional_boundary_audit"),
                   (VEDIC_ROOT, _src("vedic-astrology/resources/calculator.md")),
                   ("计算来源", "行星完整性", "Dasha完整性", "SAV校验", "分盘稳定性", "时间分辨率")),
        SkillStage("identity", "Core Step 0：身份概览", ("/chart/lagna", "/chart/planets", "/chart/dashas"),
                   (VEDIC_ROOT, VEDIC_COMMON, _src("vedic-astrology/resources/core.md", "## Step 0: 身份概览", "## Step 1: P1-P12行星审计")),
                   ("上升与命主星", "月亮", "当前MD/AD", "九星力量"), 300),
        SkillStage("planets-luminaries", "Core Step 1a：Sun、Moon、Mars 审计", ("/chart/planets", "/chart/dignity", "/chart/shadbala", "/chart/house_lords", "/chart/graha_drishti", "/chart/yoga_prescan", "/chart/parivartana", "/chart/pushkara"),
                   (VEDIC_ROOT, VEDIC_COMMON, _src("vedic-astrology/resources/core.md", "## Step 1: P1-P12行星审计", "## Step 2: 分盘交叉分析"),
                    _src("vedic-astrology/resources/p1_p12.md"), _src("vedic-astrology/resources/yogas.md")),
                   ("Sun", "Moon", "Mars"), 800),
        SkillStage("planets-benefics", "Core Step 1b：Mercury、Jupiter、Venus 审计", ("/chart/planets", "/chart/dignity", "/chart/shadbala", "/chart/house_lords", "/chart/graha_drishti", "/chart/yoga_prescan", "/chart/parivartana", "/chart/pushkara"),
                   (VEDIC_ROOT, VEDIC_COMMON, _src("vedic-astrology/resources/core.md", "## Step 1: P1-P12行星审计", "## Step 2: 分盘交叉分析"),
                    _src("vedic-astrology/resources/p1_p12.md"), _src("vedic-astrology/resources/yogas.md")),
                   ("Mercury", "Jupiter", "Venus"), 800),
        SkillStage("planets-nodes-yoga", "Core Step 1c：Saturn、节点与 Yoga 终判", ("/chart/planets", "/chart/dignity", "/chart/shadbala", "/chart/house_lords", "/chart/graha_drishti", "/chart/yoga_prescan", "/chart/parivartana", "/chart/pushkara"),
                   (VEDIC_ROOT, VEDIC_COMMON, _src("vedic-astrology/resources/core.md", "## Step 1: P1-P12行星审计", "## Step 2: 分盘交叉分析"),
                    _src("vedic-astrology/resources/p1_p12.md"), _src("vedic-astrology/resources/yogas.md")),
                   ("Saturn", "Rahu", "Ketu", "Yoga终判"), 800),
        SkillStage("divisional", "Core Step 2：D9、D10、D4、D5 分盘交叉", ("/chart/d9", "/chart/d10", "/chart/d4", "/chart/d5", "/chart/divisional_boundary_audit"),
                   (VEDIC_ROOT, VEDIC_COMMON, _src("vedic-astrology/resources/core.md", "## Step 2: 分盘交叉分析", "## ⏸️ 阶段1完成（自动暂停）"),
                    _src("vedic-astrology/resources/p1_p12.md"), _src("vedic-astrology/resources/house_framework.md")),
                   ("D9-Sun", "D9-Moon", "D9-Mars", "D9-Mercury", "D9-Jupiter", "D9-Venus", "D9-Saturn", "D9-Rahu", "D9-Ketu", "D10", "D4", "D5", "线A", "线B", "分盘反证"), 300),
        SkillStage("houses-1-4", "Core Step 3a：第一至四宫四维诊断", ("/chart/house_lords", "/chart/planets", "/chart/graha_drishti", "/chart/sav_by_house"),
                   (VEDIC_ROOT, VEDIC_COMMON, _src("vedic-astrology/resources/core.md", "## Step 3: 宫位诊断", "## Step 4: 十大板块总结"),
                    _src("vedic-astrology/resources/house_framework.md")),
                   tuple(f"{number}宫" for number in range(1, 5)), 500),
        SkillStage("houses-5-8", "Core Step 3b：第五至八宫四维诊断", ("/chart/house_lords", "/chart/planets", "/chart/graha_drishti", "/chart/sav_by_house"),
                   (VEDIC_ROOT, VEDIC_COMMON, _src("vedic-astrology/resources/core.md", "## Step 3: 宫位诊断", "## Step 4: 十大板块总结"),
                    _src("vedic-astrology/resources/house_framework.md")),
                   tuple(f"{number}宫" for number in range(5, 9)), 500),
        SkillStage("houses-9-12", "Core Step 3c：第九至十二宫四维诊断", ("/chart/house_lords", "/chart/planets", "/chart/graha_drishti", "/chart/sav_by_house"),
                   (VEDIC_ROOT, VEDIC_COMMON, _src("vedic-astrology/resources/core.md", "## Step 3: 宫位诊断", "## Step 4: 十大板块总结"),
                    _src("vedic-astrology/resources/house_framework.md")),
                   tuple(f"{number}宫" for number in range(9, 13)), 500),
        SkillStage("life", "Core Step 4：Dasha 与十大人生板块", ("/chart/dashas", "/chart/chara_dasha", "/chart/lagna", "/chart/planets", "/chart/house_lords"),
                   (VEDIC_ROOT, VEDIC_COMMON, _src("vedic-astrology/resources/core.md", "## Step 4: 十大板块总结", "## Step 5: 技术附录"),
                    _src("vedic-astrology/resources/house_framework.md"), _src("vedic-astrology/resources/yogas.md")),
                   ("人格核心", "财富潜力", "事业方向", "感情婚姻", "健康提醒", "教育学习", "家庭居住", "社交声誉", "灵性成长", "赛道优势"), 300),
        SkillStage("appendix", "Core Step 5：技术附录与五维可信度", ("/validation", "/chart/planets", "/chart/dashas", "/chart/divisional_boundary_audit"),
                   (VEDIC_ROOT, VEDIC_COMMON, _src("vedic-astrology/resources/core.md", "## Step 5: 技术附录", "## Q&A模式"),
                    _src("vedic-astrology/resources/report_rules.md")),
                   ("计算可信度", "结构解释可信度", "事件形态可信度", "时间分辨率", "外部验证状态")),
    ),
}


def stages_for(system: str) -> tuple[SkillStage, ...]:
    try:
        return STAGES[system]
    except KeyError as exc:
        raise ValueError(f"unknown Skill route: {system}") from exc


def _verbatim(source: RuleSource) -> str:
    path = PACK_ROOT / source.path
    text = path.read_text(encoding="utf-8")
    start = 0
    if source.start_heading is not None:
        index = text.find(source.start_heading)
        if index < 0:
            raise RuntimeError(f"missing start heading {source.start_heading!r} in {source.path}")
        start = index
    end = len(text)
    if source.end_heading is not None:
        index = text.find(source.end_heading, start + 1)
        if index < 0:
            raise RuntimeError(f"missing end heading {source.end_heading!r} in {source.path}")
        end = index
    return text[start:end].strip()


def stage_rule_bundle(system: str, stage: SkillStage) -> str:
    documents: list[str] = []
    for source in stage.rules:
        fragment = _verbatim(source)
        range_label = "full file" if source.start_heading is None and source.end_heading is None else "verbatim heading range"
        documents.append(f"\n===== ORIGINAL SOURCE: {SKILL_PACK_VERSION}/{source.path} ({range_label}) =====\n{fragment}")
    return "\n".join(documents)


def stage_manifest() -> dict[str, Any]:
    return {
        system: [
            {
                "id": stage.id,
                "title": stage.title,
                "coverage": list(stage.coverage),
                "minCharsPerCoverage": stage.min_chars_per_coverage,
                "sources": [source.path for source in stage.rules],
            }
            for stage in stages
        ]
        for system, stages in STAGES.items()
    }
