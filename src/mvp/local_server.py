"""Deterministic calculators and complete, resumable Skill analysis workflows."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import shutil
import hashlib
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import urlencode

from pypinyin import Style, lazy_pinyin

from src.mvp.audit_log import LocalAuditLog
from src.mvp.session_store import BirthInput, EphemeralSessionStore, SessionNotFound, SYSTEMS
from src.mvp.skill_router import SKILL_PACK_VERSION, SkillStage, stage_rule_bundle, stages_for
from src.mvp.timezone_lookup import resolve_domestic_reference
from src.research.validator import ValidationIssue, validate_analysis


ROOT = Path(__file__).resolve().parents[2]
BAZI_PYTHON = Path(os.environ.get("BAZI_PYTHON", sys.executable))
VEDIC_PYTHON = Path(os.environ.get("VEDIC_PYTHON", sys.executable))
NODE = Path(os.environ.get("NODE_EXECUTABLE") or shutil.which("node") or "node")
CALC_RUNNER = ROOT / "src" / "mvp" / "calc_runner.py"
ZIWEI_RUNNER = ROOT / "src" / "mvp" / "ziwei_runner.mjs"
SCHEMA_PATH = ROOT / "schemas" / "research-analysis.v1.schema.json"
PROMPTS = ROOT / "prompts"
# Qwen is used through Model Studio's OpenAI-compatible Chat Completions API.
# The endpoint can be overridden for a regional or workspace-specific gateway.
MODEL = os.environ.get("QWEN_MODEL", "qwen3.7-max-2026-06-08")
API_URL = os.environ.get("QWEN_API_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")
# Full original Skill stages can legitimately need long, structured answers.
# This is an output ceiling, not a prompt-compression setting.
MAX_OUTPUT_TOKENS = int(os.environ.get("QWEN_MAX_OUTPUT_TOKENS", "16000"))
QWEN_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("QWEN_REQUEST_TIMEOUT_SECONDS", "900"))
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
GEOCODING_TIMEOUT_SECONDS = float(os.environ.get("GEOCODING_TIMEOUT_SECONDS", "3"))
GEOCODING_RETRY_DELAY_SECONDS = float(os.environ.get("GEOCODING_RETRY_DELAY_SECONDS", "0.5"))
GEOCODING_MAX_ATTEMPTS = 2
LOCAL_AUDIT_RETENTION = timedelta(hours=int(os.environ.get("LOCAL_AUDIT_RETENTION_HOURS", "24")))
LOCAL_AUDIT_PATH = ROOT / "data" / "local-audit" / "research-audit.jsonl"

# Stable cross-system taxonomy.  The model fills these slots; it never decides
# how many conclusions exist or which claims are comparable.
CLAIM_GROUPS: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...] = (
    ("personality", "人格核心", (("expression", "外在表达"), ("motivation", "内在驱动"), ("decision_style", "决策方式"), ("stress_response", "压力反应"))),
    ("career", "职业工作", (("strengths", "能力优势"), ("work_content", "适合的工作内容"), ("environment", "适合的组织环境"), ("obstacles", "职业阻力"), ("development", "发展方式"))),
    ("wealth", "财富资源", (("income", "收入方式"), ("accumulation", "积累模式"), ("risk", "风险倾向"), ("volatility", "财富波动条件"))),
    ("relationship", "关系互动", (("needs", "情感需求"), ("interaction", "互动方式"), ("conflict", "冲突模式"), ("stability", "稳定条件"))),
    ("learning", "学习成长", (("method", "学习方式"), ("advantages", "优势领域"), ("obstacles", "成长阻力"))),
    ("family", "家庭居住", (("interaction", "家庭互动"), ("resources", "家庭资源"), ("mobility", "迁移与居住"))),
    ("social", "社交声誉", (("style", "社交模式"), ("reputation", "公众形象"))),
    ("health", "健康提醒", (("signals", "传统象意提示"), ("contexts", "生活情境留意"), ("care", "日常照护建议"))),
    ("timing", "阶段趋势", (("current", "当前阶段"), ("window", "后续窗口"), ("sensitivity", "敏感条件"))),
)

# Deterministic topic routing. The model no longer has to discover which
# frozen stages are legal evidence for a user-facing module, which previously
# caused complete relationship/timing sections to disappear because of a bad
# audit id rather than weak chart evidence.
CLAIM_STAGE_ROUTES: dict[str, dict[str, tuple[str, ...]]] = {
    "ziwei": {
        "personality": ("input", "palaces-core", "stars", "themes"),
        "career": ("palaces-core", "stars", "themes", "timing"),
        "wealth": ("palaces-core", "stars", "themes", "timing"),
        "relationship": ("palaces-relations", "stars", "themes", "timing"),
        "learning": ("palaces-inner", "stars", "themes"),
        "family": ("palaces-relations", "palaces-inner", "themes"),
        "social": ("palaces-relations", "palaces-core", "themes"),
        "health": ("palaces-inner", "stars", "themes"),
        "timing": ("timing",),
    },
    "bazi": {
        "personality": ("pillars", "strength", "interactions", "blind"),
        "career": ("strength", "interactions", "blind", "timing"),
        "wealth": ("strength", "interactions", "blind", "timing"),
        "relationship": ("pillars", "interactions", "blind", "timing"),
        "learning": ("pillars", "strength", "blind"),
        "family": ("pillars", "interactions", "blind"),
        "social": ("interactions", "blind"),
        "health": ("health", "strength", "blind"),
        "timing": ("timing", "strength", "interactions"),
    },
    "vedic": {
        "personality": ("identity", "planets-luminaries", "planets-benefics", "planets-nodes-yoga", "houses-1-4", "life"),
        "career": ("divisional", "houses-9-12", "life", "identity"),
        "wealth": ("divisional", "houses-1-4", "houses-9-12", "life"),
        "relationship": ("divisional", "houses-5-8", "life"),
        "learning": ("houses-1-4", "houses-5-8", "houses-9-12", "life"),
        "family": ("houses-1-4", "divisional", "life"),
        "social": ("houses-9-12", "life"),
        "health": ("houses-1-4", "houses-5-8", "life"),
        "timing": ("input", "identity", "life", "appendix"),
    },
}

# This is a repair target, never a license to invent conclusions.  It asks the
# model to revisit a sparse module only when the frozen route already contains
# usable evidence.
MODULE_MINIMUM_USABLE_CLAIMS = {"relationship": 3, "family": 2, "health": 2, "timing": 2}


def claim_specs(group_key: str | None = None) -> tuple[dict[str, str], ...]:
    return tuple(
        {"claimKey": f"{key}.{subkey}", "group": label, "topic": topic}
        for key, label, topics in CLAIM_GROUPS
        if group_key is None or key == group_key
        for subkey, topic in topics
    )


CLAIM_SPECS = claim_specs()

TAIWAN_CITY_ALIASES = {
    "台北": "Taipei", "新北": "New Taipei", "桃园": "Taoyuan", "台中": "Taichung",
    "台南": "Tainan", "高雄": "Kaohsiung", "基隆": "Keelung", "新竹": "Hsinchu",
    "嘉义": "Chiayi", "花莲": "Hualien", "台东": "Taitung", "澎湖": "Penghu",
}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def qwen_api_key() -> str:
    """Accept a pasted key with harmless surrounding whitespace or quotes."""
    # DASHSCOPE_API_KEY is the provider's documented name. QWEN_API_KEY is a
    # convenient equivalent for a terminal session and is never persisted.
    value = (os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("QWEN_API_KEY") or "").strip()
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    return value.strip("'\"")


def _location_query_variants(compact: str) -> tuple[str, list[str]]:
    """Return the selected country and progressively simpler city names.

    Administrative city labels are not always geocoder labels: e.g.
    “恩施土家族苗族自治州” is indexed as “Enshi”.  The selector intentionally
    stops at city level, so normalize those labels here instead of asking the
    user to enter a country or a different spelling.
    """
    country_code = "TW" if compact.startswith("台湾") else "CN"
    stem = compact.rstrip("省市县區区")
    city = stem
    for marker in ("特别行政区", "自治区", "省"):
        if marker in city:
            city = city.rsplit(marker, 1)[-1]
            break

    variants = [city]
    if city.endswith(("自治州", "地区", "盟")):
        # Autonomous prefectures and leagues normally have a short geographic
        # core before their ethnic/administrative suffix.  Try 2–4 characters
        # in descending usefulness, e.g. 恩施 / 黔东南 / 锡林郭勒.
        variants.extend(city[:length] for length in (4, 3, 2) if len(city) >= length)
    if city.endswith("市"):
        variants.append(city[:-1])
    variants.extend([stem, compact])
    return country_code, list(dict.fromkeys(value for value in variants if value))


def _geocoder_candidates(country_code: str, variants: list[str]) -> list[str]:
    """Prefer exact Latin city queries; keep Chinese labels as fallbacks."""
    candidates: list[str] = []
    for value in variants:
        if country_code == "TW" and value in TAIWAN_CITY_ALIASES:
            candidates.append(TAIWAN_CITY_ALIASES[value])
        romanized = "".join(lazy_pinyin(value, style=Style.NORMAL))
        if romanized:
            candidates.extend([romanized, romanized.replace("v", "u")])
    candidates.extend(variants)
    return list(dict.fromkeys(value for value in candidates if value))


@dataclass(frozen=True)
class PlaceResolution:
    matches: list[dict[str, Any]]
    selected: dict[str, Any]
    audit: dict[str, Any]


def _open_meteo_matches(payload: dict[str, Any], country_code: str, variants: list[str]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for item in payload.get("results", [])[:5]:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("timezone"), str)
            or item.get("country_code") != country_code
        ):
            continue
        labels = " ".join(str(item[key]) for key in ("name", "admin2", "admin1") if item.get(key))
        # Do not accept a same-sounding but different Chinese city.  This is
        # important for true-solar-time correction, where a wrong longitude is
        # worse than an explicit failure.
        if not any(variant in labels for variant in variants):
            continue
        display = " · ".join(dict.fromkeys(labels.split(" ") + [str(item.get("country", ""))]))
        matches.append(
            {
                "displayName": display.strip(" · ") or item["timezone"],
                "timezone": item["timezone"],
                "latitude": item.get("latitude"),
                "longitude": item.get("longitude"),
                "source": "Open-Meteo Geocoding",
                "coordinatePrecision": "provider-city-reference",
            }
        )
    return matches


def _load_json(request: urllib.request.Request, *, timeout: float) -> Any:
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _error_detail(error: Exception) -> str:
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code}"
    return type(error).__name__


def _is_transient_geocoding_error(error: Exception) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return error.code == 429 or error.code >= 500
    return isinstance(error, (urllib.error.URLError, TimeoutError, json.JSONDecodeError))


def _nominatim_matches(payload: Any, compact: str, country_code: str, variants: list[str]) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []
    for item in payload:
        if not isinstance(item, dict):
            continue
        address = item.get("address") if isinstance(item.get("address"), dict) else {}
        address_text = " ".join(str(value) for value in address.values())
        if (
            str(address.get("country_code", "")).upper() != country_code
            or not any(variant in address_text for variant in variants)
            or item.get("category") != "boundary"
            or item.get("type") != "administrative"
        ):
            continue
        try:
            latitude, longitude = float(item["lat"]), float(item["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        return [{
            "displayName": str(item.get("display_name") or compact),
            "timezone": "Asia/Taipei" if country_code == "TW" else "Asia/Shanghai",
            "latitude": latitude,
            "longitude": longitude,
            "source": "OpenStreetMap Nominatim",
            "coordinatePrecision": "provider-boundary-centroid",
        }]
    return []


def resolve_place(place: str) -> PlaceResolution:
    """Resolve a city locally first; third-party lookup is only a supplement."""
    compact = "".join(place.strip().split())
    local = resolve_domestic_reference(compact)
    if local is not None and local.latitude is not None and local.longitude is not None:
        match = local.to_dict()
        return PlaceResolution([match], match, {"inputPlace": place, "strategy": "local-reference", "attempts": [{"provider": match["source"], "candidate": compact, "attempt": 1, "status": "matched", "response": match}]})

    country_code, variants = _location_query_variants(compact)
    candidates = _geocoder_candidates(country_code, variants)
    attempts: list[dict[str, Any]] = []
    external_failed = False

    for candidate in candidates[:3]:
        query = urlencode({"name": candidate, "count": 5, "countryCode": country_code, "language": "zh", "format": "json"})
        request = urllib.request.Request(
            f"{GEOCODING_URL}?{query}", headers={"Accept": "application/json", "User-Agent": "AstrologyResearchMVP/1.0"}
        )
        for attempt_number in range(1, GEOCODING_MAX_ATTEMPTS + 1):
            try:
                payload = _load_json(request, timeout=GEOCODING_TIMEOUT_SECONDS)
                matches = _open_meteo_matches(payload, country_code, variants)
                attempts.append({"provider": "Open-Meteo", "candidate": candidate, "attempt": attempt_number, "status": "matched" if matches else "no-match", "response": payload})
                if matches:
                    return PlaceResolution(matches, matches[0], {"inputPlace": place, "strategy": "open-meteo", "attempts": attempts})
                break
            except Exception as exc:  # converted to a user-facing failure below
                retryable = _is_transient_geocoding_error(exc)
                attempts.append({"provider": "Open-Meteo", "candidate": candidate, "attempt": attempt_number, "status": "error", "error": _error_detail(exc), "retryable": retryable})
                external_failed = True
                if retryable and attempt_number < GEOCODING_MAX_ATTEMPTS:
                    time.sleep(GEOCODING_RETRY_DELAY_SECONDS)
                    continue
                break
        if external_failed:
            break

    query = urlencode(
        {"q": compact, "format": "jsonv2", "addressdetails": 1, "accept-language": "zh", "countrycodes": country_code.lower(), "limit": 10}
    )
    request = urllib.request.Request(
        f"{NOMINATIM_URL}?{query}",
        headers={"Accept": "application/json", "User-Agent": "AstrologyResearchMVP/1.0 (local research tool)"},
    )
    try:
        payload = _load_json(request, timeout=GEOCODING_TIMEOUT_SECONDS)
        matches = _nominatim_matches(payload, compact, country_code, variants)
        attempts.append({"provider": "OpenStreetMap Nominatim", "candidate": compact, "attempt": 1, "status": "matched" if matches else "no-match", "response": payload})
        if matches:
            return PlaceResolution(matches, matches[0], {"inputPlace": place, "strategy": "nominatim", "attempts": attempts})
    except Exception as exc:  # fallback below keeps the user moving when services are unstable
        external_failed = True
        attempts.append({"provider": "OpenStreetMap Nominatim", "candidate": compact, "attempt": 1, "status": "error", "error": _error_detail(exc), "retryable": _is_transient_geocoding_error(exc)})

    if external_failed:
        raise ApiError(422, "geocoding_not_found", "所选地点未在本地城市库中，且联网补充查询未成功；请重新选择省份和城市。")
    raise ApiError(422, "geocoding_not_found", "所选城市暂无法匹配，请重新选择省份和城市后重试。")


def external_place_lookup(place: str) -> list[dict[str, Any]]:
    """Compatibility wrapper for callers that only need candidate locations."""
    return resolve_place(place).matches


def model_raw_view(system: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Keep the full Raw document in RAM, while excluding unneeded PD lists from model context."""
    if system == "vedic":
        chart = raw["chart"]
        dashas = [
            {
                key: value
                for key, value in dasha.items()
                if key != "antardashas"
            }
            | {
                "antardashas": [
                    {key: value for key, value in antar.items() if key != "pratyantardashas"}
                    for antar in dasha["antardashas"]
                ]
            }
            for dasha in chart["dashas"]
        ]
        selected_chart = {
            key: chart[key]
            for key in (
                "lagna", "planets", "sav", "sav_by_house", "bav", "shadbala", "karakas", "dignity",
                "graha_drishti", "mutual_drishti", "house_lords", "functional", "special_points", "special_lagnas",
                "combustion", "moon_phase", "d4", "d5", "d9", "d10", "d9_dignity", "vargottama",
                "divisional_boundary_audit", "divisional_charts", "yoga_prescan", "parivartana", "pushkara", "chara_dasha",
            )
            if key in chart
        }
        selected_chart["dashas"] = dashas
        return {key: value for key, value in raw.items() if key != "chart"} | {"chart": selected_chart}
    if system == "ziwei":
        chart = raw["chart"]
        return {key: value for key, value in raw.items() if key != "chart"} | {
            "chart": {key: value for key, value in chart.items() if key != "horoscopeRanges"}
        }
    return raw


