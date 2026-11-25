"""Bybit public linear orderbook/trades research capture; no orders or secrets.

Writes raw snapshots/deltas with local receive timestamps. After reconnect, book
is considered invalid until each symbol's next complete snapshot.
"""
import argparse
import asyncio
import gzip
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import websockets

URL = "wss://stream.bybit.com/v5/public/linear"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
TOPICS = [f"orderbook.50.{s}" for s in SYMBOLS] + [f"publicTrade.{s}" for s in SYMBOLS]


def validate(message):
    if not isinstance(message, dict) or message.get("topic") not in TOPICS:
        return False
    topic, data = message["topic"], message.get("data")
    if topic.startswith("orderbook."):
        return (message.get("type") in ("snapshot", "delta") and
                isinstance(data, dict) and data.get("s") == topic.rsplit(".", 1)[1] and
                all(key in data for key in ("b", "a", "u", "seq")))
    return (isinstance(data, list) and
            all(isinstance(d, dict) and d.get("s") == topic.rsplit(".", 1)[1] for d in data))


async def record(path, seconds):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    counts = {t: 0 for t in TOPICS}
    connections = 0
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=3) as output:
        def emit(event):
            output.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")

        emit({"event": "start", "utc": datetime.now(timezone.utc).isoformat(),
              "url": URL, "topics": TOPICS,
              "note": "raw book delta requires preceding snapshot; reconnect invalidates book"})
        while time.monotonic() - started < seconds:
            connections += 1
            valid_books = set()
            last_seq = {}
            emit({"event": "connection_start", "n": connections,
                  "recv_utc_ns": time.time_ns(), "reset_book": True})
            try:
                async with websockets.connect(URL, ping_interval=20, ping_timeout=15,
                                              max_size=8_000_000, open_timeout=20) as ws:
                    await ws.send(json.dumps({"op": "subscribe", "args": TOPICS}))
                    while time.monotonic() - started < seconds:
                        remaining = seconds - (time.monotonic() - started)
                        if remaining <= 0:
                            break
                        try:
                            payload = await asyncio.wait_for(ws.recv(), timeout=min(35, remaining))
                        except asyncio.TimeoutError:
                            if remaining < 35:
                                break
                            raise TimeoutError("no messages for 35 seconds")
                        receive_utc_ns, receive_mono_ns = time.time_ns(), time.monotonic_ns()
                        data = json.loads(payload)
                        if not validate(data):
                            emit({"event": "control_or_invalid", "n": connections,
                                  "recv_utc_ns": receive_utc_ns, "payload": data})
                            continue
                        topic = data["topic"]
                        if topic.startswith("orderbook."):
                            seq = data["data"]["seq"]
                            if data["type"] == "snapshot":
                                valid_books.add(topic)
                            elif topic not in valid_books:
                                emit({"event": "delta_without_snapshot", "topic": topic,
                                      "n": connections, "recv_utc_ns": receive_utc_ns})
                                continue
                            elif isinstance(seq, int) and seq < last_seq.get(topic, -1):
                                valid_books.discard(topic)
                                emit({"event": "sequence_regression", "topic": topic,
                                      "n": connections, "recv_utc_ns": receive_utc_ns})
                                continue
                            last_seq[topic] = seq
                        counts[topic] += 1
                        emit({"event": "market", "n": connections, "topic": topic,
                              "recv_utc_ns": receive_utc_ns, "recv_monotonic_ns": receive_mono_ns,
                              "exchange_ts_ms": data.get("ts"), "payload": data})
            except (OSError, TimeoutError, websockets.exceptions.WebSocketException) as error:
                emit({"event": "connection_error", "n": connections,
                      "recv_utc_ns": time.time_ns(), "type": type(error).__name__,
                      "message": str(error)[:200]})
                await asyncio.sleep(min(10, 2 * connections))
            finally:
                emit({"event": "connection_end", "n": connections,
                      "recv_utc_ns": time.time_ns(), "reset_book": True})
        emit({"event": "end", "recv_utc_ns": time.time_ns(),
              "connections": connections, "topic_counts": counts})
    print(json.dumps({"output": str(path), "connections": connections, "topic_counts": counts},
                     sort_keys=True))
    if any(counts[t] < 1 for t in TOPICS):
        raise SystemExit("MISSING_ORDERFLOW_TOPICS")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--seconds", type=int, default=900)
    p.add_argument("--output", default="orderflow-data/bybit_raw_orderflow.jsonl.gz")
    a = p.parse_args()
    if not 30 <= a.seconds <= 3600:
        p.error("--seconds must be within 30..3600")
    asyncio.run(record(a.output, a.seconds))
