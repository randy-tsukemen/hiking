from datetime import date

from yama.weather import DayForecast, rate_day


def _fc(**kw) -> DayForecast:
    base = dict(
        day=date(2026, 8, 1),
        t_max=15.0,
        t_min=8.0,
        rain_prob=10,
        precip_mm=0.0,
        wind_max=3.0,
        weather_code=1,
    )
    base.update(kw)
    return DayForecast(**base)


def test_perfect_day_is_best_grade():
    s = rate_day(_fc())
    assert s.grade == "◎"
    assert s.score == 100


def test_thunderstorm_is_zero():
    s = rate_day(_fc(weather_code=95))
    assert s.grade == "×"
    assert s.score == 0


def test_heavy_rain_prob_downgrades():
    s = rate_day(_fc(rain_prob=80))
    assert s.score <= 50
    assert "降雨機率80%" in s.reason


def test_strong_wind_downgrades():
    s = rate_day(_fc(wind_max=16.0))
    assert s.grade in ("△", "○")
    assert "強風" in s.reason


def test_beginner_threshold_is_looser():
    windy = _fc(wind_max=16.0)
    assert rate_day(windy, "初級").score > rate_day(windy, "中級").score


def test_freezing_min_temp_penalized():
    s = rate_day(_fc(t_min=-6.0))
    assert s.score < 100
    assert "低溫" in s.reason


def test_score_never_negative():
    s = rate_day(
        _fc(rain_prob=100, precip_mm=50, wind_max=25, t_min=-10, weather_code=65)
    )
    assert s.score == 0
    assert s.grade == "×"
