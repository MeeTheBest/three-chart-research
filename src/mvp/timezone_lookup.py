"""Deterministic city-centre fallback for Chinese birthplace selection.

The website selector supplies a province and city. This module first handles
special administrative regions explicitly, then searches the bundled GeoNames
city reference distributed with the local Vedic runtime. Returned coordinates
are city-centre references: suitable for a transparent true-solar estimate,
but never asserted to be a hospital or street-level birthplace.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from importlib.util import find_spec
from pathlib import Path

from pypinyin import Style, lazy_pinyin


ROOT = Path(__file__).resolve().parents[2]
_JHORA = find_spec("jhora")
GEONAMES_REFERENCE = Path(_JHORA.origin).parent / "data" / "geonames_places_5k.csv" if _JHORA and _JHORA.origin else ROOT / "data" / "geonames_places_5k.csv"


@dataclass(frozen=True)
class PlaceMatch:
    display_name: str
    timezone: str
    latitude: float | None = None
    longitude: float | None = None
    source: str = "本地城市中心坐标库"
    coordinate_precision: str = "city-center-reference"

    def to_dict(self) -> dict[str, object]:
        return {
            "displayName": self.display_name,
            "timezone": self.timezone,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "source": self.source,
            "coordinatePrecision": self.coordinate_precision,
        }


# Special regions use their own IANA zones. Municipalities are included here
# so that no network request is required for a deterministic city-centre match.
DIRECT_CITY_MATCHES: tuple[tuple[tuple[str, ...], PlaceMatch], ...] = (
    (("北京", "北京市"), PlaceMatch("北京 · 中国", "Asia/Shanghai", 39.9075, 116.39723)),
    (("上海", "上海市"), PlaceMatch("上海 · 中国", "Asia/Shanghai", 31.22222, 121.45806)),
    (("天津", "天津市"), PlaceMatch("天津 · 中国", "Asia/Shanghai", 39.14222, 117.17667)),
    (("重庆", "重庆市"), PlaceMatch("重庆 · 中国", "Asia/Shanghai", 29.56026, 106.55771)),
    (("香港", "香港特别行政区"), PlaceMatch("香港 · 中国香港", "Asia/Hong_Kong", 22.3193, 114.1694)),
    (("澳门", "澳門", "澳门特别行政区"), PlaceMatch("澳门 · 中国澳门", "Asia/Macau", 22.1987, 113.5439)),
    (("高雄",), PlaceMatch("高雄 · 中国台湾", "Asia/Taipei", 22.6273, 120.3014)),
    (("台北", "臺北"), PlaceMatch("台北 · 中国台湾", "Asia/Taipei", 25.0330, 121.5654)),
)

# A final selector-safe reference for the few administrative areas absent from
# the GeoNames city table. It keeps a temporary network outage from blocking a
# report. The precision is deliberately lower and remains visible in Raw Data.
PROVINCE_REFERENCES: tuple[tuple[tuple[str, ...], PlaceMatch], ...] = (
    (("河北",), PlaceMatch("河北省级参考点 · 中国", "Asia/Shanghai", 38.0428, 114.5149, "本地省级参考坐标", "province-reference")),
    (("山西",), PlaceMatch("山西省级参考点 · 中国", "Asia/Shanghai", 37.8706, 112.5489, "本地省级参考坐标", "province-reference")),
    (("内蒙古",), PlaceMatch("内蒙古自治区级参考点 · 中国", "Asia/Shanghai", 40.8173, 111.7652, "本地省级参考坐标", "province-reference")),
    (("辽宁",), PlaceMatch("辽宁省级参考点 · 中国", "Asia/Shanghai", 41.8057, 123.4315, "本地省级参考坐标", "province-reference")),
    (("吉林",), PlaceMatch("吉林省级参考点 · 中国", "Asia/Shanghai", 43.8171, 125.3235, "本地省级参考坐标", "province-reference")),
    (("黑龙江",), PlaceMatch("黑龙江省级参考点 · 中国", "Asia/Shanghai", 45.8038, 126.5349, "本地省级参考坐标", "province-reference")),
    (("江苏",), PlaceMatch("江苏省级参考点 · 中国", "Asia/Shanghai", 32.0603, 118.7969, "本地省级参考坐标", "province-reference")),
    (("浙江",), PlaceMatch("浙江省级参考点 · 中国", "Asia/Shanghai", 30.2741, 120.1551, "本地省级参考坐标", "province-reference")),
    (("安徽",), PlaceMatch("安徽省级参考点 · 中国", "Asia/Shanghai", 31.8206, 117.2272, "本地省级参考坐标", "province-reference")),
    (("福建",), PlaceMatch("福建省级参考点 · 中国", "Asia/Shanghai", 26.0745, 119.2965, "本地省级参考坐标", "province-reference")),
    (("江西",), PlaceMatch("江西省级参考点 · 中国", "Asia/Shanghai", 28.6820, 115.8579, "本地省级参考坐标", "province-reference")),
    (("山东",), PlaceMatch("山东省级参考点 · 中国", "Asia/Shanghai", 36.6512, 117.1201, "本地省级参考坐标", "province-reference")),
    (("河南",), PlaceMatch("河南省级参考点 · 中国", "Asia/Shanghai", 34.7466, 113.6254, "本地省级参考坐标", "province-reference")),
    (("湖北",), PlaceMatch("湖北省级参考点 · 中国", "Asia/Shanghai", 30.5928, 114.3055, "本地省级参考坐标", "province-reference")),
    (("湖南",), PlaceMatch("湖南省级参考点 · 中国", "Asia/Shanghai", 28.2282, 112.9388, "本地省级参考坐标", "province-reference")),
    (("广东",), PlaceMatch("广东省级参考点 · 中国", "Asia/Shanghai", 23.1291, 113.2644, "本地省级参考坐标", "province-reference")),
    (("广西",), PlaceMatch("广西壮族自治区级参考点 · 中国", "Asia/Shanghai", 22.8170, 108.3669, "本地省级参考坐标", "province-reference")),
    (("海南",), PlaceMatch("海南省级参考点 · 中国", "Asia/Shanghai", 20.0440, 110.1999, "本地省级参考坐标", "province-reference")),
    (("四川",), PlaceMatch("四川省级参考点 · 中国", "Asia/Shanghai", 30.5728, 104.0668, "本地省级参考坐标", "province-reference")),
    (("贵州",), PlaceMatch("贵州省级参考点 · 中国", "Asia/Shanghai", 26.6470, 106.6302, "本地省级参考坐标", "province-reference")),
    (("云南",), PlaceMatch("云南省级参考点 · 中国", "Asia/Shanghai", 25.0389, 102.7183, "本地省级参考坐标", "province-reference")),
    (("西藏",), PlaceMatch("西藏自治区级参考点 · 中国", "Asia/Shanghai", 29.6520, 91.1721, "本地省级参考坐标", "province-reference")),
    (("陕西",), PlaceMatch("陕西省级参考点 · 中国", "Asia/Shanghai", 34.3416, 108.9398, "本地省级参考坐标", "province-reference")),
    (("甘肃",), PlaceMatch("甘肃省级参考点 · 中国", "Asia/Shanghai", 36.0611, 103.8343, "本地省级参考坐标", "province-reference")),
    (("青海",), PlaceMatch("青海省级参考点 · 中国", "Asia/Shanghai", 36.6171, 101.7782, "本地省级参考坐标", "province-reference")),
    (("宁夏",), PlaceMatch("宁夏回族自治区级参考点 · 中国", "Asia/Shanghai", 38.4872, 106.2309, "本地省级参考坐标", "province-reference")),
    (("新疆",), PlaceMatch("新疆维吾尔自治区级参考点 · 中国", "Asia/Shanghai", 43.8256, 87.6168, "本地省级参考坐标", "province-reference")),
    (("台湾", "台灣"), PlaceMatch("台湾省级参考点 · 中国台湾", "Asia/Taipei", 23.6978, 120.9605, "本地省级参考坐标", "province-reference")),
)


def _normalise(value: str) -> str:
    return "".join(value.lower().split()).replace("-", "")


def _romanise(value: str) -> str:
    return "".join(lazy_pinyin(value, style=Style.NORMAL)).lower().replace("-", "").replace(" ", "")


def _split_selected_place(place: str) -> tuple[str, str]:
    """Return a rough province and selected city from selector text."""
    compact = "".join(place.strip().split())
    for marker in ("特别行政区", "自治区", "省"):
        if marker in compact:
            province, city = compact.rsplit(marker, 1)
            return province + marker, _strip_city_suffix(city)
    return "", _strip_city_suffix(compact)


def _strip_city_suffix(value: str) -> str:
    for suffix in ("市辖区", "地区", "市", "县", "區", "区"):
        if value.endswith(suffix):
            return value[: -len(suffix)]
    return value


@lru_cache(maxsize=1)
def _china_city_index() -> dict[str, tuple[tuple[str, str, float, float, bool], ...]]:
    """Index the installed, deterministic local GeoNames city reference."""
    index: dict[str, list[tuple[str, str, float, float, bool]]] = {}
    if not GEONAMES_REFERENCE.exists():
        return {}
    with GEONAMES_REFERENCE.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("country") != "China":
                continue
            try:
                latitude, longitude = float(row["latitude"]), float(row["longitude"])
            except (KeyError, TypeError, ValueError):
                continue
            state = str(row.get("state") or "")
            name = str(row.get("place_name") or "")
            aliases = str(row.get("alternate_names") or "").split("|")
            for position, candidate in enumerate((name, *aliases)):
                key = _normalise(candidate)
                if len(key) < 3:
                    continue
                entry = (name, state, latitude, longitude, position == 0)
                bucket = index.setdefault(key, [])
                if entry not in bucket:
                    bucket.append(entry)
    return {key: tuple(value) for key, value in index.items()}


PROVINCE_ENGLISH_NAMES = {
    "内蒙古": "Inner Mongolia", "广西": "Guangxi", "西藏": "Tibet", "宁夏": "Ningxia", "新疆": "Xinjiang", "陕西": "Shaanxi",
}


def _city_query_keys(city: str) -> tuple[str, ...]:
    values = [f"{city}市", city, f"{city}地区"]
    if city.endswith(("自治州", "地区", "盟")):
        values.extend(city[:length] for length in (4, 3, 2) if len(city) >= length)
    return tuple(dict.fromkeys(_romanise(value) for value in values if value))


def _province_state_key(province: str) -> str:
    short = province.replace("省", "").replace("市", "").split("自治区", 1)[0]
    for prefix, english_name in PROVINCE_ENGLISH_NAMES.items():
        if short.startswith(prefix):
            return _normalise(english_name)
    return _romanise(short)


def resolve_china_city_center(place: str) -> PlaceMatch | None:
    """Return an exact, local city-centre fallback for a Chinese selector value."""
    compact = _normalise(place)
    if not compact:
        return None
    for aliases, match in DIRECT_CITY_MATCHES:
        if any(_normalise(alias) in compact for alias in aliases):
            return match

    province, city = _split_selected_place(place)
    if not city:
        return None
    index = _china_city_index()
    candidate_pairs = [(key, index.get(key, ())) for key in _city_query_keys(city)]
    candidate_pairs = [(key, candidates) for key, candidates in candidate_pairs if candidates]
    if not candidate_pairs:
        return None
    province_key = _province_state_key(province)
    for _, possible in candidate_pairs:
        in_province = [candidate for candidate in possible if not province_key or _normalise(candidate[1]) == province_key]
        unique = {(name, state, latitude, longitude) for name, state, latitude, longitude, _ in in_province}
        if len(unique) == 1:
            name, state, latitude, longitude = next(iter(unique))
            return PlaceMatch(
                display_name=f"{name} · {state} · 中国",
                timezone="Asia/Shanghai",
                latitude=latitude,
                longitude=longitude,
                source="本地 GeoNames 城市中心坐标库",
            )
    candidates = tuple(candidate for _, possible in candidate_pairs for candidate in possible)
    if province_key:
        same_province = [candidate for candidate in candidates if _normalise(candidate[1]) == province_key]
        if same_province:
            candidates = tuple(same_province)
    candidates = tuple(dict.fromkeys((name, state, latitude, longitude, primary) for name, state, latitude, longitude, primary in candidates))
    primary = [candidate for candidate in candidates if candidate[4]]
    if primary:
        candidates = tuple(primary)
    if len(candidates) != 1:
        return None
    name, state, latitude, longitude, _ = candidates[0]
    return PlaceMatch(
        display_name=f"{name} · {state} · 中国",
        timezone="Asia/Shanghai",
        latitude=latitude,
        longitude=longitude,
        source="本地 GeoNames 城市中心坐标库",
    )


def resolve_domestic_reference(place: str) -> PlaceMatch | None:
    """Always resolve a supported domestic selector value without networking."""
    exact = resolve_china_city_center(place)
    if exact is not None:
        return exact
    province, _ = _split_selected_place(place)
    compact = _normalise(province or place)
    for aliases, match in PROVINCE_REFERENCES:
        if any(_normalise(alias) in compact for alias in aliases):
            return match
    return None
