"""Offline, long-only Bybit order-flow replay. Research ONLY: no orders.

Public websocket L50 snapshots/deltas are replayed in receive order. Execution
uses NEXT observed quote after 1s/2s delay. Taker fee, spread, and an additional
adverse slippage allowance apply on both legs. Signals are not a profit claim.
"""
import argparse
import collections
import datetime
import gzip
import json
import statistics
from pathlib import Path

SYMBOLS=("BTCUSDT","ETHUSDT","SOLUSDT")
FEE=0.0006
SLIPPAGE=0.0001
STALE_NS=2_000_000_000

class Book:
    def __init__(self):
        self.bid={}
        self.ask={}
        self.ready=False
        self.last_seq=-1
        self.last_ts=0
        self.resets=0
        self.bad=0
        self.updates=0
    def ingest(self,msg,ts):
        data=msg["data"]
        seq=int(data["seq"])
        if msg["type"]=="snapshot" or int(data.get("u",2))==1:
            self.bid.clear()
            self.ask.clear()
            self.ready=True
            self.resets+=1
        elif not self.ready or seq<=self.last_seq:
            self.ready=False
            self.bad+=1
            return
        for target,key in ((self.bid,"b"),(self.ask,"a")):
            for p,q in data[key]:
                price,qty=float(p),float(q)
                if qty==0:target.pop(price,None)
                else:target[price]=qty
        self.last_seq=seq
        self.last_ts=ts
        self.updates+=1
        if self.bid and self.ask and max(self.bid)>=min(self.ask):
            self.ready=False
            self.bad+=1
    def quote(self,now):
        if not self.ready or now-self.last_ts>STALE_NS or not self.bid or not self.ask:
            return None
        b=sorted(self.bid.items(),reverse=True)[:5]
        a=sorted(self.ask.items())[:5]
        best_bid,best_ask=b[0][0],a[0][0]
        denom=sum(x[1]*x[0] for x in b)+sum(x[1]*x[0] for x in a)
        imbalance=(sum(p*q for p,q in b)-sum(p*q for p,q in a))/denom if denom else 0
        return (best_bid,best_ask,imbalance)

def load(path):
    books={s:Book() for s in SYMBOLS}
    flows={s:collections.deque() for s in SYMBOLS}
    quote_at={s:{} for s in SYMBOLS}
    flow_at={s:{} for s in SYMBOLS}
    stats=collections.Counter()
    first=None
    last=None
    connection=None
    # Bucket per second: last causally observed quote for every elapsed second.
    # Processing this stream captures the quote at the CLOSE of each second.
    def flush_second(second):
        for s in SYMBOLS:
            q=books[s].quote(second*1_000_000_000+999_999_999)
            if q:
                quote_at[s][second]=q
                dq=flows[s]
                while dq and dq[0][0] < (second-4)*1_000_000_000:
                    dq.popleft()
                buy=sum(qty for t,qty,side in dq if side=="Buy")
                sell=sum(qty for t,qty,side in dq if side=="Sell")
                flow_at[s][second]=(buy-sell)/(buy+sell) if buy+sell else 0.0
    with gzip.open(path,"rt",encoding="utf-8") as source:
        for line in source:
            m=json.loads(line)
            event=m.get("event")
            if event=="connection_start":
                for book in books.values():book.ready=False
                connection=m.get("n")
                stats["connections"]+=1
                continue
            if event=="connection_end":
                for book in books.values():book.ready=False
                continue
            if event!="market":
                stats[str(event)]+=1
                continue
            ts=int(m["recv_utc_ns"])
            second=ts//1_000_000_000
            if first is None:
                first=second
                last=second
            while last<second:
                flush_second(last)
                last+=1
            payload=m["payload"]
            topic=m["topic"]
            sym=topic.rsplit(".",1)[1]
            stats[topic]+=1
            if topic.startswith("orderbook."):
                books[sym].ingest(payload,ts)
            else:
                for trade in payload["data"]:
                    flows[sym].append((ts,float(trade["v"]),trade["S"]))
    if last is not None:
        flush_second(last)
    return quote_at,flow_at,books,stats,first,last

