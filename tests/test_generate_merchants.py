import pytest

from generate_customers import load_config
from generate_merchants import generate_merchants


@pytest.fixture(scope="module")
def cfg():
    return load_config()


@pytest.fixture(scope="module")
def merchants(cfg):
    return generate_merchants(cfg)


def test_merchant_ids_are_unique(merchants):
    assert merchants["merchant_id"].is_unique


def test_mcc_and_risk_match_category(merchants, cfg):
    cats = cfg["merchants"]["categories"]
    assert (merchants["mcc"] == merchants["category"].map(lambda c: cats[c]["mcc"])).all()
    assert (merchants["risk_tier"] == merchants["category"].map(lambda c: cats[c]["risk"])).all()


def test_channel_is_allowed_for_category(merchants, cfg):
    cats = cfg["merchants"]["categories"]
    allowed = merchants.apply(
        lambda r: cats[r["category"]]["channel"] in ("BOTH", r["channel"]), axis=1
    )
    assert allowed.all()


def test_foreign_merchants_are_online_only(merchants, cfg):
    foreign = merchants[merchants["country"] != cfg["project"]["home_country"]]
    assert (foreign["channel"] == "ONLINE").all()


def test_only_physical_stores_have_location(merchants):
    online = merchants[merchants["channel"] == "ONLINE"]
    in_store = merchants[merchants["channel"] == "IN_STORE"]
    assert online["city"].isna().all()
    assert in_store["city"].notna().all()
