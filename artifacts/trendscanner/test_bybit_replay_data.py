import copy
import json

import pandas as pd
import pytest

import bybit_replay_data as data
import lower_tf_historical_stop_replay as replay
import lower_tf_portfolio_replay as portfolio


def snapshot():
    def loader(symbol, timeframe, boundary, total_limit):
        return pd.DataFrame([[stamp, 100., 101., 99., 100., 1.]
                             for stamp in range(boundary - total_limit * 3600000, boundary, 3600000)],
                            columns=data.COLUMNS)
    return data.fetch_snapshot(['BTC/USDT', 'ETH/USDT'], '1h', 300,
                               before_timestamp=3600000 * 400, loader=loader)


def test_all_symbols_share_exact_closed_boundary_and_hashes_survive_roundtrip(tmp_path):
    bundle = snapshot()
    path = tmp_path / 'candles.json'
    path.write_text(json.dumps(bundle))
    restored = data.load_snapshot(path, '1h', 300)
    assert data.digest(restored) == data.digest(bundle)
    for symbol in ('BTC/USDT', 'ETH/USDT'):
        frame = data.snapshot_frame(restored, symbol)
        assert len(frame) == 300
        assert frame.iloc[-1]['time'] == bundle['manifest']['before_timestamp'] - 3600000
        assert frame.attrs['market_source'] == 'bybit_linear'


def test_corrupt_candles_are_rejected_before_strategy_evaluation():
    bundle = snapshot()
    bundle['candles']['BTC/USDT']['rows'][0][4] = 999.
    with pytest.raises(ValueError, match='HASH_MISMATCH'):
        data.validate_snapshot(bundle)


def test_missing_slot_is_rejected_even_if_hash_is_recomputed():
    bundle = snapshot()
    item = bundle['candles']['BTC/USDT']
    item['rows'].pop(1)
    item['sha256'] = data.digest(item['rows'])
    with pytest.raises(ValueError, match='NONEXACT'):
        data.validate_snapshot(bundle)


def test_wrong_venue_is_rejected():
    bundle = snapshot()
    bundle['manifest']['market_source'] = 'kucoin_futures'
    with pytest.raises(ValueError, match='WRONG_BYBIT_SNAPSHOT_SOURCE'):
        data.validate_snapshot(bundle)


def test_bybit_portfolio_cannot_mix_snapshots_or_venues(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio, 'MARKET_SOURCE', 'bybit_linear')
    first = {'manifest': {'replay_version': replay.REPLAY_VERSION, 'market_source': 'bybit_linear',
                          'candle_snapshot_sha256': 'snapshot-a'}, 'events': [], 'errors': []}
    second = copy.deepcopy(first)
    a, b = tmp_path / 'a.json', tmp_path / 'b.json'
    a.write_text(json.dumps(first))
    second['manifest']['candle_snapshot_sha256'] = 'snapshot-b'
    b.write_text(json.dumps(second))
    with pytest.raises(ValueError, match='MIXED_OR_MISSING'):
        portfolio.load_reports([a, b])
    second['manifest']['market_source'] = 'kucoin_futures'
    b.write_text(json.dumps(second))
    with pytest.raises(ValueError, match='WRONG_REPLAY_MARKET_SOURCE'):
        portfolio.load_reports([b])


def test_bybit_cli_requires_snapshot_instead_of_falling_back_to_kucoin(tmp_path, monkeypatch):
    monkeypatch.setattr(replay, 'MARKET_SOURCE', 'bybit_linear')
    monkeypatch.setattr('sys.argv', ['replay', '--timeframe', '1h', '--bars', '300',
                                    '--output', str(tmp_path / 'report.json')])
    with pytest.raises(SystemExit, match='BYBIT_OFFLINE_SNAPSHOT_REQUIRED'):
        replay.main()
