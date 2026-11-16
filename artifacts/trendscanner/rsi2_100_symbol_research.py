"""Read-only RSI2 universe expansion. Current Bybit top-100 is survivor-biased; not historical point-in-time selection."""
import json, time, traceback
from datetime import datetime, timezone
import connors_rsi2_research as base
import prospective_market_data as market
import rsi2_utilization_loss_diagnostic as study

UNIVERSE=100
POOL=300
ORIGINAL_12=tuple(base.SYMBOLS)
def candidates():
    # Public Bybit linear perpetuals, live instruments with USDT quote/settle.
    instruments=[]
    cursor=None
    for page in range(20):
        params={"category":"linear","limit":1000}
        if cursor:params["cursor"]=cursor
        result=market._get("/v5/market/instruments-info",params)
        instruments.extend(row["symbol"] for row in result.get("list",[])
            if row.get("status")=="Trading"
            and row.get("contractType")=="LinearPerpetual"
            and row.get("quoteCoin")=="USDT"
            and row.get("settleCoin")=="USDT"
            and row.get("symbol","").endswith("USDT"))
        next_cursor=result.get("nextPageCursor")
        if not next_cursor or next_cursor==cursor:break
        cursor=next_cursor
    tickers=market._get("/v5/market/tickers",{"category":"linear"}).get("list",[])
    turnover={}
    for row in tickers:
        symbol=row.get("symbol")
        try:turnover[symbol]=float(row.get("turnover24h") or 0)
        except (ValueError,TypeError):pass
    ranked=sorted(set(instruments),key=lambda symbol:(-turnover.get(symbol,0),symbol))
    return [s[:-4]+"/USDT" for s in ranked if s in turnover and s[:-4].isalnum() and s[:-4].isupper()][:POOL]

def load():
    boundary=int(time.time()*1000)//base.DAY*base.DAY
    order=list(dict.fromkeys((*ORIGINAL_12,*candidates())))
    frames={}
    excluded=[]
    for symbol in order:
        try:
            frames[symbol]=market.get_bybit_data_before(symbol,"4h",boundary,total_limit=base.BARS)
        except Exception as exc:
            excluded.append({"symbol":symbol,"reason":type(exc).__name__+":"+str(exc)[:120]})
        if len(frames)>=UNIVERSE+len(ORIGINAL_12):
            break
    return frames,excluded,boundary

def select_100(frames):
    # At least 100 including BTC; preserve candidate rank and require full history.
    rank=candidates()
    chosen=["BTC/USDT"]
    chosen.extend(s for s in rank if s in frames and s!="BTC/USDT")
    return tuple(chosen[:UNIVERSE])

def main():
    if market.MARKET_SOURCE!="bybit_linear":raise SystemExit("BYBIT_SOURCE_REQUIRED")
    frames,excluded,boundary=load()
    picked=select_100(frames)
    original_missing=[s for s in ORIGINAL_12 if s not in frames]
    if original_missing or len(picked)<UNIVERSE:
        print(json.dumps({"status":"INSUFFICIENT_HISTORY","requested":UNIVERSE,
            "eligible":len(picked),"original_missing":original_missing,
            "excluded":excluded[:90]}))
        raise SystemExit("INSUFFICIENT_100_COMPLETE_HISTORY")
    outcome={}
    initial=base.SYMBOLS
    try:
        for label,selected in (("reference_12",ORIGINAL_12),("top_100",picked)):
            base.SYMBOLS=selected
            result=study.evaluate({s:frames[s] for s in selected})
            # Reduce output: all losses retained, but omit scenarios unrelated to universe effects
            outcome[label]={k:result["results"][k] for k in
                ("baseline_5pct_10x","no_volume_gate","rsi10_with_gate")}
    finally:
        base.SYMBOLS=initial
    print(json.dumps({"status":"RESEARCH_ONLY","source":"Bybit public linear USDT perps",
        "selection":"current 24h turnover; survivorship bias (NOT point-in-time historic universe)",
        "end_boundary_utc":datetime.fromtimestamp(boundary/1000,timezone.utc).isoformat(),
        "requested_universe":UNIVERSE,"universe_size":len(picked),
        "symbols":picked,"excluded_count":len(excluded),"excluded":excluded[:90],
        "history":"9000 exact 4h bars per symbol; no missing-candle filling",
        "costs":"16bps/side plus hypothetical 1bp/day funding, not actual funding",
        "risk":"5pct initial margin, 10x, three positions, cross liquidation proxy not exchange replica",
        "results":outcome},sort_keys=True))
if __name__=="__main__":main()
