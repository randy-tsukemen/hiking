"""Open-Meteo 山頂天氣預報 + 登山適宜度評分。

Open-Meteo 免費、無需 API key，支援以 latitude/longitude/elevation 查詢
（elevation 直接用山頂標高，預報會依標高修正氣溫）。
支援一次查多點（逗號分隔），16 天 daily 預報。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import httpx

API_URL = "https://api.open-meteo.com/v1/forecast"
FORECAST_DAYS = 16
_CACHE_DIR = Path.home() / ".yama_cache"
_CACHE_TTL = 3600  # 1 小時

DAILY_FIELDS = (
    "temperature_2m_max,temperature_2m_min,"
    "precipitation_probability_max,precipitation_sum,"
    "wind_speed_10m_max,weather_code"
)

# WMO weather code → 摘要
_WMO = {
    0: "晴", 1: "大致晴", 2: "多雲時晴", 3: "陰",
    45: "霧", 48: "霧凇",
    51: "毛毛雨", 53: "毛毛雨", 55: "毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "凍雨", 67: "凍雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "霰",
    80: "陣雨", 81: "陣雨", 82: "強陣雨",
    85: "陣雪", 86: "強陣雪",
    95: "雷雨", 96: "雷雨夾雹", 99: "強雷雨",
}


@dataclass
class DayForecast:
    day: date
    t_max: float
    t_min: float
    rain_prob: int  # %
    precip_mm: float
    wind_max: float  # m/s（Open-Meteo 預設 km/h，這裡已換算）
    weather_code: int

    @property
    def summary(self) -> str:
        return _WMO.get(self.weather_code, f"code{self.weather_code}")

    @property
    def is_weekend(self) -> bool:
        return self.day.weekday() >= 5


@dataclass
class Suitability:
    score: int  # 0-100
    grade: str  # ◎ ○ △ ×
    reason: str


def rate_day(fc: DayForecast, difficulty: str = "中級") -> Suitability:
    """登山適宜度評分。規則透明：

    - 降雨機率與降水量為主要因子，風速次之，低溫（結冰風險）扣分
    - 雷雨（WMO 95+）直接 ×
    - difficulty=初級 時風雨門檻放寬一級
    """
    hard = difficulty != "初級"
    score = 100
    reasons: list[str] = []

    # 預報末端（第 16 天）Open-Meteo 可能缺欄位
    if None in (fc.rain_prob, fc.precip_mm, fc.wind_max, fc.t_min, fc.weather_code):
        return Suitability(0, "—", "資料不全")

    if fc.weather_code >= 95:
        return Suitability(0, "×", "雷雨風險")

    # 降雨機率
    if fc.rain_prob >= 70:
        score -= 50
        reasons.append(f"降雨機率{fc.rain_prob}%")
    elif fc.rain_prob >= 50:
        score -= 30
        reasons.append(f"降雨機率{fc.rain_prob}%")
    elif fc.rain_prob >= 30:
        score -= 15
        reasons.append(f"降雨機率{fc.rain_prob}%")

    # 降水量
    if fc.precip_mm >= 20:
        score -= 30
        reasons.append(f"降水{fc.precip_mm:.0f}mm")
    elif fc.precip_mm >= 5:
        score -= 15

    # 風速（山頂 10m 最大風速，m/s）
    wind_bad = 15 if hard else 18
    wind_warn = 8 if hard else 10
    if fc.wind_max >= wind_bad:
        score -= 40
        reasons.append(f"強風{fc.wind_max:.0f}m/s")
    elif fc.wind_max >= wind_warn:
        score -= 15
        reasons.append(f"風{fc.wind_max:.0f}m/s")

    # 低溫（山頂結冰、失溫風險）
    if fc.t_min <= -5:
        score -= 20
        reasons.append(f"低溫{fc.t_min:.0f}°C")
    elif fc.t_min <= 0:
        score -= 10

    score = max(0, min(100, score))
    if score >= 75:
        grade = "◎"
    elif score >= 55:
        grade = "○"
    elif score >= 35:
        grade = "△"
    else:
        grade = "×"
    return Suitability(score, grade, "、".join(reasons) or "好天氣")


def _cache_path(key: str) -> Path:
    return _CACHE_DIR / f"weather_{key}.json"


def _read_cache(key: str) -> list[dict] | None:
    p = _cache_path(key)
    try:
        if time.time() - p.stat().st_mtime < _CACHE_TTL:
            return json.loads(p.read_text())
    except (OSError, ValueError):
        pass
    return None


def _write_cache(key: str, data: list[dict]) -> None:
    try:
        _CACHE_DIR.mkdir(exist_ok=True)
        _cache_path(key).write_text(json.dumps(data))
    except OSError:
        pass


def get_forecasts(
    points: list[tuple[float, float, int]],
) -> list[list[DayForecast]]:
    """批次查詢多座山的 16 天預報。points = [(lat, lon, elevation), ...]。

    Open-Meteo 支援逗號分隔多點，一次請求全部取回；結果有 1 小時本地快取。
    """
    if not points:
        return []
    key = "-".join(f"{la:.3f}_{lo:.3f}_{el}" for la, lo, el in points)
    raw = _read_cache(key)
    if raw is None:
        params = {
            "latitude": ",".join(f"{p[0]:.4f}" for p in points),
            "longitude": ",".join(f"{p[1]:.4f}" for p in points),
            "elevation": ",".join(str(p[2]) for p in points),
            "daily": DAILY_FIELDS,
            "forecast_days": FORECAST_DAYS,
            "timezone": "Asia/Tokyo",
            "wind_speed_unit": "ms",
        }
        resp = httpx.get(API_URL, params=params, timeout=20.0)
        resp.raise_for_status()
        data = resp.json()
        raw = data if isinstance(data, list) else [data]
        _write_cache(key, raw)

    results: list[list[DayForecast]] = []
    for loc in raw:
        daily = loc["daily"]
        days: list[DayForecast] = []
        for i, ds in enumerate(daily["time"]):
            days.append(
                DayForecast(
                    day=date.fromisoformat(ds),
                    t_max=daily["temperature_2m_max"][i],
                    t_min=daily["temperature_2m_min"][i],
                    rain_prob=int(daily["precipitation_probability_max"][i] or 0),
                    precip_mm=float(daily["precipitation_sum"][i] or 0),
                    wind_max=float(daily["wind_speed_10m_max"][i] or 0),
                    weather_code=int(daily["weather_code"][i] or 0),
                )
            )
        results.append(days)
    return results


def get_forecast(lat: float, lon: float, elevation: int) -> list[DayForecast]:
    return get_forecasts([(lat, lon, elevation)])[0]
