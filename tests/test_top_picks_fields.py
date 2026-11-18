"""Row fields behind the web app's Top picks panel (service.recent_and_forecast)."""
from ski.service import recent_and_forecast


def test_recent_and_forecast_flattens_the_card():
    card = {"conditions": {"fresh_24h": 2.0, "fresh_48h": 5.0, "fresh_72h": 9.5},
            "forecast_horizons": [{"horizon_hours": 24, "inches": 1.0},
                                  {"horizon_hours": 48, "inches": 6.0},
                                  {"horizon_hours": 72, "inches": 11.0}]}
    assert recent_and_forecast(card) == {
        "fresh_48h": 5.0, "fresh_72h": 9.5,
        "forecast_24h": 1.0, "forecast_48h": 6.0, "forecast_72h": 11.0}


def test_no_forecast_is_none_not_zero():
    out = recent_and_forecast({"conditions": {}, "forecast_horizons": None})
    assert out == {"fresh_48h": None, "fresh_72h": None,
                   "forecast_24h": None, "forecast_48h": None, "forecast_72h": None}
