"""Pre-registered RSI2 LONG entry-gating screen, research only.

Original RSI2 LONG add20 fills/exits/additions unchanged. Only block new
entries at PREVIOUS completed UTC daily CLOSE according to one of:
  btc_trend: BTC daily close > its trailing 200-day SMA
  selloff_3d: target coin completed close / close 3 days ago >= .92
  volume_stress: NOT (down close day and daily traded volume >2x average
                 of previous 20 complete daily volumes)
  combined: all three gates.
Volumes are TOTAL traded volume, NOT aggressive selling volume.
These thresholds are hypotheses selected before running the test, not optimized.
Dates previously researched in earlier studies, no blind OOS.
No funding, mark-price, delisting or actual executions modeled.
"""
import copy,json,sys,time,statistics
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import connors_rsi2_research as base
import rsi2_one_add_down as long_model
from prospective_market_data import get_bybit_data_before

MODES=("unchanged","btc_trend","selloff_3d","volume_stress","combined")
FEES=(.0008,.0016)
CONTRACT={
  "reference":"RSI2 LONG with one capped 5%-dip addition, 20-day max hold, original model unchanged",
  "new_entries_only":"All filters alter only eligibility for NEW entries, never affect existing position exits, adds or sizes",
  "btc_trend":"BTC previous completed UTC daily close > trailing 200-day SMA",
  "selloff_3d":"Coin previous completed daily close is not down more than 8% versus 3 days earlier",
  "volume_stress":"On a negative coin day, completed volume not above 2x previous 20 completed days' average traded volume",
  "volume_note":"Total trading volume (not directional sell volume). Coin daily 4H bar volume sums; no price futures or funding",
  "combined":"All 3 gates; same research comparison, no parameter fitting",
  "execution":"signals after completed daily close, fill following day's OPEN; original sizing, one capped add, SMA5 exit",
  "validation":"60% early, 20-day embargo, last 40% later, both windows previously used for research; NOT clean holdout",
  "cost":"8 and 16 basis points each traded side; funding omitted; no orders or active runtime changes",
  "acceptance":"Only progress if improved annualized return and max DD in both windows at both fee settings; then fresh shadow OOS required"
}

def volume_series(frame):
    vol=frame["volume"].astype(float).tolist()
    if len(vol)!=base.BARS:raise ValueError("MISSING_VOLUME")
    return [sum(vol[i:i+6]) for i in range(0,len(vol),6)]

def allowed_flags(data,volumes,symbol,i):
    btc=data["BTC/USDT"]
    a=data[symbol]
    b=btc["sma200"][i]
    if i<22 or b!=b:raise ValueError("INCOMPLETE_CONTEXT")
    trend=btc["close"][i]>b
    move=a["close"][i]/a["close"][i-3]-1
    momentum=move>=-.08
    prevvol=statistics.mean(volumes[symbol][i-20:i])
    if prevvol<=0: raise ValueError("BAD_VOLUME_BASE")
    stress=(a["close"][i]<a["close"][i-1] and
            volumes[symbol][i]>2*prevvol)
    return {"btc_trend":trend,"selloff_3d":momentum,
            "volume_stress":not stress,"combined":trend and momentum and not stress}

def masked_data(data,volume,mode):
    if mode not in MODES:raise ValueError("BAD_MODE")
    if mode=="unchanged":return data,0
    result={}
    n=len(data["BTC/USDT"]["close"])
    masked=0
    for symbol,a in data.items():
        copydata=a.copy()
        rsi=a["rsi"].copy()
        for i in range(base.WARMUP,n):
            eligible=(a["close"][i]>a["sma200"][i] and a["rsi"][i]<5)
            if eligible and not allowed_flags(data,volume,symbol,i)[mode]:
                rsi[i]=50
                masked+=1
        copydata["rsi"]=rsi
        result[symbol]=copydata
    return result,masked

def evaluate(frames):
    data,times=base.daily_features(frames)
    volume={s:volume_series(frames[s]) for s in base.SYMBOLS}
    cut=int(len(times)*.6)
    windows={"early":(base.WARMUP+1,cut),"later":(cut+20,len(times))}
    transformed={}
    blocks={}
    for mode in MODES:
        transformed[mode],blocks[mode]=masked_data(data,volume,mode)
    results={}
    for fee in FEES:
        costs=str(int(fee*1e4))
        results[costs]={}
        for period,(start,end) in windows.items():
            metrics={}
            for mode in MODES:
                metrics[mode]=long_model.replay(transformed[mode],"add20",start,end,fee)
            original=long_model.replay(data,"add20",start,end,fee)
            assert original==metrics["unchanged"],"ORIGINAL_PARITY_FAIL"
            results[costs][period]=metrics
    return {"protocol":CONTRACT,
            "window":{"history_start":datetime.fromtimestamp(times[0]/1000,timezone.utc).isoformat(),
                      "early_end":datetime.fromtimestamp(times[cut-1]/1000,timezone.utc).isoformat(),
                      "later_end":datetime.fromtimestamp(times[-1]/1000,timezone.utc).isoformat()},
            "screened_eligible_symbol_days_full_range":blocks,
            "results":results,
            "original_parity":"EXACT",
            "disclaimer":"No clean blind OOS; masks only NEW entries not already-open positions. Volume is total trades, not sell pressure. Hypothesis test only."}

def main():
    import os
    if os.environ.get("TREND_SCANNER_MARKET_SOURCE")!="bybit_linear":
        raise SystemExit("BYBIT_SOURCE_REQUIRED")
    boundary=int(time.time()*1000)//base.DAY*base.DAY
    frames={}
    for s in base.SYMBOLS:
        for attempt in range(5):
            try:
                frames[s]=get_bybit_data_before(s,"4h",boundary,total_limit=base.BARS)
                break
            except ValueError as e:
                if str(e)!="BYBIT_PUBLIC_API_ERROR:10016" or attempt==4:raise
                time.sleep(5*(attempt+1))
    print(json.dumps(evaluate(frames),sort_keys=True))
if __name__=="__main__":main()
