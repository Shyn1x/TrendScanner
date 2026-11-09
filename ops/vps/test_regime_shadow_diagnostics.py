from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

p=Path(__file__).with_name("regime_shadow_diagnostics.py")
spec=spec_from_file_location("shadow_diagnostics",p)
m=module_from_spec(spec)
spec.loader.exec_module(m)


def case(side, stamp, h, net, stopped=False):
    return {"direction":side,"target_ms":stamp,"horizon":h,
            "status":"SCORED","net_pct":net,"gross_pct":net+0.16,"stop_hit":stopped}


def test_correlated_signals_count_as_one_time_cohort():
    report=m.summarize([case("SHORT",1000,1,-3,True),
                        case("SHORT",1000,1,-1),case("SHORT",2000,1,2)])
    row=report["by_direction_and_horizon"][0]
    assert row["signal_count"]==3
    assert row["independent_time_cohorts"]==2
    assert row["stopped_rate_pct"]==33.33
    assert row["average_net_pct_ex_funding"]==-0.6667
    assert row["average_of_cohort_means_pct"]==0


def test_missing_score_not_counted_as_profit():
    pending={"direction":"LONG","target_ms":1000,"horizon":3,
             "status":"INVALID_STOP_AT_NEXT_OPEN","gross_pct":None,
             "net_pct":None,"stop_hit":False}
    result=m.summarize([pending])
    assert result["by_direction_and_horizon"]==[]
    assert result["non_scored"][0]["count"]==1


def test_no_database_mutation_or_order_paths():
    source=p.read_text()
    for forbidden in ("INSERT INTO", "DELETE FROM", "UPDATE regime_", "order/create",
                      "CREATE TABLE", "DROP TABLE"):
        assert forbidden not in source
