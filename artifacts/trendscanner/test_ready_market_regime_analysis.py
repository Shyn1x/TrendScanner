from __future__ import annotations
import copy
import pandas as pd
from ready_market_regime_analysis import _frame_features, _metrics, _attach_market, classify_regime, _hypothesis, analyze_market_regimes

def _frame(count=250):
    return pd.DataFrame({"time": [index * 14_400_000 for index in range(count)], "open": range(100,100+count), "high": range(101,101+count), "low": range(99,99+count), "close": range(100,100+count), "volume": [1]*count})
def _event(timestamp, direction="LONG", available=True): return {"symbol":"BTC/USDT","timeframe":"4h","direction":direction,"ready_timestamp":timestamp,"ready_candle_index":210,"horizons":{"5":{"available":available,"mfe_pct":3,"mae_pct":1,"close_return_pct":1}}}
def _payload(events, holdout=False): return {"runs":[{"status":"OK","symbol":"BTC/USDT","holdout_window":{"holdout_first_timestamp":10,"holdout_last_timestamp":14_400_000*999} if holdout else {},"events":events}]}

def test_features_are_past_only_and_exact():
    features = _frame_features(_frame()); timestamp = 220*14_400_000
    assert timestamp in features and features[timestamp]["return_60"] == (320/260-1)*100
    enriched, diag = _attach_market([{"period":"CURRENT","ready_timestamp":timestamp}], {("CURRENT","BTC/USDT"):features})
    assert len(enriched)==1 and diag["missing_exact_timestamp_events"]==0
    assert _attach_market([{"period":"CURRENT","ready_timestamp":timestamp+1}], {("CURRENT","BTC/USDT"):features})[1]["missing_exact_timestamp_events"]==1


def test_volatility_percentile_uses_only_prior_bars():
    frame = _frame(250)
    timestamp = 220 * 14_400_000
    baseline = _frame_features(frame)[timestamp]["atr_percentile_past_200"]
    frame.loc[221:, "high"] = 1_000_000
    assert _frame_features(frame)[timestamp]["atr_percentile_past_200"] == baseline


def test_atr_percentile_uses_exactly_200_prior_observations():
    features = _frame_features(_frame(213))
    assert 212 * 14_400_000 not in features
    feature = _frame_features(_frame(214))[213 * 14_400_000]
    assert feature["prior_atr_observations"] == 200

def test_breadth_and_regime_rules():
    btc={"close":101,"ema200":100}; bull={"breadth_above_ema50":.6,"median_return_20":.1}; bear={"breadth_above_ema50":.4,"median_return_20":-.1}
    assert classify_regime(btc,bull)=="BULL"
    assert classify_regime({"close":99,"ema200":100},bear)=="BEAR"
    assert classify_regime(btc,{"breadth_above_ema50":.5,"median_return_20":.1})=="MIXED"

def test_low_sample_and_hypothesis_logic():
    assert _metrics([])["low_sample"] is True
    rows=[]
    for period in ("CURRENT","HOLDOUT"):
        rows += [{"period":period,"market_regime":"BULL","direction":"LONG","n":15,"mfe_minus_mae_pct":1},{"period":period,"market_regime":"BULL","direction":"SHORT","n":15,"mfe_minus_mae_pct":0}]
    assert _hypothesis(rows,"BULL","LONG",True)=="SUPPORTED"
    rows[0]["mfe_minus_mae_pct"]=-1
    assert _hypothesis(rows,"BULL","LONG",True)=="NOT_SUPPORTED"
    rows[0]["mfe_minus_mae_pct"]=1
    assert _hypothesis(rows,"BULL","LONG",False)=="SUPPORTED"


def test_hypothesis_insufficient_sample():
    rows=[{"period":"CURRENT","market_regime":"BEAR","direction":"SHORT","n":14,"mfe_minus_mae_pct":1}]
    assert _hypothesis(rows,"BEAR","SHORT",False)=="INSUFFICIENT_SAMPLE"


def test_h1_does_not_require_short_sample():
    rows=[]
    for period in ("CURRENT", "HOLDOUT"):
        rows += [{"period":period,"market_regime":"BULL","direction":"LONG","n":15,"mfe_minus_mae_pct":1},{"period":period,"market_regime":"BULL","direction":"SHORT","n":14,"mfe_minus_mae_pct":-1}]
    assert _hypothesis(rows,"BULL","LONG",False)=="SUPPORTED"


def test_h2_does_not_require_long_sample_but_h3_h4_do():
    rows=[]
    for period in ("CURRENT", "HOLDOUT"):
        rows += [{"period":period,"market_regime":"BEAR","direction":"SHORT","n":15,"mfe_minus_mae_pct":1},{"period":period,"market_regime":"BEAR","direction":"LONG","n":14,"mfe_minus_mae_pct":-1}]
    assert _hypothesis(rows,"BEAR","SHORT",False)=="SUPPORTED"
    assert _hypothesis(rows,"BEAR","SHORT",True)=="INSUFFICIENT_SAMPLE"


def _single_direction_rows(regime, direction, edge=1.0, count=15):
    return [
        {"period": period, "market_regime": regime, "direction": direction,
         "n": count, "mfe_minus_mae_pct": edge}
        for period in ("CURRENT", "HOLDOUT")
    ]


