# Momentum — month-end close for St. Moritz Watch

Scripts that run the monthly close in **Xoro** (St. Moritz Watch Corp, CAD home currency, FY ends **July 31**)
and **QuickBooks Online** (SMWCO = St. Moritz Watch Company Inc., USD). The checklist is the Todoist project
**Job Overview**; work only from it unless asked otherwise.

Read before working: `AUTOMATIONS.md` (what is scripted and how), `XORO_API.md` (endpoints, payload quirks).

## Ground rules
- Confirm before any write to Xoro, QuickBooks, a bank or email: show the exact entries first. Reads are free.
- Commits: no `Co-Authored-By: Claude` trailer. Work on a `<mon>-<yyyy>-close` branch and push.
- New Xoro writes: capture the real page's payload first (CDP on the debug Chrome, `execAjx` stubbed), then send.
  `xoro_cdp/` has the helpers; reload the page before each run. Xoro allows one session — if a tab shows
  `LoggedOut.aspx`/`login.aspx`, ask the user to log in on the debug Chrome (`--remote-debugging-port=9222`).
- FY2026 (to 2026-07-31) is under audit and **locked**. Changes there need the user to unlock and re-lock,
  and anything that moves a 7/31 balance must be reported to the auditors (D&H, Pan Zhang).
- Rec PDFs the user sends: `python3 file_rec.py <pdf>` files it and ticks Todoist only at $0.00 uncleared.
  Old carry-overs (FY2025 AJE JE015996 on BMO USD / Umpqua; 2022–2025 pennies) are normal — explain, then tick on request.

## Monthly order (what worked in Sep 2026)
1. **Cards** (7): give the user each card's expected statement date; convert + upload via API (no
   BankStatementImport.csv needed); open recs. DHL bill from 4002 → `dhl_bill.py --create`, paid by Amex 2106.
2. **Banks**: BMO CAD/USD, Umpqua 1140, Wise CAD/EUR/GBP, PayPal 1143, SC Cash 1121 — statements up, recs open.
3. **Payouts**: Shopify SC + Consolidated (`shopify_deposits.py`, then `--retry` after fixing missing orders),
   Stripe, Afterpay, PayPal (`paypal_deposits.py`), Amazon (workbook, `amazon_sheet_exports.py <from> <to>`).
4. **Bills**: Ceridian (`ceridian_bills.py`), Formula (`formula_bills.py`), others via `bills.py`.
5. **Recs**: user ticks in Xoro; help find matches with `xoro_ye_recon.fetch()` / `outstanding()`.
6. **SMWCO** in QuickBooks (`qbo_api.py`): reconcile 1142 Checking (1448) → P&L Net Operating Income →
   fulfillment worksheet (`Accounting Docs/Fulfillment Fees Worksheet - bill to Corp from Company.xlsx`,
   tab `FY <fy>`, col B) → QBO JE Dr 2810 / Cr 6015 (memo "Intercompany Fulfillment Income - <Month Year>")
   → Xoro JE Dr 7190 / Cr 1260 → 2810 and 1260 balances into cols H/G; they must net to 0.

## Recurring items and how they are booked
- **Umpqua → BMO wires lose $25**: Fund Transfer at the amount sent + JE Dr 7450 / Cr receiving bank $25.
  Umpqua USD → BMO CAD wires: transfer in USD at the rate printed on the wire (e.g. `97129CAD1.3766`).
- **"FUNDS TRANSFER TO DEP XXXXXX7206"** on Umpqua: an outside account; book a JE to the expense
  (7235 Digital Advertising / 7060 Delivery / 7020 Duties) — ask which.
- **"DEP 5041721448" book transfers**: SMWCO's QBO Checking (1448); Xoro side is 1260 Intercompany.
- **Branch deposits at BMO CAD**: till cash = Cr 1121 Cash–Service Centre; Simon's own cash = Cr 2900 Shareholder Loan.
- **Livingston** debits are duties (7020); a large Livingston *credit* is a tariff (IEEPA) refund → Cr 7020.
- **CAD deposits use rate 1** (Service Centre). A USD rate on a CAD deposit inflates it and blocks the rec.
- **Item receipts → bills**: convert in place (`editEntityWithCustomFlag` `receiptTobill` → Bill page →
  `updateBill`), keeping the receipt date as bill date. Then apply vendor prepayments (1321) on Bill Payment.

## Lessons from Sep 2026 (check these first next time)
- Before booking a vendor payment, look for an existing bill/prepayment under the vendor's exact name
  (e.g. "Formula Resource Group **Ltd.**") — duplicates were found for Formula and Evertime.
- A reconciled bill payment cannot be edited; use a vendor credit instead.
- Xoro recalculates line tax unrounded; add a ±0.01 GST tax adjustment to match an invoice total.
- QuickBooks: bank feed "For Review" and Reconcile are UI-only. Get the 9/30 bank balance from the user
  (Banking tile shows today's balance — back out later activity).