def parse_calculator_json(output: bytes) -> dict[str, Any]:
    """Extract the calculator's JSON when a bundled dependency prints notices."""
    text = output.decode("utf-8", "replace").strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < start:
            raise
        value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("calculator root must be an object")
    return value


def command_raw(system: str, birth: BirthInput, case_id: str) -> dict[str, Any]:
    payload = {
        "caseId": case_id,
        "date": birth.date,
        "time": birth.time,
        "place": birth.place,
        "gender": birth.gender,
        "timezone": birth.timezone,
        "latitude": birth.latitude,
        "longitude": birth.longitude,
        "baziTimeStandard": birth.bazi_time_standard,
    }
    if not birth.timezone:
        raise ApiError(422, "timezone_required", "请填写出生地对应的 IANA 时区，例如 Asia/Shanghai。")
    if system == "ziwei":
        command = [str(NODE), str(ZIWEI_RUNNER)]
    elif system == "bazi":
        command = [str(BAZI_PYTHON), str(CALC_RUNNER), system]
    elif system == "vedic":
        if birth.latitude is None or birth.longitude is None:
            raise ApiError(422, "coordinates_required", "吠陀占星需城市参考坐标；请先填写或查询经纬度。")
        command = [str(VEDIC_PYTHON), str(CALC_RUNNER), system]
    else:
        raise ApiError(404, "unknown_system", "未知分析体系。")
    try:
        completed = subprocess.run(
            command,
            input=canonical_json(payload),
            capture_output=True,
            timeout=240,
            cwd=ROOT,
            check=True,
        )
        return parse_calculator_json(completed.stdout)
    except subprocess.TimeoutExpired as exc:
        raise ApiError(504, "calculation_timeout", "排盘计算超时，请重试。") from exc
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        detail = getattr(exc, "stderr", b"").decode("utf-8", "replace").strip()[-500:]
        raise ApiError(422, "calculation_failed", "排盘输入无法完成计算。", detail or None) from exc


