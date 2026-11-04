"""
Tests for the rules baseline: the Python reference must calculate correctly, and the SAS program must
contain exactly the same rules (names, order and descriptions), so the parallel run compares like with like.
"""
import re

import numpy as np
import pandas as pd

from rules_reference import RULES, apply_rules, compare, evaluation_population, measure

SAS_PROGRAM = "sas/04_rules_baseline.sas"


def sas_text() -> str:
    text = open(SAS_PROGRAM, encoding="utf-8").read()
    return re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)


def sas_measure_calls() -> list[tuple[str, str]]:
    """Every %measure(name, description) call in the SAS program, in order (not the macro definition)."""
    calls = re.findall(r"%measure\((\w+),\s*([^)]*)\)", sas_text())
    return [(name, " ".join(desc.split())) for name, desc in calls if name != "rule"]


def row(**values):
    base = {"txn_id": "T", "txn_type": "TRANSFER_OUT", "status": "SUCCESS", "in_scope": 1, "days_to_data_end": 40,
            "date_key": 20260601, "amount": 100.0, "fraud_label": 0, "device_known": 1, "new_payee": 0,
            "failed_logins_prev_1h": 0, "foreign_logins_prev_1h": 0, "is_night": 0, "drain_ratio": 0.1,
            "amount_to_avg": 1.0, "merchant_risk_tier": "NONE", "new_merchant": np.nan, "txns_prev_1h": 0,
            "merchant_is_foreign": 0}
    base.update(values)
    return base


# ---------------------------------------------------------------- SAS and Python contain the same rules
def test_sas_measures_the_same_rules_in_the_same_order():
    assert [name for name, _ in sas_measure_calls()] == list(RULES) + ["any_rule"]


def test_sas_and_python_describe_each_rule_the_same_way():
    sas = dict(sas_measure_calls())
    for name, (description, _) in RULES.items():
        assert sas[name] == description, f"{name}: descriptions differ"


def test_sas_defines_every_rule():
    for name in list(RULES) + ["any_rule"]:
        assert re.search(rf"^\s*{name}\s*=", sas_text(), flags=re.MULTILINE), f"SAS never defines {name}"


def test_sas_uses_the_same_maturity_window():
    from rules_reference import MATURITY_DAYS
    assert re.search(rf"%let\s+maturity_days\s*=\s*{MATURITY_DAYS}\s*;", sas_text())


# ---------------------------------------------------------------- the Python reference itself
def test_population_excludes_recent_declined_and_out_of_scope():
    table = pd.DataFrame([row(txn_id="keep"), row(txn_id="recent", days_to_data_end=29),
                          row(txn_id="declined", status="DECLINED"), row(txn_id="atm", in_scope=0)])
    assert list(evaluation_population(table)["txn_id"]) == ["keep"]


def test_a_rule_never_fires_on_missing_information():
    population = pd.DataFrame([row(device_known=np.nan, new_payee=1), row(drain_ratio=np.nan, is_night=1)])
    assert not apply_rules(population).any().any()


def test_each_rule_fires_on_its_pattern():
    population = pd.DataFrame([
        row(device_known=0, new_payee=1),
        row(failed_logins_prev_1h=2, foreign_logins_prev_1h=1),
        row(is_night=1, drain_ratio=0.3),
        row(new_payee=1, amount_to_avg=5),
        row(txn_type="CARD_PURCHASE", merchant_risk_tier="HIGH", new_merchant=1, txns_prev_1h=2),
        row(txn_type="CARD_PURCHASE", merchant_risk_tier="HIGH", merchant_is_foreign=1, amount_to_avg=3),
    ])
    hits = apply_rules(population)
    for i, name in enumerate(RULES):
        assert hits[name].iloc[i], f"{name} did not fire on its own pattern"
    assert hits["any_rule"].all()


def test_metrics_are_calculated_correctly():
    population = pd.DataFrame([row(fraud_label=1, amount=300, date_key=1), row(fraud_label=1, amount=100, date_key=2),
                               row(amount=50, date_key=2), row(amount=10, date_key=2)])
    hits = pd.DataFrame({"r": [True, False, True, False]}, index=population.index)
    result = measure(population, hits).set_index("rule").loc["r"]
    assert result["alerts"] == 2 and result["fraud_caught"] == 1
    assert result["alerts_per_day"] == 1.0                 # 2 alerts over 2 days
    assert result["precision"] == 0.5 and result["recall"] == 0.5
    assert result["value_detection"] == 0.75               # $300 of $400 fraud caught


def test_compare_flags_any_difference():
    python = pd.DataFrame({"rule": ["a", "b"], "alerts": [5, 5], "fraud_caught": [2, 2],
                           "fraud_amount_caught": [10.0, 10.0]})
    sas = pd.DataFrame({"RULE": ["a", "b"], "ALERTS": [5, 5], "FRAUD_CAUGHT": [2, 1],
                        "FRAUD_AMOUNT_CAUGHT": [10.0, 10.0]})
    result = compare(python, sas).set_index("rule")["result"]
    assert result["a"] == "MATCH" and result["b"] == "DIFFERENT"
