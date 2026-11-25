"""Offline regression tests: no Bybit access and no exchange orders."""
import gzip
import json
import tempfile
import unittest
from pathlib import Path
from bybit_orderflow_replay import Book, load, replay, SYMBOLS

class BookTests(unittest.TestCase):
    def test_snapshot_delta_zero_removal_and_regression(self):
        b=Book()
        def packet(kind,seq,bids,asks):
            return {"type":kind,"data":{"seq":seq,"u":seq,"b":bids,"a":asks}}
        b.ingest(packet("snapshot",100,[["99","2"]],[["101","2"]]),1_000_000_000)
        self.assertTrue(b.ready)
        self.assertEqual(b.quote(1_500_000_000),(99.,101.,0.))
        b.ingest(packet("delta",101,[["100","3"],["99","0"]],[]),1_100_000_000)
        self.assertEqual(b.quote(1_500_000_000)[:2],(100.,101.))
        b.ingest(packet("delta",99,[],[]),1_200_000_000)
        self.assertIsNone(b.quote(1_500_000_000))
        b.ingest(packet("snapshot",1,[["98","1"]],[["102","1"]]),1_300_000_000)
        self.assertTrue(b.ready)
    def test_latency_and_costs_are_adverse(self):
        # synthetic stable market cannot make profit after costs
        q={i:(99.99,100.01,0.8) for i in range(180)}
        f={i:0.8 for i in range(180)}
        for latency in (1,2):
            r=replay(q,f,"momentum",latency,10)
            self.assertGreater(r["trades"],0)
            self.assertLess(r["mean_net_pct"],0)
    def test_loader_second_buckets_and_connection_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"test.jsonl.gz"
            def event(topic,sec,kind):
                sym=topic.rsplit(".",1)[1]
                if topic.startswith("orderbook"):
                    payload={"topic":topic,"type":kind,"data":{"s":sym,"seq":sec,"u":sec,"b":[["99","5"]],"a":[["101","5"]]}}
                else:
                    payload={"topic":topic,"data":[{"s":sym,"v":"2","S":"Buy"}]}
                return {"event":"market","recv_utc_ns":sec*1_000_000_000,"topic":topic,"payload":payload}
            rows=[{"event":"connection_start","n":1}]
            for sec in range(10,15):
                for sym in SYMBOLS:
                    rows.append(event("orderbook.50."+sym,sec,"snapshot" if sec==10 else "delta"))
                    rows.append(event("publicTrade."+sym,sec,"trade"))
            rows.append({"event":"connection_end","n":1})
            with gzip.open(path,"wt") as f:
                for row in rows:f.write(json.dumps(row)+"\n")
            q,f,b,stats,first,last=load(path)
            self.assertEqual((first,last),(10,14))
            for sym in SYMBOLS:
                self.assertEqual(len(q[sym]),5)
                self.assertGreater(f[sym][12],0)
                self.assertEqual(b[sym].bad,0)

if __name__=="__main__":
    unittest.main()
