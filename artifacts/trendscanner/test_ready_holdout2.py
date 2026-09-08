from __future__ import annotations
import copy
import pandas as pd
from pathlib import Path
from tempfile import TemporaryDirectory
from ready_market_regime_analysis import _frame_features
from ready_outcome_holdout2 import HOLDOUT_LIMIT, prepare_holdout2_window, preflight_holdout2, run_ready_outcome_holdout2
from ready_regime_validation_holdout2 import PREDICTIONS, prediction_verdict, validate_holdout2

TF=14_400_000
def _frame(before): return pd.DataFrame({"time":list(range(before-HOLDOUT_LIMIT, before)),"open":[1]*HOLDOUT_LIMIT,"high":[2]*HOLDOUT_LIMIT,"low":[.5]*HOLDOUT_LIMIT,"close":[1]*HOLDOUT_LIMIT,"volume":[1]*HOLDOUT_LIMIT})
def _event(ts, direction="SHORT", mfe=3, mae=1): return {"symbol":"BTC/USDT","timeframe":"4h","direction":direction,"ready_timestamp":ts,"horizons":{"5":{"available":True,"mfe_pct":mfe,"mae_pct":mae,"close_return_pct":1}}}
def _h1(): return {"runs":[{"symbol":"BTC/USDT","status":"OK","holdout_window":{"holdout_first_timestamp":1000*TF,"holdout_last_timestamp":1999*TF},"events":[]}]}
def _current(): return {"runs":[{"symbol":"BTC/USDT","timeframe":"4h","status":"OK","events":[{**_event(2001*TF),"ready_candle_index":1}]}]}
def test_holdout2_strict_and_zero_overlap():
    _,d=prepare_holdout2_window(_frame(2000),2000,(2000,2999),(3000,3999)); assert d["eligible"] and d["holdout2_last_timestamp"]<2000 and d["overlap_holdout1_count"]==d["overlap_current_count"]==0
def test_preflight_exact_rows_and_immutable():
    h1,current=_h1(),_current(); original=copy.deepcopy((h1,current)); rows=preflight_holdout2(h1,current,["BTC/USDT"],lambda *args,**kwargs:_frame(1000*TF)); assert rows[0]["status"]=="READY" and rows[0]["holdout2_rows"]==1000 and (h1,current)==original
def test_predictions_and_shared_context_exact_timestamp():
    payload={"runs":[{"symbol":"BTC/USDT","status":"OK","holdout2_window":{"holdout2_first_timestamp":1000,"holdout2_last_timestamp":2200*TF},"events":[_event(220*TF) for _ in range(15)]}]}
    def loader(*args,**kwargs):
        return pd.DataFrame({"time":[i*TF for i in range(1213)],"open":[100+i for i in range(1213)],"high":[101+i for i in range(1213)],"low":[99+i for i in range(1213)],"close":[100+i for i in range(1213)],"volume":[1]*1213})
    report=validate_holdout2(payload,loader); assert report["coverage"]["analyzed_events"]==15 and set(report["predictions"])==set(PREDICTIONS)
def test_p4_negative_and_insufficient():
    assert PREDICTIONS["P4_mixed_normal_short"][3] == -1
    assert prediction_verdict({"n":15,"mfe_minus_mae_pct":1}, 1) == "SUPPORTED"
    assert prediction_verdict({"n":15,"mfe_minus_mae_pct":-1}, -1) == "SUPPORTED"
    assert prediction_verdict({"n":14,"mfe_minus_mae_pct":-1}, -1) == "INSUFFICIENT_SAMPLE"


def test_shared_feature_has_exactly_200_past_atr_observations():
    frame=pd.DataFrame({"time":[i*TF for i in range(214)],"open":[100+i for i in range(214)],"high":[101+i for i in range(214)],"low":[99+i for i in range(214)],"close":[100+i for i in range(214)],"volume":[1]*214})
    assert _frame_features(frame)[213*TF]["prior_atr_observations"] == 200


def test_runner_writes_only_holdout2_outputs():
    with TemporaryDirectory() as tmp:
        old=Path(tmp)/"ready_outcome_holdout_results.json"; old.write_text("old",encoding="utf-8")
        output_json,output_csv=Path(tmp)/"holdout2.json",Path(tmp)/"holdout2.csv"
        payload=run_ready_outcome_holdout2(holdout1_payload=_h1(),current_payload=_current(),symbols=["BTC/USDT"],output_json=str(output_json),output_csv=str(output_csv),loader=lambda *args,**kwargs:_frame(1000*TF),replay_fn=lambda *args,**kwargs:{"meta":{},"replay_results":[]},analyzer=lambda *args,**kwargs:{"summary":{"ready_events":0,"skipped_events":0},"events":[]},progress=False)
        assert payload["runs"][0]["status"]=="OK" and output_json.exists() and output_csv.exists() and old.read_text(encoding="utf-8")=="old"
if __name__=="__main__":
    tests=[value for name,value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:test()
    print(f"{len(tests)} holdout2 tests passed.")