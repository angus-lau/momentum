from card_coding import merchant_key, _round_lines


def test_merchant_key_strips_processor_and_ids():
    assert merchant_key("FACEBK *T7QQYB23J4 MENLO PARK, CA") == "FACEBK"
    assert merchant_key("HCM*PHO VIET LOTUS RES SURREY, BC") == "PHO VIET"
    assert merchant_key("Dropbox TRJ83HRPJQZX San Francisco") == merchant_key("Dropbox TDFK8DQF7GP5 San Francisco,")
    assert merchant_key("IMPARK00011928U VANCOUVER BC") == merchant_key("IMPARK00011888U VANCOUVER, BC")


def test_round_lines_sums_to_charge():
    parts = [{"gl": "2226", "amt": 3.1117}, {"gl": "2900", "amt": 65.345}, {"gl": "7620", "amt": 62.2333}]
    assert round(sum(p["amt"] for p in _round_lines(parts, 130.69)), 2) == 130.69