def replay(quotes,flows,strategy,latency,hold):
    # Signals are calculated with only information available at the end
    # of second t. A fill is never earlier than t+latency.
    seconds=sorted(quotes)
    if len(seconds)<hold+latency+3:
        return {"trades":0,"net_pct_sum":0,"mean_net_pct":None,"win_rate":None}
    i=0
    trades=[]
    while i<len(seconds):
        t=seconds[i]
        bid,ask,imb=quotes[t]
        pressure=flows.get(t,0)
        spread=(ask-bid)/((ask+bid)/2)
        if strategy=="momentum":
            trigger=imb>=0.25 and pressure>=0.20
        else:
            trigger=imb<=-0.25 and pressure<=-0.20
        if not trigger or spread>0.0005:
            i+=1
            continue
        enter=t+latency
        exit_=enter+hold
        if enter not in quotes or exit_ not in quotes:
            i+=1
            continue
        eb,ea,_=quotes[enter]
        xb,xa,_=quotes[exit_]
        # Taker buy at ask, taker sell at bid, plus adverse 1 bp each side.
        executed_entry=ea*(1+SLIPPAGE)
        executed_exit=xb*(1-SLIPPAGE)
        net=executed_exit/executed_entry*(1-FEE)**2-1
        trades.append(net*100)
        # No overlapping positions, plus 5 sec cooldown
        while i<len(seconds) and seconds[i]<=exit_+5:i+=1
    return {"trades":len(trades),
            "net_pct_sum":round(sum(trades),4),
            "mean_net_pct":round(statistics.mean(trades),5) if trades else None,
            "win_rate_pct":round(100*sum(x>0 for x in trades)/len(trades),2) if trades else None,
            "best_pct":round(max(trades),4) if trades else None,
            "worst_pct":round(min(trades),4) if trades else None}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("data",type=Path)
    ap.add_argument("--json-out",type=Path,required=True)
    ap.add_argument("--md-out",type=Path,required=True)
    a=ap.parse_args()
    quotes,flows,books,stats,first,last=load(a.data)
    results={}
    for s in SYMBOLS:
        results[s]={}
        for strategy in ("momentum","reversal"):
            for latency in (1,2):
                for hold in (10,30,60):
                    k=f"{strategy}_latency{latency}s_hold{hold}s"
                    results[s][k]=replay(quotes[s],flows[s],strategy,latency,hold)
    out={"study":"15-minute Bybit public orderflow, strictly preliminary",
         "data_file":str(a.data),
         "start_utc":datetime.datetime.fromtimestamp(first,datetime.timezone.utc).isoformat() if first else None,
         "end_utc":datetime.datetime.fromtimestamp(last,datetime.timezone.utc).isoformat() if last else None,
         "seconds":last-first+1 if first else 0,
         "fee_per_side":FEE,"slippage_per_side":SLIPPAGE,"long_only":True,
         "market_messages":dict(stats),
         "books":{s:{"ready_quotes_seconds":len(quotes[s]),"snapshots":books[s].resets,
                     "updates":books[s].updates,"bad_or_regressing_seq":books[s].bad}
                  for s in SYMBOLS},
         "scenarios":results,
         "caveats":["15 minutes inadequate for profitability inference",
                    "A local orderbook sequence is not proof of no packet loss",
                    "L50 quoted sizes are not executable size; no queue simulation",
                    "Cannot establish true fill probability or exchange-side latency",
                    "No microsecond trade-vs-book synchronization guarantee",
                    "1s sampled book hides intrasecond movement",
                    "No funding, liquidation, leverage or live order execution"]}
    a.json_out.parent.mkdir(parents=True,exist_ok=True)
    a.md_out.parent.mkdir(parents=True,exist_ok=True)
    a.json_out.write_text(json.dumps(out,ensure_ascii=False,indent=2))
    lines=["# Bybit Orderflow: 15-minute diagnostic replay",
           "Research ONLY; not a verified profitable strategy.",
           f"UTC: {out['start_utc']} to {out['end_utc']}",
           f"Fees: {FEE*100:.2f}% per side, extra adverse slippage {SLIPPAGE*100:.2f}% per side, spread from live quotes.",
           "LONG-only; entry at next sampled ask 1s/2s after signal, exit at future bid after 10/30/60s.",
           "Signal momentum: top-five book imbalance >= 0.25 AND 5-second buy flow >= 0.2.",
           "Signal reversal: negative imbalance <= -0.25 AND sell flow <= -0.2.",
           "| Symbol | Valid quote seconds | Resets | Invalid/seq regressions |",
           "|---|---:|---:|---:|"]
    for s in SYMBOLS:
        b=out["books"][s]
        lines.append(f"| {s} | {b['ready_quotes_seconds']} | {b['snapshots']} | {b['bad_or_regressing_seq']} |")
    lines+=["","| Symbol | Case | Trades | Mean net % | Wins % | Sum of individual trade net returns % |",
            "|---|---|---:|---:|---:|---:|"]
    for s in SYMBOLS:
        for k,r in results[s].items():
            lines.append(f"| {s} | {k} | {r['trades']} | {r['mean_net_pct']} | {r['win_rate_pct']} | {r['net_pct_sum']} |")
    lines+=["","## Limitations"]+["- "+item for item in out["caveats"]]
    a.md_out.write_text("\n".join(lines)+"\n")
    print(json.dumps({"seconds":out["seconds"],"books":out["books"],
                      "trade_counts":{s:{k:v["trades"] for k,v in rs.items()} for s,rs in results.items()}}))
if __name__=="__main__":
    main()
