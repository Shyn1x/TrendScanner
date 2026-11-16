"""Read-only RSI2 100-crypto-perp comparison with listing-aware unequal histories.

Current turnover universe is survivorship-biased: exploratory, not independent OOS.
Missing history is NaN, NEVER backfilled with price. Only historical closed bars.
"""
import json,time,math
from datetime import datetime,timezone
import pandas as pd
import connors_rsi2_research as base
import prospective_market_data as market
import rsi2_utilization_loss_diagnostic as study

UNIVERSE=100
POOL=400
MIN_BARS=1800
ORIGINAL_12=tuple(base.SYMBOLS)
# Exclude tokenized equity/index/commodity contracts, not cryptocurrency perps.
NONCRYPTO={
"SOXL","NVDA","XAU","XAG","SNDK","SPCX","SOXS","QQQ","MU","SKHYNIX",
"CL","SNXX","SKHY","MSTR","XAUT","NBIS","SPY","INTC","BZ","DRAM","GOOGL",
"TSLA","AAPL","CRCL","TQQQ","MSFT","AMDSTOCK","XLF","SAMSUNG","EWY",
"MRVL","AXP","HOOD","LMT","AXIS","UNITREE","QNT","CBRS","MXL","KORU",
"XPL","MUU","DGAI","QQQM","SPX","NDX","DOW","GOLD","SILVER","OIL",
}
NONCRYPTO_SUFFIX=("STOCK","ETF","INDEX")
def is_crypto_contract(symbol):
    if not symbol.endswith("USDT"):return False
    base_symbol=symbol[:-4]
    return bool(base_symbol and base_symbol.isalnum() and base_symbol.isupper()
                and base_symbol not in NONCRYPTO
                and not any(base_symbol.endswith(x) for x in NONCRYPTO_SUFFIX))

def candidates():
    instruments=[]
    cursor=None
    for _ in range(20):
        params={"category":"linear","limit":1000}
        if cursor:params["cursor"]=cursor
        result=market._get("/v5/market/instruments-info",params)
        instruments.extend(row["symbol"] for row in result.get("list",[])
            if row.get("status")=="Trading"
            and row.get("contractType")=="LinearPerpetual"
            and row.get("quoteCoin")=="USDT"
            and row.get("settleCoin")=="USDT"
            and is_crypto_contract(row.get("symbol","")))
        next_cursor=result.get("nextPageCursor")
        if not next_cursor or next_cursor==cursor:break
        cursor=next_cursor
    tickers=market._get("/v5/market/tickers",{"category":"linear"}).get("list",[])
    turnover={}
    for row in tickers:
        symbol=row.get("symbol")
        try:turnover[symbol]=float(row.get("turnover24h") or 0)
        except (ValueError,TypeError):pass
    ranked=sorted(set(instruments),key=lambda x:(-turnover.get(x,0),x))
    return [s[:-4]+"/USDT" for s in ranked if s in turnover][:POOL]

def fetch_partial(symbol,boundary):
    # First check 300d availability, then request longest successful past-only window.
    small=market.get_bybit_data_before(symbol,"4h",boundary,total_limit=MIN_BARS)
    for length in (base.BARS,6000,4200,3000):
        try:return market.get_bybit_data_before(symbol,"4h",boundary,total_limit=length)
        except ValueError as e:
            if str(e).startswith("INSUFFICIENT_BYBIT_HISTORY:"):continue
            raise
    return small

def pad(frame,boundary):
    target=pd.DataFrame({"time":list(range(boundary-base.BARS*base.TF,boundary,base.TF))})
    out=target.merge(frame,on="time",how="left",validate="one_to_one",sort=True)
    assert len(out)==base.BARS
    assert out["close"].notna().sum()>=MIN_BARS
    assert out["close"].dropna().index[-1]==base.BARS-1
    assert out["close"].dropna().index.to_series().diff().dropna().eq(1).all()
    return out

def load():
    boundary=int(time.time()*1000)//base.DAY*base.DAY
    rank=candidates()
    order=list(dict.fromkeys((*ORIGINAL_12,*rank)))
    frames={}
    excluded=[]
    successful_lengths={}
    for s in order:
        if s not in ORIGINAL_12 and len(frames)>=UNIVERSE:break
        try:
            raw=fetch_partial(s,boundary)
            frames[s]=pad(raw,boundary)
            successful_lengths[s]=len(raw)
        except Exception as exc:
            excluded.append({"symbol":s,"reason":type(exc).__name__+":"+str(exc)[:120]})
    return frames,rank,excluded,successful_lengths,boundary

def main():
    if market.MARKET_SOURCE!="bybit_linear":raise SystemExit("BYBIT_SOURCE_REQUIRED")
    frames,rank,excluded,lengths,boundary=load()
    missing=[s for s in ORIGINAL_12 if s not in frames]
    picked=tuple(list(ORIGINAL_12)+[s for s in rank if s in frames and s not in ORIGINAL_12])[:UNIVERSE]
    if missing or len(picked)<UNIVERSE:
        print(json.dumps({"status":"INSUFFICIENT_100_ELIGIBLE","requested":UNIVERSE,
            "eligible":len(picked),"original_missing":missing,"excluded":excluded[:100]},sort_keys=True))
        raise SystemExit("INSUFFICIENT_100_ELIGIBLE")
    outcome={}
    old_symbols=base.SYMBOLS
    try:
        for label,selected in (("reference_12",ORIGINAL_12),("expanded_100",picked)):
            base.SYMBOLS=selected
            result=study.evaluate({s:frames[s] for s in selected})
            outcome[label]={k:result["results"][k] for k in
                ("baseline_5pct_10x","no_volume_gate","rsi10_with_gate")}
    finally:base.SYMBOLS=old_symbols
    print(json.dumps({"status":"RESEARCH_ONLY","source":"Bybit public linear USDT perpetual contracts",
        "selection":"Current turnover-ranked, with 12 legacy symbols held fixed; survivor bias; NOT point-in-time historical selection",
        "history":"Common 9000x4h UTC timestamps, missing prelisting prices as NaN, min 1800 contiguous 4h bars, 200d SMA warmup",
        "requested_universe":100,"universe_size":len(picked),"symbols":picked,
        "available_4h_bars":{k:lengths[k] for k in picked},
        "excluded_count":len(excluded),"excluded":excluded[:100],
        "end_boundary_utc":datetime.fromtimestamp(boundary/1000,timezone.utc).isoformat(),
        "costs":"16bps/side + hypothetical 1bp/day funding, not real historical funding",
        "risk":"5pct initial margin, 10x, three positions; synthetic low-based liquidation proxy",
        "results":outcome},sort_keys=True,allow_nan=False))
if __name__=="__main__":main()
