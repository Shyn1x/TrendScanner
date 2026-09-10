import lower_tf_shadow as shadow


def test_context_anchor_uses_latest_fully_closed_4h():
    assert shadow.context_4h_timestamp(39_600_000, "1h") == 28_800_000
    assert shadow.context_4h_timestamp(42_300_000, "15m") == 28_800_000
    assert shadow.context_4h_timestamp(28_800_000, "1h") == 14_400_000


def test_observer_preserves_lower_timeframe_and_anchor(monkeypatch):
    captured = {}

    def fake_observe(event, **kwargs):
        captured["event"] = event
        captured.update(kwargs)
        return {"status": "TRANSITION", "inserted": True}

    monkeypatch.setattr(shadow.lower_tf_storage, "observe", fake_observe)
    candidate = {
        "ready": True,
        "ready_timestamp": 42_300_000,
        "symbol": "BTC/USDT",
        "timeframe": "15m",
        "direction": "LONG",
        "production_decision": "WATCH",
        "decision_score": 55.0,
        "confidence": 65.0,
    }

    result = shadow.observe_lower_tf(candidate, database_url="postgres://example", observed_at_ms=43_200_000)
    assert result["shadow_status"] == "TRANSITION"
    assert result["target_4h_timestamp"] == 28_800_000
    assert captured["identity"][:4] == (
        shadow.EXPERIMENT_VERSION,
        "BTC/USDT",
        "15m",
        "LONG",
    )
    assert captured["event"]["market_context"]["target_4h_timestamp"] == 28_800_000
    assert captured["event"]["market_context"]["classification_at_collection"] is False


def test_invalid_timeframe_fails_safe():
    result = shadow.observe_lower_tf(
        {
            "ready": False,
            "ready_timestamp": 0,
            "symbol": "BTC/USDT",
            "timeframe": "5m",
            "direction": "LONG",
        },
        observed_at_ms=100_000_000,
    )
    assert result["shadow_status"] == "UNAVAILABLE"
