"""Orchestrator: raw bank export + PDF -> Xoro bank statement.

Ties the two tools together so a caller just names an account:

    convert_activity.convert_to_lines   -> sign-correct statement lines
    convert_activity.extract_balance    -> closing date + New Balance from the PDF
    xoro_webmethods.create_bank_statement -> commit to Xoro

Account keys come from ``upload_configs.json`` (e.g. ``amex_delta``); each entry's
``option`` is the Xoro account display name, which we match against
``get_bank_statement_accounts`` to resolve the FAccountId at runtime — no hardcoded
ids. The per-card sign convention lives entirely in ``bank_configs.json`` (via
``convert_to_lines``), so there is no ``credit_card`` boolean here.

Balance sign: a credit card's New Balance prints as a positive amount owed, but the
statement's ``EndBalance`` must match the (negative-charge) line convention, so it is
negated for credit-card accounts. Whether an account is a credit card is read from
Xoro (``AccountType``), not guessed.
"""

import convert_activity as ca
from xoro_webmethods import WebMethodClient


class AccountNotFound(Exception):
    """The upload_configs ``option`` name did not match any Xoro bank account."""


def resolve_account(client, option_name):
    """Return the Xoro bank-statement account whose ``Name`` equals ``option_name``."""
    accounts = client.get_bank_statement_accounts()
    for a in accounts:
        if a.get("Name") == option_name:
            return a
    raise AccountNotFound(
        "no Xoro bank account named %r (checked %d accounts)"
        % (option_name, len(accounts))
    )


def _header_from_pdf(pdf_path, bank_config_key, is_credit_card):
    """Return ``(end_date, end_balance)`` from the statement PDF, sign-corrected.

    ``end_balance`` is negated for credit cards so it matches the line convention;
    both are ``None`` when no PDF is given or the patterns do not match.
    """
    if not pdf_path:
        return None, None
    balance, closing_date = ca.extract_balance(pdf_path, bank_config_key)
    end_balance = None
    if balance is not None:
        end_balance = "%.2f" % (-balance if is_credit_card else balance)
    return closing_date, end_balance


def upload_statement(account_key, activity_path, pdf_path=None, *, client=None,
                     commit=True, start_reconcile=False):
    """Convert a raw export (+PDF) and upload it as a Xoro bank statement.

    ``account_key``    key in ``upload_configs.json`` (e.g. ``"amex_delta"``).
    ``activity_path``  raw bank export (csv / xls / xlsx).
    ``pdf_path``       statement PDF for closing date + balance (optional).
    ``commit``         when ``False``, resolve + build the payload but do NOT post
                       (a read-only dry-run preview).
    ``start_reconcile`` when ``True`` (and committing), also start the reconciliation
                       with the same ending balance + date (line matching stays
                       manual). Needs a PDF so the balance/date are known.

    Returns a summary dict. With ``commit=False`` it includes ``payload`` (the exact
    ``bankStmtData`` object); with ``commit=True`` it includes the Xoro ``envelope``,
    plus ``reconciliation`` when ``start_reconcile`` is set.
    """
    acct = ca.ACCOUNTS[account_key]
    bank_config_key = acct.get("bank_config", account_key)
    if bank_config_key not in ca.BANK_CONFIGS:
        raise KeyError("no bank_config %r for account %r" % (bank_config_key, account_key))

    lines, _dhl = ca.convert_to_lines(
        bank_config_key, activity_path, dhl_filter=acct.get("dhl_filter", False)
    )

    client = client or WebMethodClient.from_config()
    account = resolve_account(client, acct["option"])
    faccount_id = account["FAccountingId"]
    is_credit_card = account.get("AccountType") == "Credit Card"

    end_date, end_balance = _header_from_pdf(pdf_path, bank_config_key, is_credit_card)
    header = {"end_date": end_date, "end_balance": end_balance}

    summary = {
        "account": account["Name"],
        "faccount_id": faccount_id,
        "is_credit_card": is_credit_card,
        "line_count": len(lines),
        "end_date": end_date,
        "end_balance": end_balance,
    }

    if not commit:
        from xoro_webmethods import build_bank_statement
        summary["payload"] = build_bank_statement(faccount_id, lines, **header)
        return summary

    summary["envelope"] = client.create_bank_statement(faccount_id, lines, **header)

    if start_reconcile:
        if end_balance is None or end_date is None:
            raise ValueError("start_reconcile needs a PDF (ending balance + date)")
        summary["reconciliation"] = client.start_reconciliation(
            faccount_id, end_balance, end_date,
        )
    return summary