def test_h1_supported_when_short_group_is_absent():
    rows = _single_direction_rows("BULL", "LONG")
    assert _hypothesis(rows, "BULL", "LONG", False) == "SUPPORTED"
    rows.append({"period": "CURRENT", "market_regime": "BULL",
                 "direction": "SHORT", "n": 15, "mfe_minus_mae_pct": 2.0})
    assert _hypothesis(rows, "BULL", "LONG", False) == "SUPPORTED"


def test_h2_supported_when_long_group_is_absent():
    rows = _single_direction_rows("BEAR", "SHORT")
    assert _hypothesis(rows, "BEAR", "SHORT", False) == "SUPPORTED"
    rows.append({"period": "HOLDOUT", "market_regime": "BEAR",
                 "direction": "LONG", "n": 15, "mfe_minus_mae_pct": 2.0})
    assert _hypothesis(rows, "BEAR", "SHORT", False) == "SUPPORTED"


def test_single_direction_hypotheses_still_reject_non_positive_edges():
    for regime, direction in (("BULL", "LONG"), ("BEAR", "SHORT")):
        for period_index in (0, 1):
            for edge in (0.0, -1.0):
                rows = _single_direction_rows(regime, direction)
                rows[period_index]["mfe_minus_mae_pct"] = edge
                assert _hypothesis(rows, regime, direction, False) == "NOT_SUPPORTED"


def test_single_direction_hypotheses_require_favored_sample_in_both_periods():
    for regime, direction in (("BULL", "LONG"), ("BEAR", "SHORT")):
        for period_index in (0, 1):
            rows = _single_direction_rows(regime, direction)
            rows[period_index]["n"] = 14
            assert _hypothesis(rows, regime, direction, False) == "INSUFFICIENT_SAMPLE"
        rows = _single_direction_rows(regime, direction)
        assert _hypothesis(rows[:1], regime, direction, False) == "INSUFFICIENT_SAMPLE"
        assert _hypothesis(rows[1:], regime, direction, False) == "INSUFFICIENT_SAMPLE"


def test_relative_hypotheses_require_opposite_group_in_both_periods():
    for regime, direction, other in (("BULL", "LONG", "SHORT"), ("BEAR", "SHORT", "LONG")):
        for period in ("CURRENT", "HOLDOUT"):
            rows = _single_direction_rows(regime, direction)
            assert _hypothesis(rows, regime, direction, True) == "INSUFFICIENT_SAMPLE"
            rows.append({"period": period, "market_regime": regime,
                         "direction": other, "n": 15, "mfe_minus_mae_pct": 0.0})
            assert _hypothesis(rows, regime, direction, True) == "INSUFFICIENT_SAMPLE"


def test_full_report_handles_a_single_direction_per_regime():
    timeframe_ms = 14_400_000
    for direction in ("LONG", "SHORT"):
        current_events, holdout_events = [], []
        for index in range(213, 228):
            for base, events in ((1000, current_events), (0, holdout_events)):
                event = _event((base + index) * timeframe_ms, direction)
                event["ready_candle_index"] = index
                events.append(event)
        current, holdout = _payload(current_events), _payload(holdout_events, True)
        holdout["runs"][0]["holdout_window"]["holdout_first_timestamp"] = 0
        original = copy.deepcopy((current, holdout))

        def loader(symbol, timeframe, before_timestamp, total_limit):
            assert symbol == "BTC/USDT" and timeframe == "4h"
            assert before_timestamp in (1000 * timeframe_ms, 2000 * timeframe_ms)
            frame = _frame(228)
            if direction == "SHORT":
                frame["close"] = 600 - frame["close"]
                frame["open"] = frame["close"]
                frame["high"] = frame["close"] + 1
                frame["low"] = frame["close"] - 1
            if before_timestamp == 2000 * timeframe_ms:
                frame["time"] += 1000 * timeframe_ms
            return frame

        report = analyze_market_regimes(current, holdout, loader)
        supported = "H1_long_bull_positive" if direction == "LONG" else "H2_short_bear_positive"
        assert report["coverage"]["analyzed_events"] == 30
        assert report["coverage"]["missing_exact_timestamp_events"] == 0
        assert report["hypotheses"][supported] == "SUPPORTED"
        assert all(value == "INSUFFICIENT_SAMPLE" for key, value in report["hypotheses"].items() if key != supported)
        assert (current, holdout) == original


def test_breadth_uses_all_exact_symbol_contexts():
    timestamp = 220 * 14_400_000
    features = _frame_features(_frame())
    adjusted = dict(features[timestamp], close=90, ema50=100, return_20=-20)
    enriched, _ = _attach_market([{"period":"CURRENT","ready_timestamp":timestamp}], {("CURRENT","BTC/USDT"):features, ("CURRENT","ALT"): {timestamp: adjusted}})
    assert enriched[0]["breadth_above_ema50"] == .5
    assert enriched[0]["median_return_20"] < 0


def test_mixed_when_bull_conditions_are_incomplete():
    assert classify_regime({"close":101,"ema200":100}, {"breadth_above_ema50":.6,"median_return_20":0}) == "MIXED"

def test_period_separation_and_input_immutable():
    current, holdout = _payload([_event(220*14_400_000)]), _payload([_event(220*14_400_000)], True)
    original=copy.deepcopy((current,holdout))
    def loader(*args, **kwargs): return _frame(1200)
    report=analyze_market_regimes(current,holdout,loader)
    assert report["coverage"]["analyzed_events"]==2 and (current,holdout)==original

if __name__ == "__main__":
    tests=[value for name,value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:test()
    print(f"{len(tests)} ready market regime tests passed.")