def chart_preview(system: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Expose a small, deterministic chart snapshot while interpretation runs."""
    if system == "ziwei":
        chart, audit = raw["chart"], raw["inputAudit"]
        return {
            "title": "紫微斗数排盘已完成",
            "note": "以下是确定性排盘信息，尚未生成命理解读。",
            "items": [
                {"label": "干支", "value": str(chart.get("chineseDate", "—"))},
                {"label": "农历", "value": str(chart.get("lunarDate", "—"))},
                {"label": "命宫 / 身宫", "value": f"{chart.get('soulPalaceEarthlyBranch', '—')} / {chart.get('bodyPalaceEarthlyBranch', '—')}"},
                {"label": "出生时辰", "value": str(audit.get("normalizedLocalCivilTime", {}).get("timeName", "—"))},
            ],
        }
    if system == "bazi":
        calendar, audit = raw["calendarRawData"], raw["inputAudit"]
        correction = audit.get("trueSolarCorrectionMinutes")
        correction_text = "未启用" if correction is None else f"{float(correction):+.3f} 分钟"
        return {
            "title": "子平八字排盘已完成",
            "note": "以下是确定性历法与四柱信息，尚未生成命理解读。",
            "items": [
                {"label": "四柱", "value": str(calendar.get("pillarsText", "—"))},
                {"label": "日主", "value": str(calendar.get("dayMaster", "—"))},
                {"label": "计算时间", "value": str(audit.get("calculationLocalDatetime", "—"))},
                {"label": "真太阳时校正", "value": correction_text},
            ],
        }
    chart = raw["chart"]
    lagna = chart.get("lagna", {})
    d9_lagna = chart.get("d9", {}).get("Lagna", ["—"])[0]
    d10_lagna = chart.get("d10", {}).get("Lagna", ["—"])[0]
    return {
        "title": "吠陀占星排盘已完成",
        "note": "以下是恒星黄道排盘与分盘信息，尚未生成命理解读。",
        "items": [
            {"label": "D1 上升", "value": f"{lagna.get('sign', '—')} {lagna.get('deg_str', '')}".strip()},
            {"label": "月宿", "value": str(lagna.get("nakshatra", {}).get("name", "—"))},
            {"label": "D9 上升", "value": str(d9_lagna)},
            {"label": "D10 上升", "value": str(d10_lagna)},
        ],
    }


def progress_stage_manifest(system: str) -> list[dict[str, str]]:
    return [
        {"id": "calculation", "title": "输入校验与确定性排盘"},
        *({"id": stage["id"], "title": stage["title"]} for stage in PROFESSIONAL_AUDIT_STAGES[system]),
        {"id": "summary", "title": "结论汇总与冻结"},
    ]


def progress_payload(
    system: str, *, current_step: int, total_steps: int, label: str, state: str = "running",
    preview: dict[str, Any] | None = None, stage_id: str = "calculation",
) -> dict[str, Any]:
    return {
        "system": system,
        "state": state,
        "currentStep": current_step,
        "totalSteps": total_steps,
        "label": label,
        "preview": preview,
        "stageId": stage_id,
        "stages": progress_stage_manifest(system),
    }


def parse_model_json(content: Any) -> dict[str, Any]:
    """Accept JSON mode output even if a provider still adds a Markdown fence.

    Raw model replies are retained only in the current in-memory session when
    a diagnostic trace is requested. They are never written to disk and expire
    with the anonymous session.
    """
    if not isinstance(content, str) or not content.strip():
        raise ValueError("empty-content")
    text = content.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < start:
            raise ValueError("no-json-object") from None
        value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("json-root-not-object")
    return value


def _read_qwen_stream(response: Any) -> tuple[str, str | None, int]:
    """Collect Qwen's streamed final content without retaining chain-of-thought.

    Qwen's reasoning-capable models may take several minutes before their final
    JSON is ready. Streaming keeps the connection alive while the server keeps
    only the final `content` text required for the research protocol.
    """
    content_parts: list[str] = []
    finish_reason: str | None = None
    reasoning_chars = 0
    for raw_line in response:
        line = raw_line.decode("utf-8").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        event = json.loads(data)
        choices = event.get("choices") if isinstance(event, dict) else None
        choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
        content = delta.get("content")
        if isinstance(content, str):
            content_parts.append(content)
        reasoning = delta.get("reasoning_content")
        if isinstance(reasoning, str):
            reasoning_chars += len(reasoning)
        if isinstance(choice.get("finish_reason"), str):
            finish_reason = choice["finish_reason"]
    return "".join(content_parts), finish_reason, reasoning_chars


def qwen_json(
    system_prompt: str, user_prompt: str, *, trace: list[dict[str, Any]] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    api_key = qwen_api_key()
    if not api_key:
        raise ApiError(503, "model_not_configured", "未配置 DASHSCOPE_API_KEY；未发送任何出生信息。")
    last_parse_error: Exception | None = None
    for attempt in range(2):
        event: dict[str, Any] | None = None
        retry_suffix = "" if attempt == 0 else "\n上次输出无法解析或被截断。保留全部覆盖标签和独立推演，但不要逐字复述原始 Skill、整段 Raw Data 或重复理论；只返回一个完整 JSON 对象，不要 Markdown、解释文字或代码围栏。"
        payload = {
            "model": MODEL,
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt + retry_suffix}],
            "temperature": 0,
            # qwen3.7-max-2026-06-08 is a thinking model. Each stage is stateless, so no
            # reasoning trace is sent into a later call; only frozen JSON is.
            "enable_thinking": True,
            "response_format": {"type": "json_object"},
            "max_completion_tokens": MAX_OUTPUT_TOKENS,
            # Model Studio recommends streaming for deep-thinking models. It
            # prevents a quiet long reasoning period from looking like a dead
            # local request while the final report remains a single JSON blob.
            "stream": True,
        }
        request = urllib.request.Request(
            API_URL,
            data=canonical_json(payload),
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=QWEN_REQUEST_TIMEOUT_SECONDS) as response:
                raw_reply, finish_reason, reasoning_chars = _read_qwen_stream(response)
            event = {
                **(context or {}), "attempt": attempt + 1, "finishReason": finish_reason,
                "rawReply": raw_reply, "reasoningCharsReceived": reasoning_chars, "parsed": False,
            }
            if trace is not None:
                trace.append(event)
            if finish_reason == "length":
                event["error"] = "model_output_truncated"
                last_parse_error = ValueError("model_output_truncated")
                if attempt == 0:
                    continue
                raise ApiError(502, "model_output_truncated", "通义千问两次输出均被截断；请在失败报告中查看对应阶段。")
            parsed = parse_model_json(raw_reply)
            event["parsed"] = True
            return parsed
        except (ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            last_parse_error = exc
            if event is not None:
                event["error"] = type(exc).__name__
            elif trace is not None:
                trace.append({**(context or {}), "attempt": attempt + 1, "parsed": False, "error": type(exc).__name__})
            continue
        except urllib.error.HTTPError as exc:
            # Some compatible-mode Qwen requests can be rejected once with a
            # transient HTTP 400 after a successful prior stage.  A complete
            # Skill stage is stateless and safe to retry once; never surface a
            # bare 400 without retaining its provider diagnostic.
            provider_detail = exc.read().decode("utf-8", errors="replace")[:1_500]
            if trace is not None:
                trace.append({
                    **(context or {}), "attempt": attempt + 1, "parsed": False,
                    "error": f"HTTP {exc.code}", "providerDetail": provider_detail,
                })
            if exc.code == 400 and attempt == 0:
                continue
            if exc.code == 401:
                raise ApiError(502, "model_auth_failed", "通义千问（百炼）API Key 无效、已失效或不属于当前地域/业务空间；请重新创建或确认 Key 后重启本机服务。", "HTTP 401") from exc
            if exc.code == 402:
                raise ApiError(502, "model_balance_unavailable", "通义千问（百炼）账户余额或套餐不可用；请检查百炼账户余额。", "HTTP 402") from exc
            if exc.code == 403:
                if "FreeTierOnly" in provider_detail or "Free quota exhausted" in provider_detail:
                    raise ApiError(
                        502,
                        "model_free_quota_exhausted",
                        "通义千问免费额度已用完，且当前百炼业务空间设为“仅使用免费额度”；请充值或在百炼控制台关闭该限制后重试。",
                        "HTTP 403: AllocationQuota.FreeTierOnly",
                    ) from exc
                raise ApiError(502, "model_access_denied", f"当前百炼业务空间无权调用 {MODEL}；请在模型广场开通该模型，或设置可用的 QWEN_MODEL。", "HTTP 403") from exc
            if exc.code == 429:
                raise ApiError(502, "model_rate_limited", "通义千问请求过于频繁或额度受限；请稍后重试。", "HTTP 429") from exc
            raise ApiError(502, "model_request_failed", "通义千问请求失败。", f"HTTP {exc.code}: {provider_detail}") from exc
        except urllib.error.URLError as exc:
            if trace is not None:
                trace.append({**(context or {}), "attempt": attempt + 1, "parsed": False, "error": type(exc).__name__})
            raise ApiError(502, "model_request_failed", "无法连接通义千问（百炼）。") from exc
    raise ApiError(
        502,
        "model_response_invalid",
        "通义千问两次均未返回可解析的 JSON 报告；已丢弃响应且未保存分析。",
        type(last_parse_error).__name__ if last_parse_error else "unknown",
    )


def prompt_text(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8")


def run_contract(document_ids: list[str]) -> dict[str, Any]:
    return {
        "model": f"qwen/{MODEL}",
        "promptVersion": f"website-research-v3.3-qwen-long-output+{SKILL_PACK_VERSION}",
        "temperature": 0,
        "seed": None,
        "answerKeyAccess": False,
        "knownOutcomeAccess": False,
        "networkAccess": True,
        "rawDataDocumentIds": document_ids,
    }


def single_fixed_metadata(system: str, case_id: str, document_id: str, birth: BirthInput) -> dict[str, Any]:
    availability = {name: "not-run" for name in SYSTEMS}
    availability[system] = "analyzed"
    return {
        "schemaVersion": "research.analysis.v1",
        "analysisId": f"{case_id}-{system}-single-v1",
        "caseId": case_id,
        "analysisType": system,
        "scenario": "website",
        "run": run_contract([document_id]),
        "inputAudit": {
            "rawDataDocumentIdsVerified": [document_id],
            "frozenSingleSystemAnalyses": {name: None for name in SYSTEMS},
            "systemAvailability": availability,
            "birthTimeSensitivity": "unknown",
            "locationPrecision": "city-reference" if birth.latitude is not None else "unknown",
            "issues": ["只使用本次单术 Raw Data；未读取其他术数或用户经历。"],
        },
        "validationVersion": {"version": 1, "phase": "pre-validation", "parentAnalysisId": None, "feedbackRecordIds": []},
        "questionDecisions": [],
    }


def _pointer_resolves(document: Any, pointer: str) -> bool:
    try:
        if pointer == "":
            return True
        current = document
        for token in pointer.lstrip("/").split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            current = current[int(token)] if isinstance(current, list) else current[token]
        return True
    except (KeyError, IndexError, ValueError, TypeError):
        return False


def _draft_text(value: Any, fallback: str) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else fallback


def _draft_list(value: Any, fallback: list[str], *, max_items: int = 3) -> list[str]:
    if not isinstance(value, list):
        return fallback
    items = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return items[:max_items] or fallback


def _draft_refs(value: Any, system: str, document_id: str, raw_document: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        pointer = item.get("pointer") if isinstance(item, dict) else None
        if isinstance(pointer, str) and _pointer_resolves(raw_document, pointer):
            ref: dict[str, Any] = {"system": system, "documentId": document_id, "pointer": pointer}
            note = item.get("note")
            if isinstance(note, str) and note.strip():
                ref["note"] = note.strip()[:180]
            refs.append(ref)
    return refs[:3] or [{"system": system, "documentId": document_id, "pointer": "", "note": "整个单术 Raw Data 文档。"}]


def _draft_theory(value: Any, system: str, *, max_items: int = 3) -> list[dict[str, Any]]:
    theories: list[dict[str, Any]] = []
    for index, item in enumerate(value if isinstance(value, list) else []):
        if isinstance(item, dict):
            statement = _draft_text(item.get("statement"), "")
            if not statement:
                continue
            theories.append({
                "system": system,
                "ruleId": _draft_text(item.get("ruleId"), f"model-rule-{index + 1}"),
                "statement": statement[:300],
                "sourceRef": item.get("sourceRef") if isinstance(item.get("sourceRef"), str) else None,
                "schoolOrTradition": item.get("schoolOrTradition") if isinstance(item.get("schoolOrTradition"), str) else None,
            })
        elif isinstance(item, str) and item.strip():
            theories.append({"system": system, "ruleId": f"model-rule-{index + 1}", "statement": item.strip()[:300], "sourceRef": None, "schoolOrTradition": None})
    return theories[:max_items] or [{
        "system": system, "ruleId": "insufficient-theory",
        "statement": "未提供足够可复核的理论链；该条只能作为待验证假设。",
        "sourceRef": None, "schoolOrTradition": None,
    }]


PROFESSIONAL_AUDIT_STAGES: dict[str, tuple[dict[str, Any], ...]] = {
    system: tuple({"id": stage.id, "title": stage.title, "refs": stage.refs, "skillStage": stage} for stage in stages_for(system))
    for system in SYSTEMS
}


def _stage_pointers(system: str, stage: dict[str, Any]) -> tuple[str, ...]:
    """Always include shared birth facts so a specialist cannot invent gaps."""
    common = {
        "ziwei": ("/inputAudit", "/chart/gender", "/chart/solarDate"),
        "bazi": ("/inputAudit", "/calendarRawData/pillarsText"),
        "vedic": ("/input", "/inputAudit", "/validation"),
    }[system]
    return tuple(dict.fromkeys((*common, *stage["refs"])))


def _fixed_audit_refs(system: str, document_id: str, raw_document: dict[str, Any], pointers: tuple[str, ...]) -> list[dict[str, Any]]:
    return [
        {"system": system, "documentId": document_id, "pointer": pointer, "note": "专业审计所用 Raw Data。"}
        for pointer in pointers
        if _pointer_resolves(raw_document, pointer)
    ] or [{"system": system, "documentId": document_id, "pointer": "", "note": "整个单术 Raw Data 文档。"}]


def _canonical_palace_name(value: Any) -> str:
    """Match iztro's short palace labels to the routed, user-facing labels."""
    name = str(value).strip().replace("宮", "宫")
    if name == "仆役":
        name = "交友"
    return name[:-1] if name.endswith("宫") else name


def _audit_raw_view(raw_document: dict[str, Any], pointers: tuple[str, ...], stage: dict[str, Any] | None = None) -> dict[str, Any]:
    """Expose only the Raw fields relevant to this professional audit stage."""
    view: dict[str, Any] = {}
    for pointer in pointers:
        if not _pointer_resolves(raw_document, pointer):
            continue
        current: Any = raw_document
        for token in pointer.lstrip("/").split("/"):
            if not token:
                continue
            current = current[int(token)] if isinstance(current, list) else current[token]
        if pointer == "/chart/dashas" and isinstance(current, list):
            # Preserve the canonical 9/81/729 timeline in Raw Data, but obey
            # the original Vedic progressive-read gate: ordinary natal stages
            # receive MD/AD only and may not browse all PD rows post hoc.
            current = [
                {
                    key: value for key, value in dasha.items() if key != "antardashas"
                }
                | {
                    "antardashas": [
                        {key: value for key, value in antar.items() if key != "pratyantardashas"}
                        for antar in dasha.get("antardashas", [])
                    ]
                }
                for dasha in current
                if isinstance(dasha, dict)
            ]
        palace_names = stage["skillStage"].palace_names if stage else ()
        if pointer == "/chart/palaces" and palace_names and isinstance(current, list):
            selected = {_canonical_palace_name(name) for name in palace_names}
            current = [
                palace for palace in current
                if isinstance(palace, dict) and _canonical_palace_name(palace.get("name")) in selected
            ]
        view[pointer] = current
    return view


def make_professional_audit_prompt(
    system: str, raw_document: dict[str, Any], stage: dict[str, Any], previous_frozen: dict[str, Any] | None,
) -> tuple[str, str]:
    skill_stage: SkillStage = stage["skillStage"]
    system_prompt = "\n\n".join((
        prompt_text("research-core-v2.md"),
        "以下是版本化保存的原始完整 Skill 规则。它是本阶段的专业判断真源；外层研究协议只约束证据、反证与 JSON 交付，不得用常识或压缩提示替代这些规则。\n" + stage_rule_bundle(system, skill_stage),
        prompt_text("single-system-v2.md"),
        prompt_text("scenarios/website-v2.md"),
    ))
    pointers = _stage_pointers(system, stage)
    coverage = "、".join(f"[{label}]" for label in skill_stage.coverage)
    depth_rule = (
        f"原 Skill 对本阶段有深度下限：每个覆盖标签在 observations 与 inference 中合计至少 {skill_stage.min_chars_per_coverage} 个中文字符；不得用一句话占位。"
        if skill_stage.min_chars_per_coverage else
        "按原 Skill 的本阶段深度完整展开，不使用一句话占位。"
    )
    allows_health_symbolism = any("健康" in label or "疾厄" in label for label in skill_stage.coverage)
    health_boundary = (
        "本阶段包含传统健康象意：只能讨论传统体系中的结构信号、生活情境与日常照护方向；不得写疾病、诊断、治疗、手术、死亡、寿命或确定性健康结果。"
        if allows_health_symbolism else
        "本阶段不包含健康主题：不得主动引入健康、疾病、医疗、手术、死亡或寿命内容。"
    )
    user_prompt = "\n".join((
        "只返回一个 JSON 专业审计草稿，不要 Markdown。你正在执行完整单术流程中的一个冻结阶段；不能读取或推测其他体系。",
        f"当前阶段：{stage['title']}（{stage['id']}）。只对本阶段负责，未提供的数据必须说明无法判断。",
        "格式：{\"theory\":[{\"ruleId\":string,\"statement\":string,\"sourceRef\":string|null,\"schoolOrTradition\":string|null}],\"observations\":[string],\"inference\":[string],\"counterEvidence\":[string],\"uncertainty\":[string],\"status\":\"completed|conditional|insufficient-input\"}",
        f"覆盖清单：{coverage}。observations 或 inference 中必须逐项出现这些方括号标签；即使某项证据不足，也要以对应标签明确写“无法判断”，不得省略。",
        depth_rule,
        "必须完整执行上方原始 Skill，但不得把原始 Skill 段落、规则全文或整段 Raw Data 转抄进 JSON。theory 只保留本次实际使用的操作性规则摘要和 sourceRef；同一规则只写一次。每个覆盖标签至少给出一条不重复的 Raw 观察和一条条件式推演；当原 Skill 要求更高字符下限时，可拆成多条，但每条只表达一个可审计事实或推理环节。除原 Skill 明定的字符下限外，每个标签最多两条 observations、两条 inference，每条不超过 220 个中文字符；counterEvidence 与 uncertainty 只保留最强且不重复的信号。observations 必须只是可见 Raw Data 的事实；inference 必须是原 Skill 规则到条件式含义的可审计推演；不得写确定事件、诊断、死亡或财富数额。",
        health_boundary,
        "严禁声称某项输入“未提供”或“未知”，除非它在下方 RAW_DATA_FOR_THIS_STAGE 中确实不存在。不得为缺失的流年、地点、性别或时间精度编造通用免责声明。",
        "上一阶段冻结结果只可引用，不可改写或纠正：\n" + json.dumps(previous_frozen, ensure_ascii=False) if previous_frozen else "这是首个解释阶段，没有上一阶段结果。",
        "硬性输出边界：theory 最多 8 条，observations、inference 每个覆盖标签各最多 1 条，counterEvidence 与 uncertainty 各最多 4 条。只写本阶段覆盖标签，严禁列举未列入覆盖清单的宫位、规则或盘面事实；原 Skill 只用于内部专业推演，不能逐条转录。",
        "RAW_DATA_FOR_THIS_STAGE：\n" + json.dumps({"documentId": raw_document["documentId"], "fields": _audit_raw_view(raw_document, pointers, stage)}, ensure_ascii=False),
    ))
    return system_prompt, user_prompt


def _missing_stage_coverage(stage: dict[str, Any], draft: dict[str, Any]) -> list[str]:
    skill_stage: SkillStage = stage["skillStage"]
    items = [
        item for key in ("observations", "inference")
        for item in (draft.get(key) if isinstance(draft.get(key), list) else [])
        if isinstance(item, str)
    ]
    missing: list[str] = []
    for label in skill_stage.coverage:
        tagged = [item for item in items if f"[{label}]" in item]
        if not tagged or sum(len(item) for item in tagged) < skill_stage.min_chars_per_coverage:
            missing.append(label)
    return missing


def _stage_insufficient_fallback(stage: dict[str, Any], draft: dict[str, Any], missing: list[str]) -> dict[str, Any]:
    """Preserve completed evidence while making an irreparable stage explicit."""
    result = dict(draft)
    observations = list(draft.get("observations") if isinstance(draft.get("observations"), list) else [])
    inference = list(draft.get("inference") if isinstance(draft.get("inference"), list) else [])
    for label in missing:
        inference.append(f"[{label}] 无法判断：该阶段多次修复后仍未形成足够的可冻结推演。")
    result["observations"] = observations or ["该阶段未形成可复核盘面观察。"]
    result["inference"] = inference or ["该阶段证据不足，无法判断。"]
    result["status"] = "insufficient-input"
    return result


def professional_audit_from_drafts(system: str, raw_document: dict[str, Any], drafts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    document_id = raw_document["documentId"]
    audits: list[dict[str, Any]] = []
    for index, stage in enumerate(PROFESSIONAL_AUDIT_STAGES[system]):
        draft = drafts[index] if index < len(drafts) and isinstance(drafts[index], dict) else {}
        status = draft.get("status") if draft.get("status") in {"completed", "conditional", "insufficient-input"} else "insufficient-input"
        audits.append({
            "auditId": f"{system}-audit-{stage['id']}",
            "stage": stage["id"],
            "title": stage["title"],
            "rawDataRefs": _fixed_audit_refs(system, document_id, raw_document, _stage_pointers(system, stage)),
            "theory": _draft_theory(draft.get("theory"), system, max_items=30),
            "observations": _draft_list(draft.get("observations"), ["该阶段未形成足够的可复核盘面观察。"], max_items=60),
            "inference": _draft_list(draft.get("inference"), ["该阶段证据不足，无法判断。"], max_items=60),
            "counterEvidence": _draft_list(draft.get("counterEvidence"), ["未提供可复核的反向信号；不应将此视为支持。"], max_items=30),
            "uncertainty": _draft_list(draft.get("uncertainty"), ["该阶段需要与其他同术审计阶段交叉验证。"], max_items=20),
            "status": status,
        })
    return audits


def _draft_claims_by_key(draft: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    values = draft.get("claims") if isinstance(draft.get("claims"), list) else []
    for value in values:
        if not isinstance(value, dict):
            continue
        key = value.get("claimKey")
        if isinstance(key, str) and key in {spec["claimKey"] for spec in CLAIM_SPECS} and key not in result:
            result[key] = value
    return result


def _selected_audits(source: dict[str, Any], professional_audit: list[dict[str, Any]]) -> list[dict[str, Any]]:
    audit_map = {audit["auditId"]: audit for audit in professional_audit}
    selected: list[dict[str, Any]] = []
    for audit_id in source.get("sourceAuditIds", []) if isinstance(source.get("sourceAuditIds"), list) else []:
        if isinstance(audit_id, str) and audit_id in audit_map and audit_map[audit_id] not in selected:
            selected.append(audit_map[audit_id])
    return selected


def _group_key_for_spec(spec: dict[str, str]) -> str:
    return spec["claimKey"].split(".", 1)[0]


def _routed_audits(
    system: str, spec: dict[str, str], professional_audit: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    allowed = set(CLAIM_STAGE_ROUTES[system][_group_key_for_spec(spec)])
    return [audit for audit in professional_audit if audit.get("stage") in allowed]


def _apply_claim_stage_routes(
    system: str, draft: dict[str, Any], specs: tuple[dict[str, str], ...], professional_audit: list[dict[str, Any]],
) -> dict[str, Any]:
    """Normalize every present Claim to its deterministic legal evidence route."""
    spec_by_key = {spec["claimKey"]: spec for spec in specs}
    claims: list[dict[str, Any]] = []
    for source in draft.get("claims", []) if isinstance(draft.get("claims"), list) else []:
        if not isinstance(source, dict) or source.get("claimKey") not in spec_by_key:
            continue
        item = dict(source)
        allowed = [audit["auditId"] for audit in _routed_audits(system, spec_by_key[item["claimKey"]], professional_audit)]
        selected = [audit_id for audit_id in item.get("sourceAuditIds", []) if audit_id in allowed]
        # The summary model only sees routed frozen stages, so attaching that
        # route is deterministic provenance, not invented interpretive support.
        item["sourceAuditIds"] = selected or allowed
        claims.append(item)
    return {"claims": claims}


def _usable_draft_claim_count(
    draft: dict[str, Any], specs: tuple[dict[str, str], ...], professional_audit: list[dict[str, Any]],
) -> int:
    by_key = _draft_claims_by_key(draft)
    return sum(
        1 for spec in specs
        if spec["claimKey"] in by_key
        and _selected_audits(by_key[spec["claimKey"]], professional_audit)
        and not _claim_says_unable_to_judge(by_key[spec["claimKey"]])
    )


def _missing_claim_slots(
    draft: dict[str, Any], specs: tuple[dict[str, str], ...], professional_audit: list[dict[str, Any]],
) -> list[str]:
    by_key = _draft_claims_by_key(draft)
    return [
        spec["claimKey"] for spec in specs
        if spec["claimKey"] not in by_key or not _selected_audits(by_key[spec["claimKey"]], professional_audit)
    ]


def _audit_supports_conditional_expression(audit: dict[str, Any]) -> bool:
    """Whether a frozen stage contains enough material for a cautious summary.

    A completed audit may still explicitly say that its input is insufficient.
    That is a legitimate abstention.  But a completed stage containing both an
    observation and an inference is not made insufficient merely because it
    also records a counter-signal.
    """
    if audit.get("status") == "insufficient-input":
        return False
    observations = audit.get("observations") if isinstance(audit.get("observations"), list) else []
    inferences = audit.get("inference") if isinstance(audit.get("inference"), list) else []
    return any(isinstance(item, str) and item.strip() and "无法判断" not in item for item in observations) and any(
        isinstance(item, str) and item.strip() and "无法判断" not in item for item in inferences
    )


def _excessive_abstention_claim_keys(
    draft: dict[str, Any], specs: tuple[dict[str, str], ...], professional_audit: list[dict[str, Any]],
) -> list[str]:
    """Find family/health slots that declined despite routed frozen evidence.

    This intentionally applies only to the two user-facing modules that need
    a cautious, non-event-based expression.  It never changes a real
    insufficient-input result into a conclusion.
    """
    by_key = _draft_claims_by_key(draft)
    excessive: list[str] = []
    for spec in specs:
        if _group_key_for_spec(spec) not in {"family", "health"}:
            continue
        source = by_key.get(spec["claimKey"])
        if not source or not _claim_says_unable_to_judge(source):
            continue
        if any(_audit_supports_conditional_expression(audit) for audit in _selected_audits(source, professional_audit)):
            excessive.append(spec["claimKey"])
    return excessive


def _high_stakes_claim_keys(draft: dict[str, Any], specs: tuple[dict[str, str], ...]) -> list[str]:
    """Keep health material non-diagnostic without deleting useful symbolism."""
    high_stakes_terms = ("健康", "疾病", "手术", "死亡", "寿命", "癌", "health", "disease", "death", "surgery")
    # Match assertions, rather than merely mentioning the boundary.  For
    # example, “不作医学诊断” is an appropriate disclaimer, whereas “诊断为”
    # is not an acceptable conclusion.
    health_direct_terms = ("患", "罹患", "确诊", "诊断为", "有疾病", "疾病风险", "疾病问题", "治疗", "手术", "死亡", "寿命", "癌", "病症")
    absolute_terms = ("一定", "必然", "必定", "注定", "肯定", "将会", "必会", "不可避免")
    soft_terms = ("留意", "注意", "建议", "可", "宜", "不妨", "值得", "倾向", "提醒", "如有")
    by_key = _draft_claims_by_key(draft)
    unsafe: list[str] = []
    for spec in specs:
        source = by_key.get(spec["claimKey"], {})
        action = source.get("actionAdvice")
        action_items = action.get("items") if isinstance(action, dict) else action
        text = " ".join(
            item for item in (
                source.get("claim"), source.get("provisionalConclusion"),
                *(action_items if isinstance(action_items, list) else []),
            ) if isinstance(item, str)
        ).lower()
        if spec["group"] != "健康提醒" and any(term in text for term in high_stakes_terms):
            unsafe.append(spec["claimKey"])
        elif spec["group"] == "健康提醒":
            # Signals and contexts may state a traditional chart observation;
            # only the care slot must itself be phrased as advice.  Requiring
            # every health sentence to contain an advice word was causing
            # compliant symbolic observations to be removed wholesale.
            requires_soft_language = spec["claimKey"] == "health.care"
            if (
                any(term in text for term in health_direct_terms)
                or any(term in text for term in absolute_terms)
                or (requires_soft_language and not any(term in text for term in soft_terms))
            ):
                unsafe.append(spec["claimKey"])
    return unsafe


def _claim_says_unable_to_judge(source: dict[str, Any]) -> bool:
    """A traceable source can still decline to draw a conclusion.

    Keep the evidence-chain grade and the conclusion state consistent: wording
    such as “无法判断” must not be rendered as a conditional conclusion with a
    C-grade badge.
    """
    return "无法判断" in " ".join(
        value for value in (source.get("claim"), source.get("provisionalConclusion"))
        if isinstance(value, str)
    )


def _is_generation_incomplete(claim: dict[str, Any]) -> bool:
    """Identify a pipeline gap, which is not evidence about the birth chart."""
    text = " ".join(
        value for value in (
            claim.get("claim"),
            claim.get("provisionalConclusion", {}).get("text") if isinstance(claim.get("provisionalConclusion"), dict) else None,
        ) if isinstance(value, str)
    )
    return "结论生成待补全" in text


def report_quality_summary(analysis: dict[str, Any]) -> dict[str, Any]:
    """Keep a publish-quality audit without weakening the research contract."""
    claims = [claim for claim in analysis.get("claims", []) if isinstance(claim, dict)]
    incomplete = [claim.get("claimKey") for claim in claims if _is_generation_incomplete(claim)]
    usable = [
        claim for claim in claims
        if not _is_generation_incomplete(claim)
        and claim.get("provisionalConclusion", {}).get("status") != "unable-to-judge"
    ]
    usable_groups = {
        str(claim.get("claimKey", "")).split(".", 1)[0]
        for claim in usable if isinstance(claim.get("claimKey"), str)
    }
    core_groups = {"personality", "career", "relationship", "timing"}
    module_coverage = {
        group: {
            "usable": sum(1 for claim in usable if str(claim.get("claimKey", "")).startswith(f"{group}.")),
            "minimum": minimum,
        }
        for group, minimum in MODULE_MINIMUM_USABLE_CLAIMS.items()
    }
    for values in module_coverage.values():
        values["complete"] = values["usable"] >= values["minimum"]
    return {
        "status": "ready" if len(usable) >= 8 and len(usable_groups & core_groups) >= 3 and all(values["complete"] for values in module_coverage.values()) else "limited",
        "usableClaimCount": len(usable),
        "generationIncompleteClaimKeys": [key for key in incomplete if isinstance(key, str)],
        "missingCoreGroups": sorted(core_groups - usable_groups),
        "moduleCoverage": module_coverage,
        "minimum": {"usableClaims": 8, "coreGroups": 3},
    }


def _dedupe_dicts(items: list[dict[str, Any]], keys: tuple[str, ...], limit: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for item in items:
        marker = tuple(item.get(key) for key in keys)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def single_analysis_from_draft(
    system: str, fixed: dict[str, Any], raw_document: dict[str, Any], draft: dict[str, Any],
    professional_audit: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Deterministically seal a small model draft into research.analysis.v1.

    Models are good at the interpretive text but unreliable at reproducing a
    large nested schema.  This keeps all research safeguards server-owned.
    """
    document_id = raw_document["documentId"]
    professional_audit = professional_audit or []
    draft_by_key = _draft_claims_by_key(draft)
    claims: list[dict[str, Any]] = []
    for index, spec in enumerate(CLAIM_SPECS):
        source = draft_by_key.get(spec["claimKey"], {})
        selected_audits = _selected_audits(source, professional_audit)
        has_claim = isinstance(source.get("claim"), str) and bool(source["claim"].strip()) and bool(selected_audits)
        semantic_unable = has_claim and _claim_says_unable_to_judge(source)
        refs = _dedupe_dicts(
            [ref for audit in selected_audits for ref in audit.get("rawDataRefs", [])],
            ("system", "documentId", "pointer"), 8,
        ) or [{"system": system, "documentId": document_id, "pointer": "", "note": "整个单术 Raw Data 文档。"}]
        theory = _dedupe_dicts(
            [item for audit in selected_audits for item in audit.get("theory", [])],
            ("system", "ruleId", "statement"), 12,
        ) or _draft_theory([], system)
        inference = _draft_list(source.get("inference"), ["本条尚未形成足够的可展示结论。"], max_items=8)
        uncertainty_items = _draft_list(source.get("uncertainty"), ["单术分析尚未与另外两术比较。"])
        counter_items: list[dict[str, Any]] = []
        for audit in selected_audits:
            for statement in audit.get("counterEvidence", []):
                if not isinstance(statement, str) or any(marker in statement for marker in ("未提供可复核", "不应将此视为支持", "未发现")):
                    continue
                counter_items.append({
                    "system": system,
                    "statement": statement[:300],
                    "impact": "moderate",
                    "rawDataRefs": audit.get("rawDataRefs", [])[:2] or refs[:1],
                })
                if len(counter_items) >= 3:
                    break
            if len(counter_items) >= 3:
                break
        grade = "C" if has_claim and not semantic_unable else "D"
        strong_signal = (
            grade == "C" and len(selected_audits) >= 2
            and all(audit.get("status") == "completed" for audit in selected_audits)
            and not counter_items
        )
        provisional_text = _draft_text(
            source.get("provisionalConclusion") if isinstance(source.get("provisionalConclusion"), str) else source.get("claim"),
            "无法判断：本条结论生成待补全，尚未形成可追溯的专业阶段引用。",
        )
        if not has_claim:
            provisional_text = "无法判断：本条结论生成待补全，尚未形成可追溯的专业阶段引用。"
        elif grade == "D" and "无法判断" not in provisional_text:
            provisional_text = f"无法判断：{provisional_text}"
        active_system = {
            "status": "analyzed", "analysisId": fixed["analysisId"], "frozen": True,
            "conclusion": provisional_text, "rawDataRefs": refs, "theory": theory,
            "inference": inference, "uncertainty": uncertainty_items,
        }
        inactive_system = {
            "status": "not-run", "analysisId": None, "frozen": False, "conclusion": "尚未运行，无法判断。",
            "rawDataRefs": [], "theory": [], "inference": [], "uncertainty": ["该体系尚未运行。"],
        }
        action_items: list[dict[str, Any]] = []
        raw_advice = source.get("actionAdvice")
        source_items = raw_advice.get("items") if isinstance(raw_advice, dict) else raw_advice
        for item in source_items if isinstance(source_items, list) and grade == "C" else []:
            text = item.get("text") if isinstance(item, dict) else item
            if isinstance(text, str) and text.strip():
                action_items.append({"text": text.strip()[:180], "basis": "provisional-conclusion", "nonDiagnostic": True})
        is_health_claim = spec["group"] == "健康提醒"
        if is_health_claim and not action_items:
            action_items.append({"text": "可结合作息、压力与身体感受做日常记录；如有持续不适，建议咨询专业人士。", "basis": "general-safety", "nonDiagnostic": True})
        disclaimer = "传统命理象意仅供自我观察与日常照护参考，不替代专业医疗意见。" if is_health_claim else None
        claims.append({
            "claimId": f"{system}-claim-{spec['claimKey'].replace('.', '-')}",
            "claimKey": spec["claimKey"],
            "claimScope": {"atomic": True, "topic": f"{spec['group']}｜{spec['topic']}", "direction": _draft_text(source.get("direction"), "待验证倾向"), "timeRange": None},
            "claim": _draft_text(source.get("claim"), "无法判断：本条结论生成待补全。"),
            "rawDataRefs": refs,
            "theory": theory,
            "inference": inference,
            "counterEvidence": {
                "status": "identified" if counter_items else "none-found",
                "searchSummary": "冻结阶段存在需要一并保留的反向信号。" if counter_items else "未发现明确反向盘面信号；这不等于结论成立。",
                "items": counter_items,
            },
            "systemAnalyses": {name: active_system if name == system else inactive_system for name in SYSTEMS},
            "comparisonBasis": {"topicAligned": False, "directionAligned": False, "timeRangeAligned": False, "specificityAligned": False, "notes": ["单术阶段，尚不能进行三术比较。"]},
            "consistency": {"status": "insufficient", "agreements": [], "conflicts": []},
            "uncertainty": {"birthTimeSensitivity": "unknown", "items": uncertainty_items},
            "validation": _draft_list(source.get("validation"), ["以非诱导方式记录与该主题相关的长期真实经历。"]),
            "falsificationConditions": _draft_list(source.get("falsificationConditions"), ["若长期现实记录持续与该倾向相反，则该暂定结论应被推翻。"]),
            "evidenceGrade": grade,
            "gradeRationale": {"rawDataDirectness": "strong" if strong_signal else "moderate", "timeStability": "unknown", "theoryChain": "complete" if grade == "C" else "weak", "counterEvidenceResistance": "weak" if counter_items else "strong" if strong_signal else "unknown", "systemIndependence": "single-system", "explanation": "多个相关冻结阶段支持同一方向，仍受单术证据上限约束。" if strong_signal else "仅由一个体系支持或仍有条件限制；需现实验证。" if grade == "C" else "本条尚未形成可稳定下结论的证据链，按协议为 D。"},
            "pollutionRisk": "P2",
            "pollutionRationale": "网站场景中用户可能已知部分经历；结果仅用于探索与验证。",
            "empiricalUse": "exploratory",
            "provisionalConclusion": {"status": "conditional" if grade == "C" else "unable-to-judge", "text": provisional_text, "basisSystems": [system] if grade == "C" else [], "conflictRetained": True},
            "actionAdvice": {"status": "provided" if action_items else "not-applicable", "items": action_items[:3], "disclaimer": disclaimer},
        })
    return {**fixed, "professionalAudit": professional_audit, "claims": claims}


def make_single_prompt(
    system: str, case_id: str, raw_document: dict[str, Any], birth: BirthInput, professional_audit: list[dict[str, Any]],
    specs: tuple[dict[str, str], ...] = CLAIM_SPECS,
) -> tuple[str, str]:
    doc_id = raw_document["documentId"]
    system_prompt = "\n\n".join((prompt_text("research-core-v2.md"), prompt_text("single-system-v2.md"), prompt_text("scenarios/website-v2.md")))
    allowed_by_claim = {
        spec["claimKey"]: [audit["auditId"] for audit in _routed_audits(system, spec, professional_audit)]
        for spec in specs
    }
    routed_ids = {audit_id for values in allowed_by_claim.values() for audit_id in values}
    routed_audit = [audit for audit in professional_audit if audit["auditId"] in routed_ids]
    group_keys = {_group_key_for_spec(spec) for spec in specs}
    topic_guidance: list[str] = []
    if "relationship" in group_keys:
        topic_guidance.append("关系互动优先总结长期需求、互动方式、冲突模式与稳定条件；没有充分时间证据时不得改写成结婚、离婚或具体对象事件。")
    if "timing" in group_keys:
        report_date = datetime.now().date().isoformat()
        topic_guidance.append(f"时间基准为报告生成日 {report_date}：当前阶段以正在运行的大运／大限／Dasha为背景，近期窗口指未来12个月，中期窗口指未来3年；必须说明触发条件和时间敏感性。")
    user_prompt = "\n".join(
        (
            "只返回 JSON 研究草稿，不要 Markdown。后端会确定性地生成最终研究协议结构。",
            "严格按照 FIXED_CLAIM_SLOTS 为每个 claimKey 各生成且只生成一条原子化 Claim，不得合并、改名、遗漏或新增槽位。每条只表达一个主题、一个方向与一个时间范围。不要做疾病、死亡、财富数额或确定事件预言。",
            "草稿格式：{\"claims\":[{\"claimKey\":string,\"direction\":string,\"claim\":string,\"sourceAuditIds\":[string],\"inference\":[string],\"uncertainty\":[string],\"validation\":[string],\"falsificationConditions\":[string],\"provisionalConclusion\":string,\"actionAdvice\":[string]}]}",
            "每条必须从 ALLOWED_SOURCE_AUDIT_IDS_BY_CLAIM 中引用至少一个真正支持它的冻结 auditId；后端会再次按固定路由绑定并核验，禁止引用其他阶段。后端会从这些阶段确定性复制 Raw Data 与 Theory；不得自行生成规则编号或 Raw Data 引用。只有相关冻结阶段均为 insufficient-input、没有相关观察和推演、或出生输入会改变核心结构时，才可在 claim 与 provisionalConclusion 写“无法判断”。",
            "全部 Skill 阶段已经冻结。最终 Claim 只能汇总这些冻结结果，不得重新读取盘面、重新推演、修改阶段判断或补充新理论。推演要保留冻结阶段中与该原子主题直接相关的关键信息，避免只剩泛化短句。",
            "家庭居住组：只要已冻结的合法阶段同时有相关观察和推演，就不得因为信号复杂、存在冲突或受环境影响而写“无法判断”；应写‘有条件倾向’或‘存在相反信号’，并保留成立条件。不得编造家庭成员经历、居住事件或具体事实。",
            "若冻结阶段存在冲突或某项不足，最终 Claim 必须原样保留，不得为了成文而统一。除健康提醒组外，健康、疾病、手术、死亡、寿命、医疗或心理诊断内容不得出现在 claim、provisionalConclusion 或 actionAdvice 中。健康提醒组分三类：health.signals 仅描述传统象意信号，health.contexts 仅描述值得留意的生活情境，health.care 才提供日常照护建议。三类都不得写患病、疾病诊断、治疗、手术、死亡、寿命或任何一定／必然／注定式结论；只有 health.care 必须使用‘留意、建议、可、不妨、宜、如有不适’等委婉建议词。合规的传统象意不得因为未出现建议词而省略。",
            *topic_guidance,
            "FIXED_CLAIM_SLOTS：\n" + json.dumps(specs, ensure_ascii=False),
            "ALLOWED_SOURCE_AUDIT_IDS_BY_CLAIM：\n" + json.dumps(allowed_by_claim, ensure_ascii=False),
            "本主题路由到的冻结 Skill 阶段：\n" + json.dumps(routed_audit, ensure_ascii=False),
            f"RAW_DOCUMENT_ID（仅供引用核验，不提供盘面重算）：{doc_id}",
        )
    )
    return system_prompt, user_prompt


def make_targeted_claim_repair_prompt(
    system: str,
    case_id: str,
    raw_document: dict[str, Any],
    birth: BirthInput,
    professional_audit: list[dict[str, Any]],
    spec: dict[str, str],
    previous: dict[str, Any] | None,
    reason: str,
) -> tuple[str, str]:
    """Repair one missing Claim without rerunning a completed Skill route."""
    system_prompt, user_prompt = make_single_prompt(
        system, case_id, raw_document, birth, professional_audit, (spec,),
    )
    allowed_audits = [
        {"auditId": audit["auditId"], "title": audit["title"], "status": audit["status"]}
        for audit in _routed_audits(system, spec, professional_audit)
    ]
    return system_prompt, "\n".join((
        user_prompt,
        "这是一次结论级补全，只补全下列唯一槽位；不得重跑排盘、补充新理论或输出其他 claimKey。",
        "目标 claimKey：" + spec["claimKey"],
        "上次未通过原因：" + reason,
        "sourceAuditIds 只能从以下冻结审计编号中选择，且至少选择一个实际支持本条的编号：\n" + json.dumps(allowed_audits, ensure_ascii=False),
        "上一版草稿（可能为空或无效）：\n" + json.dumps(previous or {}, ensure_ascii=False),
        "如果上次未通过原因是“过度保守弃权”，说明已冻结的合法阶段存在观察和推演：不得再写“无法判断”；只能基于这些冻结内容输出有条件倾向或存在相反信号，并保留不确定性。若是健康提醒，按 health.signals／health.contexts／health.care 的分槽位边界改写，不得作诊断。",
        "只有冻结阶段确实不足以支持该主题时，才可输出“无法判断”，且须引用最相关的冻结阶段说明不足来源。",
    ))


def _replace_claim_draft(draft: dict[str, Any], claim: dict[str, Any]) -> dict[str, Any]:
    """Replace one fixed slot while preserving every other frozen draft."""
    values = [item for item in draft.get("claims", []) if isinstance(item, dict)]
    key = claim.get("claimKey")
    replaced = False
    next_values: list[dict[str, Any]] = []
    for item in values:
        if item.get("claimKey") == key:
            if not replaced:
                next_values.append(claim)
                replaced = True
        else:
            next_values.append(item)
    if not replaced:
        next_values.append(claim)
    return {"claims": next_values}


def integration_fixed_metadata(case_id: str, raw_documents: dict[str, dict[str, Any]], singles: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Metadata owned by the server, never reproduced by the model."""
    documents = list(raw_documents)
    return {
        "schemaVersion": "research.analysis.v1",
        "analysisId": f"{case_id}-integration-v1",
        "caseId": case_id,
        "analysisType": "integration",
        "scenario": "website",
        "run": run_contract(documents),
        "inputAudit": {
            "rawDataDocumentIdsVerified": documents,
            "frozenSingleSystemAnalyses": {system: singles[system]["analysisId"] for system in SYSTEMS},
            "systemAvailability": {system: "analyzed" for system in SYSTEMS},
            "birthTimeSensitivity": "unknown",
            "locationPrecision": "city-reference",
            "issues": ["比较层只比较冻结的单术分析，不重做任何单术推演。"],
        },
        "validationVersion": {"version": 1, "phase": "pre-validation", "parentAnalysisId": None, "feedbackRecordIds": []},
        "questionDecisions": [],
    }
def _compact_frozen_claim(claim: dict[str, Any]) -> dict[str, Any]:
    """The comparison model only needs frozen conclusions, not the whole schema."""
    return {
        "claimId": claim.get("claimId"),
        "claimKey": claim.get("claimKey"),
        "scope": claim.get("claimScope"),
        "claim": claim.get("claim"),
        "provisionalConclusion": claim.get("provisionalConclusion"),
        "rawDataRefs": claim.get("rawDataRefs", [])[:2],
        "theory": claim.get("theory", [])[:2],
        "inference": claim.get("inference", [])[:2],
        "uncertainty": claim.get("uncertainty", {}),
    }


def make_integration_prompt(
    case_id: str, raw_documents: dict[str, dict[str, Any]], singles: dict[str, dict[str, Any]],
    specs: tuple[dict[str, str], ...] = CLAIM_SPECS,
) -> tuple[str, str]:
    """Ask for a compact comparison draft; the server seals the final protocol."""
    system_prompt = "\n\n".join((prompt_text("research-core-v2.md"), prompt_text("integration-v2.md"), prompt_text("scenarios/website-v2.md")))
    wanted = {spec["claimKey"] for spec in specs}
    compact_singles = {
        system: {
            "analysisId": single["analysisId"],
            "claims": [_compact_frozen_claim(claim) for claim in single.get("claims", []) if claim.get("claimKey") in wanted],
        }
        for system, single in singles.items()
    }
    user_prompt = "\n".join(
        (
            "只返回一个 JSON 比较草稿，不要 Markdown。后端会确定性地封装成最终研究协议；不要输出 Schema 或固定元数据。",
            "这是比较层，不是第四套命理：只能比较下方三份已冻结结论，严禁新增盘面解释、改写单术结论、输出统一人生画像、统一结论或整合行动建议。",
            "严格按照 FIXED_CLAIM_SLOTS，以 claimKey 对齐三术结论并为每个槽位各生成一次比较；不得按数组位置猜测对应关系。status 只能是 aligned、partial、conflict、insufficient。只有主题、方向、时间范围和具体程度都相同才可写 aligned。冲突必须列出；insufficient 必须明确“无法判断”。",
            "草稿格式：{\"claims\":[{\"claimKey\":string,\"direction\":string,\"status\":\"aligned|partial|conflict|insufficient\",\"agreements\":[string],\"conflicts\":[string],\"inference\":[string],\"uncertainty\":[string],\"validation\":[string],\"falsificationConditions\":[string]}]}",
            "FIXED_CLAIM_SLOTS：\n" + json.dumps(specs, ensure_ascii=False),
            "FROZEN_SINGLE_ANALYSES（唯一可用输入）：\n" + json.dumps(compact_singles, ensure_ascii=False),
        )
    )
    return system_prompt, user_prompt


def make_targeted_integration_repair_prompt(
    case_id: str, raw_documents: dict[str, dict[str, Any]], singles: dict[str, dict[str, Any]],
    spec: dict[str, str], previous: dict[str, Any] | None,
) -> tuple[str, str]:
    """Repair one missing comparison slot without rerunning the whole matrix."""
    system_prompt, user_prompt = make_integration_prompt(case_id, raw_documents, singles, (spec,))
    return system_prompt, "\n".join((
        user_prompt,
        "这是一次比较槽位补全，只输出目标 claimKey，不得输出其他槽位或新增单术推演。",
        "目标 claimKey：" + spec["claimKey"],
        "上一版草稿（可能为空）：\n" + json.dumps(previous or {}, ensure_ascii=False),
    ))


def integration_analysis_from_draft(
    fixed: dict[str, Any], singles: dict[str, dict[str, Any]], draft: dict[str, Any],
) -> dict[str, Any]:
    """Seal a model's small comparison draft without letting it alter singles.

    All per-system evidence is copied from frozen reports.  The model therefore
    has no way to invent a Raw Data pointer or to make comparison output depend
    on a fragile, large JSON-schema response.
    """
    draft_by_key = _draft_claims_by_key(draft)
    claims: list[dict[str, Any]] = []
    for index, spec in enumerate(CLAIM_SPECS):
        source = draft_by_key.get(spec["claimKey"], {})
        source_missing = not bool(source)
        frozen_by_system: dict[str, dict[str, Any]] = {}
        aggregate_refs: list[dict[str, Any]] = []
        aggregate_theory: list[dict[str, Any]] = []
        for system in SYSTEMS:
            frozen_claims = singles[system].get("claims", [])
            frozen_claim = next((claim for claim in frozen_claims if claim.get("claimKey") == spec["claimKey"]), None)
            if frozen_claim is None:
                frozen_by_system[system] = {
                    "status": "insufficient-input", "analysisId": singles[system]["analysisId"], "frozen": True,
                    "conclusion": "该固定主题没有对应冻结结论，无法判断。", "rawDataRefs": [], "theory": [],
                    "inference": [], "uncertainty": ["单术报告缺少同一 claimKey，禁止按数组位置替代。"],
                }
                continue
            refs = frozen_claim.get("rawDataRefs", [])[:2]
            theory = frozen_claim.get("theory", [])[:2]
            inference = frozen_claim.get("inference", [])[:2]
            uncertainty = frozen_claim.get("uncertainty", {}).get("items", [])[:2]
            conclusion = frozen_claim.get("provisionalConclusion", {}).get("text", frozen_claim.get("claim", "无法判断"))
            frozen_by_system[system] = {
                "status": "analyzed", "analysisId": singles[system]["analysisId"], "frozen": True,
                "conclusion": conclusion, "rawDataRefs": refs, "theory": theory,
                "inference": inference, "uncertainty": uncertainty or ["原单术报告未提供该主题的不确定因素。"],
            }
            aggregate_refs.extend(refs[:1])
            aggregate_theory.extend(theory[:1])

        all_systems_available = all(item.get("status") == "analyzed" for item in frozen_by_system.values())
        status = source.get("status") if source.get("status") in {"aligned", "partial", "conflict", "insufficient"} else "insufficient"
        if not all_systems_available:
            status = "insufficient"
        agreements = _draft_list(source.get("agreements"), [])
        conflicts = _draft_list(source.get("conflicts"), [])
        if status == "aligned":
            # Alignment is a strict protocol term, so the deterministic wrapper
            # supplies all four matching dimensions only for that explicit state.
            comparison_basis = {"topicAligned": True, "directionAligned": True, "timeRangeAligned": True, "specificityAligned": True, "notes": ["模型将三份冻结结论判为同主题、同方向、同时间范围及同具体程度。"]}
            conflicts = []
        elif status == "conflict":
            conflicts = conflicts or ["三份冻结单术结论存在方向或具体程度差异，不能消解为统一解释。"]
            comparison_basis = {"topicAligned": True, "directionAligned": False, "timeRangeAligned": False, "specificityAligned": False, "notes": ["存在冲突，已原样保留。"]}
        elif status == "partial":
            comparison_basis = {"topicAligned": True, "directionAligned": False, "timeRangeAligned": False, "specificityAligned": False, "notes": ["仅部分维度可比较，不能视为完整一致。"]}
        else:
            agreements, conflicts = [], conflicts
            comparison_basis = {"topicAligned": False, "directionAligned": False, "timeRangeAligned": False, "specificityAligned": False, "notes": ["冻结结论不足以形成可比较的同一命题。"]}

        unable = status == "insufficient" or source_missing
        if not aggregate_refs:
            aggregate_refs = [
                {"system": system, "documentId": singles[system]["run"]["rawDataDocumentIds"][0], "pointer": ""}
                for system in SYSTEMS
            ]
        if not aggregate_theory:
            aggregate_theory = [{
                "system": "ziwei", "ruleId": "claim-key-insufficient",
                "statement": "同一固定主题缺少可比较的冻结单术结论，只能标记为无法判断。",
                "sourceRef": None, "schoolOrTradition": None,
            }]
        # The comparison surface must never become a fourth, stronger reading.
        # Its result is only the comparison state and retained differences.
        provisional = (
            "无法判断：本条结论生成待补全，比较草稿未形成该固定主题。" if source_missing else
            "无法判断：三术冻结结论不足以形成可比较结论。" if unable else
            "本入口不输出统一结论；请分别查看三术结论及其一致与冲突。"
        )
        claim_text = "无法判断：本条结论生成待补全。" if source_missing else f"{spec['group']}｜{spec['topic']}：三术比较结果（不输出统一结论）。"
        claims.append({
            "claimId": f"{fixed['caseId']}-integration-claim-{spec['claimKey'].replace('.', '-')}",
            "claimKey": spec["claimKey"],
            "claimScope": {"atomic": True, "topic": f"{spec['group']}｜{spec['topic']}", "direction": _draft_text(source.get("direction"), "比较状态待验证"), "timeRange": None},
            "claim": claim_text,
            "rawDataRefs": aggregate_refs,
            "theory": aggregate_theory,
            "inference": ["仅比较三份已冻结单术结论，未反向修改任何单术推演。"] + _draft_list(source.get("inference"), ["比较证据不足，无法判断。"]),
            "counterEvidence": {"status": "none-found", "searchSummary": "比较层不新增盘面反证；任何系统间差异均列入“保留的冲突”。", "items": []},
            "systemAnalyses": frozen_by_system,
            "comparisonBasis": comparison_basis,
            "consistency": {"status": status, "agreements": agreements, "conflicts": conflicts},
            "uncertainty": {"birthTimeSensitivity": "unknown", "items": _draft_list(source.get("uncertainty"), ["出生时间敏感性尚未完成跨时段扫描。", "比较结论不替代对各单术原报告的阅读。 "])},
            "validation": _draft_list(source.get("validation"), ["以非诱导方式分别核验三份单术所指向的现实经历，记录一致与差异。"]),
            "falsificationConditions": _draft_list(source.get("falsificationConditions"), ["若现实记录无法支持所比较的共同方向，或三术并非指向同一主题，则该比较结论应被推翻。"]),
            "evidenceGrade": "D" if unable else "C",
            "gradeRationale": {"rawDataDirectness": "moderate", "timeStability": "unknown", "theoryChain": "complete" if not unable else "weak", "counterEvidenceResistance": "unknown", "systemIndependence": "three-systems", "explanation": "比较层仅基于三份冻结单术结论；未完成出生时间稳定性扫描，故不高于 C。" if not unable else "比较草稿或冻结结论未形成可比命题，按协议只能为 D。"},
            "pollutionRisk": "P2",
            "pollutionRationale": "网站场景中用户可能已知部分经历；比较结果仅用于探索与后续验证。",
            "empiricalUse": "exploratory",
            "provisionalConclusion": {"status": "unable-to-judge" if unable else "conditional", "text": provisional, "basisSystems": [] if unable else list(SYSTEMS), "conflictRetained": status == "conflict"},
            "actionAdvice": {"status": "not-applicable", "items": [], "disclaimer": None},
        })
    return {**fixed, "claims": claims}


class LocalState:
    def __init__(self) -> None:
        self.store = EphemeralSessionStore()
        self.lock = Lock()
        self.audit_log = LocalAuditLog(LOCAL_AUDIT_PATH, retention=LOCAL_AUDIT_RETENTION)

    def new_case(self, session_id: str) -> str:
        return self.case_id(session_id)

    def case_id(self, session_id: str) -> str:
        self.store.get_birth_input(session_id)
        return "C" + hashlib.sha256(session_id.encode()).hexdigest()[:16]

    def close(self, session_id: str) -> None:
        self.store.close(session_id)
        self.audit_log.remove_session(session_id)


STATE = LocalState()


class AnalysisService:
    def _birth(self, data: dict[str, Any]) -> tuple[BirthInput, PlaceResolution]:
        try:
            place = str(data["place"])
            resolution = resolve_place(place)
            location = resolution.selected
            birth = BirthInput(
                date=str(data["date"]), time=str(data["time"]), place=place, gender=str(data["gender"]),
                timezone=str(location["timezone"]),
                latitude=float(location["latitude"]),
                longitude=float(location["longitude"]),
                bazi_time_standard=str(data.get("baziTimeStandard", "civil")),
            )
            birth.validate()
            return birth, resolution
        except (KeyError, TypeError, ValueError) as exc:
            if "unknown IANA timezone" in str(exc) or "timezone is required" in str(exc):
                raise ApiError(
                    422,
                    "invalid_timezone",
                    "时区必须是 IANA 标准名，不能填写城市名。例如山东潍坊请填写 Asia/Shanghai。",
                ) from exc
            if "longitude is required for true_solar" in str(exc):
                raise ApiError(422, "true_solar_coordinates_required", "启用真太阳时校正需要出生地经度；请先联网匹配地点或手动填写经纬度。") from exc
            raise ApiError(422, "invalid_birth_input", "出生信息格式无效。") from exc

    @staticmethod
    def _birth_audit_payload(session_id: str, case_id: str, birth: BirthInput, resolution: PlaceResolution) -> dict[str, Any]:
        return {
            "sessionId": session_id,
            "caseId": case_id,
            "birthInput": {
                "date": birth.date,
                "time": birth.time,
                "place": birth.place,
                "gender": birth.gender,
                "timezone": birth.timezone,
                "latitude": birth.latitude,
                "longitude": birth.longitude,
                "baziTimeStandard": birth.bazi_time_standard,
            },
            "locationResolution": resolution.audit,
        }

    def _single(self, session_id: str, system: str) -> dict[str, Any]:
        if system not in SYSTEMS:
            raise ApiError(404, "unknown_system", "未知分析体系。")
        total_steps = len(PROFESSIONAL_AUDIT_STAGES[system]) + 2
        checkpoint = STATE.store.get_checkpoint(session_id, system)
        signature = run_contract([])
        if checkpoint.get("signature") != signature:
            checkpoint = {"signature": signature, "professionalDrafts": [], "groups": {}}
        diagnostic = checkpoint.get("diagnostic") or {"key": system, "events": [], "repairs": []}
        diagnostic.update(status="running", failure=None)

        def freeze_checkpoint() -> None:
            checkpoint["diagnostic"] = diagnostic
            STATE.store.set_checkpoint(session_id, system, checkpoint)
            STATE.store.set_diagnostic(session_id, system, diagnostic)

        STATE.store.set_diagnostic(session_id, system, diagnostic)
        STATE.store.set_analysis_progress(session_id, system, progress_payload(
            system, current_step=1, total_steps=total_steps, label="正在校验输入并生成确定性排盘。", stage_id="calculation",
        ))
        try:
            birth, case_id = STATE.store.get_birth_input(session_id), STATE.case_id(session_id)
            raw = checkpoint.get("raw")
            if raw is None:
                raw = command_raw(system, birth, case_id)
                checkpoint["raw"] = raw
                freeze_checkpoint()
            document_id = f"{case_id}.{system}.raw.v1"
            raw_document = {"documentId": document_id, **raw}
            preview = chart_preview(system, raw)
            STATE.store.set_analysis_progress(session_id, system, progress_payload(
                system, current_step=1, total_steps=total_steps, label="确定性排盘已完成，正在进入专业审计。", preview=preview, stage_id="calculation",
            ))
            professional_drafts: list[dict[str, Any]] = checkpoint["professionalDrafts"]
            for index, stage in enumerate(PROFESSIONAL_AUDIT_STAGES[system], start=2):
                if index - 2 < len(professional_drafts):
                    continue
                STATE.store.set_analysis_progress(session_id, system, progress_payload(
                    system, current_step=index, total_steps=total_steps, label=f"正在审计：{stage['title']}。", preview=preview, stage_id=stage["id"],
                ))
                previous_frozen = professional_drafts[-1] if professional_drafts else None
                audit_system_prompt, audit_user_prompt = make_professional_audit_prompt(system, raw_document, stage, previous_frozen)
                stage_draft = qwen_json(
                    audit_system_prompt, audit_user_prompt, trace=diagnostic["events"],
                    context={"phase": "professional-audit", "stageId": stage["id"], "stageTitle": stage["title"]},
                )
                missing = _missing_stage_coverage(stage, stage_draft)
                for repair_number in range(1, 4):
                    if not missing:
                        break
                    diagnostic["repairs"].append({"phase": "professional-audit", "stageId": stage["id"], "repair": repair_number, "reason": "missingCoverage", "details": missing})
                    STATE.store.set_analysis_progress(session_id, system, progress_payload(
                        system, current_step=index, total_steps=total_steps, label=f"正在修复：{stage['title']}（第 {repair_number}/3 次）。", preview=preview, stage_id=stage["id"],
                    ))
                    repair_prompt = "\n".join((
                        audit_user_prompt,
                        "上次阶段草稿未通过完整路由门控，不能冻结。缺少标签：" + "、".join(f"[{label}]" for label in missing) + "。",
                        "请重新返回完整 JSON；保留已完成内容并补齐所有缺项。上次草稿：\n" + json.dumps(stage_draft, ensure_ascii=False),
                    ))
                    stage_draft = qwen_json(
                        audit_system_prompt, repair_prompt, trace=diagnostic["events"],
                        context={"phase": "professional-audit-repair", "stageId": stage["id"], "stageTitle": stage["title"], "repair": repair_number},
                    )
                    missing = _missing_stage_coverage(stage, stage_draft)
                if missing:
                    diagnostic["repairs"].append({"phase": "professional-audit", "stageId": stage["id"], "reason": "fallback-insufficient", "details": missing})
                    stage_draft = _stage_insufficient_fallback(stage, stage_draft, missing)
                # Appending seals this draft for the rest of the run. Later
                # stages receive it read-only and no code path mutates it.
                professional_drafts.append(stage_draft)
                freeze_checkpoint()
            professional_audit = professional_audit_from_drafts(system, raw_document, professional_drafts)
            final_claims: list[dict[str, Any]] = []
            for group_key, group_label, _ in CLAIM_GROUPS:
                if group_key in checkpoint["groups"]:
                    final_claims.extend(checkpoint["groups"][group_key])
                    continue
                STATE.store.set_analysis_progress(session_id, system, progress_payload(
                    system, current_step=total_steps, total_steps=total_steps,
                    label=f"正在汇总结论：{group_label}。", preview=preview, stage_id="summary",
                ))
                specs = claim_specs(group_key)
                system_prompt, user_prompt = make_single_prompt(system, case_id, raw_document, birth, professional_audit, specs)
                group_draft = qwen_json(
                    system_prompt, user_prompt, trace=diagnostic["events"],
                    context={"phase": "claim-summary", "groupKey": group_key, "groupLabel": group_label},
                )
                group_draft = _apply_claim_stage_routes(system, group_draft, specs, professional_audit)
                missing = _missing_claim_slots(group_draft, specs, professional_audit)
                unsafe = _high_stakes_claim_keys(group_draft, specs)
                excessive = _excessive_abstention_claim_keys(group_draft, specs, professional_audit)
                for repair_number in range(1, 4):
                    if not (missing or unsafe or excessive):
                        break
                    diagnostic["repairs"].append({"phase": "claim-summary", "groupKey": group_key, "repair": repair_number, "reason": "claimPolicy", "missingClaimKeys": missing, "unsafeClaimKeys": unsafe, "excessiveAbstentionClaimKeys": excessive})
                    STATE.store.set_analysis_progress(session_id, system, progress_payload(
                        system, current_step=total_steps, total_steps=total_steps,
                        label=f"正在修复结论：{group_label}（第 {repair_number}/3 次）。", preview=preview, stage_id="summary",
                    ))
                    repair_prompt = "\n".join((
                        user_prompt,
                        "上次结论草稿未通过门控。缺失槽位或未引用有效 sourceAuditIds：" + "、".join(missing or ["无"]) + "。",
                        "越界槽位（超出所属主题，或健康提醒组含直接/绝对性表述）：" + "、".join(unsafe or ["无"]) + "。非健康组删除健康类文本；健康提醒组改为委婉提醒和日常建议，不得作诊断或确定性判断。",
                        "过度保守弃权槽位（已存在合法冻结观察与推演，不能仅写无法判断）：" + "、".join(excessive or ["无"]) + "。此类槽位须改为有条件倾向或存在相反信号，不得新增冻结阶段以外的事实。",
                        "请只返回本组完整 JSON，保留合格内容并补齐或改写缺项。上次草稿：\n" + json.dumps(group_draft, ensure_ascii=False),
                    ))
                    group_draft = qwen_json(
                        system_prompt, repair_prompt, trace=diagnostic["events"],
                        context={"phase": "claim-summary-repair", "groupKey": group_key, "groupLabel": group_label, "repair": repair_number},
                    )
                    group_draft = _apply_claim_stage_routes(system, group_draft, specs, professional_audit)
                    missing = _missing_claim_slots(group_draft, specs, professional_audit)
                    unsafe = _high_stakes_claim_keys(group_draft, specs)
                    excessive = _excessive_abstention_claim_keys(group_draft, specs, professional_audit)
                # A missing audit reference is an output-quality issue, not a
                # statement about the chart. Repair only the affected slot so
                # completed conclusions remain frozen and the visitor never
                # pays for a full report rerun because of one bad reference.
                unresolved = list(dict.fromkeys([*missing, *unsafe, *excessive]))
                for claim_key in unresolved:
                    spec = next(spec for spec in specs if spec["claimKey"] == claim_key)
                    previous = _draft_claims_by_key(group_draft).get(claim_key)
                    reason = (
                        "缺少有效 sourceAuditIds" if claim_key in missing else
                        "触发主题或健康表达安全门控" if claim_key in unsafe else
                        "过度保守弃权：存在合法冻结观察与推演"
                    )
                    for repair_number in range(1, 3):
                        diagnostic["repairs"].append({
                            "phase": "claim-targeted-repair", "groupKey": group_key,
                            "claimKey": claim_key, "repair": repair_number, "reason": reason,
                        })
                        STATE.store.set_analysis_progress(session_id, system, progress_payload(
                            system, current_step=total_steps, total_steps=total_steps,
                            label=f"正在补全结论：{spec['topic']}（第 {repair_number}/2 次）。", preview=preview, stage_id="summary",
                        ))
                        targeted_system_prompt, targeted_user_prompt = make_targeted_claim_repair_prompt(
                            system, case_id, raw_document, birth, professional_audit, spec, previous, reason,
                        )
                        targeted_draft = qwen_json(
                            targeted_system_prompt, targeted_user_prompt, trace=diagnostic["events"],
                            context={"phase": "claim-targeted-repair", "groupKey": group_key, "claimKey": claim_key, "repair": repair_number},
                        )
                        targeted_draft = _apply_claim_stage_routes(system, targeted_draft, (spec,), professional_audit)
                        candidate = _draft_claims_by_key(targeted_draft).get(claim_key)
                        if candidate is not None:
                            candidate_draft = {"claims": [candidate]}
                            candidate_missing = _missing_claim_slots(candidate_draft, (spec,), professional_audit)
                            candidate_unsafe = _high_stakes_claim_keys(candidate_draft, (spec,))
                            candidate_excessive = _excessive_abstention_claim_keys(candidate_draft, (spec,), professional_audit)
                            if not candidate_missing and not candidate_unsafe and not candidate_excessive:
                                group_draft = _replace_claim_draft(group_draft, candidate)
                                break
                        previous = candidate
                    # The final check below records any remaining pipeline gap
                    # for diagnostics; it is never presented as chart evidence.
                minimum_usable = MODULE_MINIMUM_USABLE_CLAIMS.get(group_key)
                if minimum_usable is not None and _usable_draft_claim_count(group_draft, specs, professional_audit) < minimum_usable:
                    for repair_number in range(1, 3):
                        diagnostic["repairs"].append({
                            "phase": "module-coverage-repair", "groupKey": group_key,
                            "repair": repair_number, "minimumUsableClaims": minimum_usable,
                        })
                        module_prompt = "\n".join((
                            user_prompt,
                            f"上一版{group_label}模块可展示结论不足。请只基于本主题已冻结阶段重新汇总，目标是形成至少 {minimum_usable} 条彼此独立、可验证且不过度具体的结论。",
                            "关系模块优先给长期互动模式与稳定条件；家庭模块优先给互动、支持责任与居住变化的条件式模式；健康模块按传统象意、生活情境、日常照护三类分别表达。阶段趋势以已给出的报告日期、未来12个月和未来3年为时间框架。只有真实输入或冻结阶段不足时才写无法判断，严禁为达到数量而推测。",
                            "上一版模块草稿：\n" + json.dumps(group_draft, ensure_ascii=False),
                        ))
                        module_draft = qwen_json(
                            system_prompt, module_prompt, trace=diagnostic["events"],
                            context={"phase": "module-coverage-repair", "groupKey": group_key, "repair": repair_number},
                        )
                        module_draft = _apply_claim_stage_routes(system, module_draft, specs, professional_audit)
                        existing_by_key = _draft_claims_by_key(group_draft)
                        for claim_key, candidate in _draft_claims_by_key(module_draft).items():
                            spec = next(spec for spec in specs if spec["claimKey"] == claim_key)
                            candidate_draft = {"claims": [candidate]}
                            if (
                                _missing_claim_slots(candidate_draft, (spec,), professional_audit)
                                or _high_stakes_claim_keys(candidate_draft, (spec,))
                                or _excessive_abstention_claim_keys(candidate_draft, (spec,), professional_audit)
                            ):
                                continue
                            existing = existing_by_key.get(claim_key)
                            if existing is not None and not _claim_says_unable_to_judge(existing) and _claim_says_unable_to_judge(candidate):
                                continue
                            group_draft = _replace_claim_draft(group_draft, candidate)
                        if _usable_draft_claim_count(group_draft, specs, professional_audit) >= minimum_usable:
                            break
                missing = _missing_claim_slots(group_draft, specs, professional_audit)
                unsafe = _high_stakes_claim_keys(group_draft, specs)
                excessive = _excessive_abstention_claim_keys(group_draft, specs, professional_audit)
                if missing or unsafe:
                    diagnostic["repairs"].append({"phase": "claim-summary", "groupKey": group_key, "reason": "generation-incomplete", "missingClaimKeys": missing, "unsafeClaimKeys": unsafe, "excessiveAbstentionClaimKeys": excessive})
                    group_draft = {"claims": [
                        claim for claim in (group_draft.get("claims") if isinstance(group_draft.get("claims"), list) else [])
                        if isinstance(claim, dict) and claim.get("claimKey") not in set(unsafe)
                    ]}
                elif excessive:
                    # This is an explicit, evidence-backed abstention that the
                    # targeted repairs could not improve.  Preserve it for the
                    # audit instead of mislabelling it as a technical gap.
                    diagnostic["repairs"].append({"phase": "claim-summary", "groupKey": group_key, "reason": "abstention-retained", "excessiveAbstentionClaimKeys": excessive})
                group_claims = group_draft.get("claims", []) if isinstance(group_draft.get("claims"), list) else []
                checkpoint["groups"][group_key] = group_claims
                freeze_checkpoint()
                final_claims.extend(group_claims)
            draft = {"claims": final_claims}
            analysis = single_analysis_from_draft(system, single_fixed_metadata(system, case_id, document_id, birth), raw_document, draft, professional_audit)
            issues = validate_analysis(analysis, {document_id: raw_document})
            if issues:
                raise ApiError(502, "analysis_contract_invalid", "模型输出未通过研究协议校验；未保存结果。", [issue.__dict__ for issue in issues[:8]])
            diagnostic["qualityGate"] = report_quality_summary(analysis)
            STATE.store.record_single_analysis(session_id, system, analysis, raw_document=raw_document)
            diagnostic["status"] = "completed-with-repairs" if diagnostic["repairs"] else "completed"
            STATE.store.set_diagnostic(session_id, system, diagnostic)
            STATE.store.set_analysis_progress(session_id, system, progress_payload(
                system, current_step=total_steps, total_steps=total_steps, label="分析完成，报告已冻结。", state="completed", preview=preview, stage_id="summary",
            ))
            response: dict[str, Any] = {"analysis": analysis, "comparison": STATE.store.comparison_status(session_id)}
            if system == "bazi":
                audit = raw["inputAudit"]
                response["calculationAudit"] = {
                    "timeStandard": raw["ruleProfile"]["timeStandard"],
                    "civilDatetime": audit["originalLocalDatetime"],
                    "calculationDatetime": audit["calculationLocalDatetime"],
                    "trueSolarCorrectionMinutes": audit["trueSolarCorrectionMinutes"],
                }
            return response
        except Exception as exc:
            try:
                diagnostic["status"] = "failed"
                diagnostic["failure"] = (
                    {"code": exc.code, "message": exc.message, "details": exc.details}
                    if isinstance(exc, ApiError) else
                    {"code": type(exc).__name__, "message": str(exc)}
                )
                checkpoint["diagnostic"] = diagnostic
                STATE.store.set_checkpoint(session_id, system, checkpoint)
                STATE.store.set_diagnostic(session_id, system, diagnostic)
                previous = STATE.store.get_analysis_progress(session_id, system)
                STATE.store.set_analysis_progress(session_id, system, {**previous, "state": "failed", "label": "分析未完成；失败报告已保存，可在当前页面查看。"})
            except SessionNotFound:
                pass
            raise

    def _comparison(self, session_id: str) -> dict[str, Any]:
        status = STATE.store.comparison_status(session_id)
        if status["status"] != "ready":
            raise ApiError(409, "comparison_locked", status["reason"], status)
        raw_documents = STATE.store.get_raw_documents(session_id)
        singles = STATE.store.get_single_analyses(session_id)
        checkpoint = STATE.store.get_checkpoint(session_id, "integration")
        if checkpoint.get("signature") != run_contract([]):
            checkpoint = {"signature": run_contract([]), "groups": {}}
        diagnostic = STATE.store.get_diagnostic(session_id, "integration")
        diagnostic.update(status="running")

        def comparison_json(system_prompt, user_prompt, *, context):
            try:
                return qwen_json(system_prompt, user_prompt, trace=diagnostic["events"], context=context)
            finally:
                # Preserve the current raw reply even if parsing or a later
                # group fails; previous frozen groups remain untouched.
                STATE.store.set_diagnostic(session_id, "integration", diagnostic)

        comparison_claims: list[dict[str, Any]] = []
        for index, (group_key, group_label, _) in enumerate(CLAIM_GROUPS, start=1):
            if group_key in checkpoint["groups"]:
                comparison_claims.extend(checkpoint["groups"][group_key])
                continue
            STATE.store.set_analysis_progress(session_id, "integration", {
                "system": "integration", "state": "running", "currentStep": index, "totalSteps": len(CLAIM_GROUPS),
                "label": f"正在比较：{group_label}。",
            })
            specs = claim_specs(group_key)
            system_prompt, user_prompt = make_integration_prompt(STATE.case_id(session_id), raw_documents, singles, specs)
            group_draft = comparison_json(system_prompt, user_prompt, context={"phase": "comparison", "groupKey": group_key})
            missing = [spec["claimKey"] for spec in specs if spec["claimKey"] not in _draft_claims_by_key(group_draft)]
            if missing:
                repair_prompt = "\n".join((
                    user_prompt,
                    "上次比较草稿缺少固定 claimKey：" + "、".join(missing) + "。请返回本组完整 JSON，不得遗漏。",
                    "上次草稿：\n" + json.dumps(group_draft, ensure_ascii=False),
                ))
                group_draft = comparison_json(system_prompt, repair_prompt, context={"phase": "comparison-repair", "groupKey": group_key})
                missing = [spec["claimKey"] for spec in specs if spec["claimKey"] not in _draft_claims_by_key(group_draft)]
            # A missing comparison slot is a model-output gap. Repair it in
            # isolation; if it still cannot be completed, the deterministic
            # wrapper marks it as generation-incomplete and the user report
            # hides it instead of failing the entire comparison at the end.
            for claim_key in missing:
                spec = next(spec for spec in specs if spec["claimKey"] == claim_key)
                previous = _draft_claims_by_key(group_draft).get(claim_key)
                for _ in range(2):
                    targeted_system_prompt, targeted_user_prompt = make_targeted_integration_repair_prompt(
                        STATE.case_id(session_id), raw_documents, singles, spec, previous,
                    )
                    targeted_draft = comparison_json(targeted_system_prompt, targeted_user_prompt, context={"phase": "comparison-targeted-repair", "claimKey": claim_key})
                    candidate = _draft_claims_by_key(targeted_draft).get(claim_key)
                    if candidate is not None:
                        group_draft = _replace_claim_draft(group_draft, candidate)
                        break
                    previous = candidate
            group_claims = group_draft.get("claims", []) if isinstance(group_draft.get("claims"), list) else []
            checkpoint["groups"][group_key] = group_claims
            STATE.store.set_checkpoint(session_id, "integration", checkpoint)
            STATE.store.set_diagnostic(session_id, "integration", diagnostic)
            comparison_claims.extend(group_claims)
        draft = {"claims": comparison_claims}
        analysis = integration_analysis_from_draft(
            integration_fixed_metadata(STATE.case_id(session_id), raw_documents, singles), singles, draft,
        )
        issues = validate_analysis(analysis, raw_documents)
        if issues:
            raise ApiError(502, "analysis_contract_invalid", "比较报告未通过研究协议校验；未保存结果。", [issue.__dict__ for issue in issues[:8]])
        STATE.store.record_integration_analysis(session_id, analysis)
        diagnostic.update(status="completed")
        STATE.store.set_diagnostic(session_id, "integration", diagnostic)
        return {"analysis": analysis, "comparison": STATE.store.comparison_status(session_id)}

def main() -> None:
    import uvicorn
    parser = argparse.ArgumentParser(description="Astrology research service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8788")))
    args = parser.parse_args()
    uvicorn.run("src.mvp.web_api:app", host=args.host, port=args.port, workers=1, access_log=False)


if __name__ == "__main__":
    main()
