from card_rec import match, signed


def L(i, d, amt, typ, desc=""):
    return {"Id": i, "PostDate": d, "Amount": amt, "TypeName": typ, "Description": desc, "BankStatementId": 1}


def T(i, d, amt, memo=""):
    return {"ID": i, "TXN_DATE": d, "AMOUNT": amt, "MEMO": memo}


def test_signs_and_duplicates():
    lines = [L(1, "10/24/2026", 763.64, "debit", "FEDEX A"), L(2, "11/08/2026", 65.96, "credit", "AMZN"),
             L(3, "10/20/2026", 200.0, "debit", "STAMPS"), L(4, "10/20/2026", 200.0, "debit", "STAMPS")]
    txns = [T(10, "10/24/2026", -763.64, "FEDEX A"), T(11, "11/08/2026", 65.96), T(12, "10/20/2026", -200.0, "STAMPS"),
            T(13, "10/21/2026", -200.0, "STAMPS"), T(14, "10/20/2026", -200.0, "OTHER")]
    pairs, left, free = match(lines, txns)
    assert [(l["Id"], t[0]["ID"]) for l, t in pairs] == [(3, 12), (4, 13), (1, 10), (2, 11)]
    assert not left and [t["ID"] for t in free] == [14]
    assert signed(lines[0]) == -763.64 and signed(lines[1]) == 65.96


def test_out_of_window_is_unmatched():
    pairs, left, _ = match([L(1, "10/01/2026", 10.0, "debit")], [T(9, "10/20/2026", -10.0)])
    assert not pairs and len(left) == 1


def test_review_holds_large_and_disagreeing():
    from card_rec import review
    a = (L(1, "10/24/2026", 50.0, "debit"), T(10, "10/24/2026", -50.0))
    a[0]["MatchedTxnId"] = "X"; a[1]["TXN_ID"] = "X"
    big = (L(2, "10/24/2026", 1500.0, "debit"), T(11, "10/24/2026", -1500.0))
    other = (dict(L(3, "10/24/2026", 20.0, "debit"), MatchedTxnId="Y", MatchedRefNumber="JE1"), dict(T(12, "10/24/2026", -20.0), TXN_ID="Z"))
    auto, held = review([(a[0],[a[1]]), (big[0],[big[1]]), (other[0],[other[1]])])
    assert [x[0]["Id"] for x in auto] == [1] and [x[0]["Id"] for x in held] == [2, 3]


def test_prefers_transaction_dated_before_bank_date():
    lines = [L(1, "10/26/2026", 4000.0, "credit"), L(2, "10/29/2026", 4000.0, "credit")]
    txns = [T(53, "10/23/2026", 4000.0), T(55, "10/28/2026", 4000.0)]
    pairs, left, _ = match(lines, txns)
    assert [(l["Id"], t[0]["ID"]) for l, t in pairs] == [(1, 53), (2, 55)] and not left


def test_two_entry_wire_and_old_prepayment():
    lines = [L(1, "10/21/2026", 29975.0, "credit", "INCOMING WIRE"), L(2, "10/28/2026", 1347.5, "debit", "OUTGOING WIRE HK, RONDA LTD.")]
    txns = [T(1, "10/21/2026", 30000.0, "transfer"), T(2, "10/21/2026", -25.0, "Wire fee"),
            dict(T(3, "08/24/2026", -1347.5), TXN_TYPE_NAME="Vendor Prepayment", ENTITY_NAME="Ronda Ltd. (USD)")]
    pairs, left, free = match(lines, txns)
    got = {l["Id"]: sorted(t["ID"] for t in ts) for l, ts in pairs}
    assert got == {1: [1, 2], 2: [3]} and not left and not free
