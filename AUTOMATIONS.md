# Momentum — Completed Automations

Quick index of the automations that are built, tested, and ready to re-run.

> **⚠️ Bank deposit `ExchangeRate`:** Xoro needs it on every deposit to state the amount in CAD (the home currency). The **API does not populate it** — omitting the field stores `0` and zeroes every home-currency GL amount, and hardcoding `1` books a USD deposit as though 1 USD = 1 CAD. Use `xoro_api.exchange_rate_for(date, currency)`, which takes Xoro's own rate for that date from GL postings on accounts in that currency (it raises `AmbiguousRate` rather than guessing when a day has two equally common rates). A deposit in the home currency is rate `1`. Found 2026-09-22 after six USD deposits were posted at rate 1 and had to be corrected via `updateBankDeposit`.

> **Fiscal year folders:** FY ends **July 31**. Month folders live under `CC Expenses/YE <fy>/` and `Bank Reconciliations/FY<fy>/`, where `<fy>` = calendar year for Jan–Jul, calendar year + 1 for Aug–Dec (so `26 07` → YE 2026, `26 08` → YE 2027). Every script derives this from the month (`convert_activity.fiscal_year_end`, `wise_statements.fiscal_year_folder`, `run.sh ye_for_month`) — nothing hardcodes a year.

## 🔄 Ceridian (Dayforce) Payroll Bill

Turns a Dayforce "Funds Summary" payroll PDF into a Vendor Bill in Xoro for Ceridian Corporation — booking wages/CPP/EI by department plus the Dayforce service fee, netted against LTD so the bill balances exactly to the PDF's "Total Payment Due". Built once via browser (no API schema existed for Bill creation), payload now captured for future API-driven runs.

- **Input:** `Vendor Invoices - Trade/Ceridian/YE{year}/{YYYYMMDD}.pdf` — a Dayforce "Funds Summary" for one pay period. Read pages: **Funds Summary** (p1, has the GST-taxed service fee breakdown and the labeled `TOTAL PAYMENT DUE`) and **Journal Entry** (p3, has per-department SAL. AND WAGES / CPP/QPP / E.I. plus the LTD*/\*LIFE/\*AD&D benefits block and account numbers).
- **Bill header:** Vendor **Ceridian Corporation** (existing vendor, Id 317). Payment Terms **Due on receipt** (0 net days, so Due Date auto-populates to the bill date). Account **2100 - Accounts Payable - Trade (CAD)**. Store **CA**. Receipt Date = Bill Date = the file's date (e.g. `20260715.pdf` → 07/15/2026). **Vendor Bill # = the PDF's own Invoice Number** (Funds Summary page, e.g. "222542-448" — not the pay-period reference).
- **Expense lines** (Bill Details → **Expense** tab, not Items):
  1. **7660 - Professional Fees**, amount = the Funds Summary service-fee subtotal *before* GST (e.g. $62.14), **Tax = G (GST Only)** — Xoro auto-computes the GST amount from this line, don't add GST as its own line
  2. **7840 - Employee Benefits**, amount = **LTD\* and STD\* only (whichever appear that period), summed, negative** (e.g. -$65.22 when only LTD appears; -$254.34 = -$189.12 STD + -$65.22 LTD when both appear) — **not** combined with LIFE, and not positive. This was the key discovery: the obvious reading of the Journal Entry (sum all short/long-term-disability + LIFE lines, all credits) doesn't balance the bill; only negative-LTD/STD-alone does. AD&D and \*LIFE are **not** on this bill at all.
  3. **2900 - Shareholder Loan - Simon Pennell**, amount = **SP.DEDNS, negative** (e.g. -$767.33), **only when SP.DEDNS appears** on the Journal Entry for that period — per user instruction, this deduction line books against the shareholder loan account, not a wash/clearing account.
  4. One line per department per Journal Entry component, **no tax**: `7120/7380/7780` (Salaries - Direct/Selling/Administration, one per dept), `7880` (CPP Employer's Expense, one per dept), `7900` (EI Employer's Expense, one per dept) — i.e. 3 lines × however many departments appear in the Journal Entry.
  - **Not included on this bill:** the unlabeled per-dept "Employee Benefits" sub-line (e.g. $14.26/$8.40/$10.90), Accrued Vac / Holiday Pay Payable (2270), AD&D, and \*LIFE — these are part of the Journal Entry's balanced total but aren't part of the bill total. Net Pay/Remittances (1160 credits) aren't on the bill either — Ceridian sweeps those directly, this bill is just the expense-recognition + service-fee side.
  - **Self-check:** the running Balance Due should land exactly on the Funds Summary's `TOTAL PAYMENT DUE` line. If it doesn't, something from the list above got included/excluded wrong — solve for the discrepancy by isolating which single line's sign/inclusion closes the gap (that's how the LTD-only-negative rule was found) rather than guessing.
- **API for future runs:** no schema getter exists for Bill creation (`BillWebMethods` has no `getBillObjSchema`), so this first bill was built live in the browser and its request captured. `BillWebMethods.asmx/createNewBill`, param `billJsonObj` (JSON-stringified, same double-encoding pattern as other Xoro writes):
  ```json
  {
    "billHeader": {
      "Id": 0, "StoreId": "10001", "VendorId": "317", "TypeId": 20,
      "AccountPayableId": "B7B8887984D745EE867FD5B84456",
      "TxnDate": "07/15/2026", "BillDate": "07/15/2026", "DueDate": "07/15/2026",
      "ExchangeRate": 1, "CurrencyId": 1, "PaymentTermId": "1285",
      "TotalAmt": 21506.48, "TotalTaxAmt": 3.11,
      "StoreName": "CA", "VendorBillNumber": "222542-448"
    },
    "billItemLineArr": [],
    "billExpenseLineArr": [
      {
        "AccountId": "B7D04105A81DDE822DF3BCBE4036", "AccountName": "7660 - Professional Fees",
        "Amount": "62.14", "TaxCodeId": "2", "StoreId": "10001", "StoreName": "CA",
        "TaxData": {"totalAmount": 3.107, "taxItems": [{"itemId": 110, "itemName": "GST Purchase 5%", "itemAmount": 3.107, "lineAmount": "62.14", "itemRatePerc": 5}]},
        "TaxAmt": 3.107, "LineSeq": 1
      },
      {
        "AccountId": "B7D04105A81DE41D478BBD874B0A", "AccountName": "7840 - Employee Benefits",
        "Amount": "-65.22", "TaxCodeId": "", "StoreId": "10001", "StoreName": "CA", "LineSeq": 2
      }
    ]
  }
  ```
  (Untaxed lines omit `TaxData`/`TaxAmt`.) Vendor search: `Common.asmx/getGeneralEntityListByKeyword` (`keyword`, no `entityTypeId` needed) — Ceridian Corporation is `Id: 317`. Account/store/payment-term/tax-code ids all come from `BillWebMethods.getDataForBill` (`AccountPayableList`, `ExpenseAccountList` — accounts here have no `AccountNumber` field, match by `Name` prefix like `"7660 -"` — `StoreList`, `PaymentTermList`, `TaxCodeList`).
- **⚠️ Session conflict:** triggering `xoro_login.login()` (or any script-side re-login) while the user has Xoro open in a browser **evicts their browser session** — don't call it during a live browser-driven task. If a session bounce happens mid-task, stop and have the user log back in rather than re-triggering a script login.
- **Verified:**
  - July 2026 pay period (`20260715.pdf`) — Bill `CA-B002004` created, 11 expense lines (7660, 7840 LTD-only, 9× dept lines), Balance Due $21,506.48, exact match to the PDF's Total Payment Due.
  - July 2026 pay period #2 (`20260731.pdf`) — Bill `CA-B002005` created, 12 expense lines (7660, 7840 = STD+LTD combined, 2900 = SP.DEDNS, 9× dept lines), Balance Due $63,340.66, exact match to the PDF's Total Payment Due. First period to surface STD and SP.DEDNS as line types.
- **TODO:** switch from browser-driven to `createNewBill` API call directly now that the payload shape is confirmed; generalize department/line count (both periods so far had 3 depts — 100/200/300 — confirm the approach holds for periods with a different department count); the `20260815.pdf` source file was found to be a byte-identical duplicate of `20260731.pdf` and needs to be re-saved by the user with the real August 15 data before that period's bill can be built.

## ✅ CC 4002 DHL Reconcile (MyBill fetch → parse → two Xoro statements)

Amex Sofia 4002 is the DHL card: each DHL charge is a whole invoice whose shipments split across EU VAT / UK VAT / Duties & Brokerage / Customer Delivery Fees / GST / Freight. So the month is booked in two passes.

- **Folder:** `Projects/momentum/4002 DHL reconcile/`; month folder `.../Amex Sofia 4002/<YY MM>/` holds `activity.csv` + the Amex statement `YYYY-MM-DD.pdf`
- **Pass 1 — non-DHL lines:** `convert_activity.py amex_sofia "<YY MM>"` (`dhl_filter` holds the DHL charges back and writes them into `DHL Reconcile.xlsx` col A). Upload those lines via `create_bank_statement` with an **interim** `EndBalance` = PDF balance − DHL total, then `start_reconciliation` at the **true** PDF balance. The open rec shows a difference equal to the DHL total until pass 2.
- **Pass 2 — DHL invoices:**
  1. `python3 mybill_fetch.py "<month folder>"` — pulls every matching invoice PDF from MyBill (mybill.dhl.com has no billing API; this drives the logged-in chrome-debug Chrome over CDP, IPv4 or IPv6 on 9222). Matches each Amex DHL charge to an invoice by **amount + date** (charge date within −3…+7 days of the invoice due date); stops before downloading if anything is unmatched/ambiguous. Saves as `<amount> <invoice#>.pdf`.
  2. `./run.sh "<YY MM>"` — parses the three DHL PDF formats (YVRIR / YVRR / E10), writes `DHL Reconcile.xlsx` + `dhl_bank_statement.csv` (4 GL-summary lines dated the statement closing date). Every invoice must allocate to its total (`dhl_review.csv` otherwise).
  3. Upload the 4 summary lines via `create_bank_statement` with `EndBalance` = PDF balance → the rec's difference closes.
  4. **GST / EU VAT / UK VAT bill:** `./run.sh "<YY MM>" --bill` (= `dhl_bill.py <folder>` dry-run, then `--create` on confirmation) — one Vendor Bill on **DHL Express Canada (387)** dated the statement closing date, store CA, AP 2100, term "Credit Card" (1318): expense lines **2251 - VAT NL - Paid** = EU VAT total and **2245 - VAT UK - Paid** = UK VAT total (no line tax), plus a **tax adjustment** `GST Purchase 5%` (item 110) = the GST total — so the bill total equals the "GST, EU, UK VAT" statement line. Totals come from the TOTAL row of `dhl_reconcile_lines.csv`; vendor bill # `DHL GST-VAT <YYYY-MM-DD>`. Refuses if a DHL bill for that date + total is already in the GL. Via `BillWebMethods.createNewBill` (payload shape in `XORO_API.md`); Aug 2026 → `CA-B002020`, 5,185.73, GL confirmed (2251 / 2245 / 2226 GST-HST Payable Dr, 2100 Cr).
  5. **Paying that bill from 2106 (Amex Sofia 4002) is still manual** — the UI payment's save request hasn't been captured, so no API call exists for it yet (see `XORO_API.md` → BillWebMethods). Aug 2026's was paid in the UI (`B001681`).
- **Verified:** Aug 2026 (statement 09/10/2026) — 31 DHL charges, 31/31 matched to MyBill invoices uniquely by amount and every one charged on its due date (or +1); 31 PDFs downloaded in ~1 min; parsed total $10,457.37 = the exact gap on rec 979; two statements posted (25 + 4 lines) ending at −15,853.54; GST/VAT bill `CA-B002020` created by API.
- **Gotchas found:** Amex changed the 4002 export layout (header on line 1, description col 2 — `bank_configs.json` updated 2026-09-21); E10 charge lines over $1,000 carry a thousands comma the parser used to skip (fixed — it had silently dropped a $1,483.60 GST line).
- **See:** [`4002 DHL reconcile/README.md`](4002%20DHL%20reconcile/README.md)

## ✅ Stripe Deposits (payout → Xoro bank deposit)

For a Stripe payout, matches each charge/refund to a Xoro undeposited Customer Deposit (`LineRefNo` == the Stripe charge description, e.g. `CA-CD038402` / `US-CD038326`) and books a bank deposit — payment line(s) + a fee line (+ FX-rounding line if needed), balancing to the payout exactly. Same `BankDepositWebMethods.createBankDeposit` mechanism as Shopify; only the match key differs.

- **File:** `stripe_deposits.py` (reads Stripe with the restricted `rk_live_` key in `.env` — `STRIPE_LIVE_KEY`; writes to Xoro via `xoro_webmethods`)
- **Run:** `python3 stripe_deposits.py` (dry-runs the most recent payout), `python3 stripe_deposits.py 258.67` (by amount), `python3 stripe_deposits.py "" 2026-09-01 2026-09-30` (date range) — dry-run by default; `create_stripe_deposit(amount=..., dry_run=False)` to post
- **Accounts (per payout currency, from Stripe's own external_accounts + the manually-booked Aug 2026 deposits):** CAD → **1160 BMO 41547651 (CAD)** (BMO …7651), fee **7455**; USD → **1170 BMO 44569097 (USD)** (BMO …9097), fee **7456**; FX 8150/8151. Memo `stripe cad` / `stripe usd` (matches the manual convention).
- **Duplicate guard:** refuses if a Bank Deposit of the same amount exists within ±3 days on the deposit-to account (`check_duplicate=False` to override) — the Aug 2026 CAD payouts had been booked by hand, so this matters.
- **Verified live 2026-09-21:** three September payouts created and confirmed in the GL — `po_…s3kEkQhs` 09/16 **257.30 USD** → 1170 (GL 647146), `po_…U58Ma1WB` 09/18 **258.67 CAD** → 1160 (647147), `po_…UeXrY6BV` 09/21 **480.24 CAD** → 1160 (647148); each = gross charge − Stripe fee, one matched undeposited row, no FX residual. Guard proven against the already-booked 08/21 payout (`BD052047`).
- **Also:** `Stripe Payouts/payouts.py [start] [end]` — read-only listing of payouts + balance transactions (stdlib only); handy for eyeballing a month before running the deposits.

## ✅ Shopify Deposits (Consolidated + Service Centre)

For a Shopify payout, matches its orders to Xoro undeposited payments (`ChequeNo` == Shopify order number) and books a bank deposit — payment lines + a fee line + an FX-rounding line, balancing to the payout exactly. Runs for both stores: Momentum Watch US (USD → Umpqua 1140) and Momentum Watches Service Centre (CAD → BMO 1160).

- **File:** `shopify_deposits.py`
- **Run:** `python3 shopify_deposits.py` (dry-runs the most recent payout), `python3 shopify_deposits.py 5898.38` (a specific payout by amount), or call `create_shopify_deposit(payout_id=..., store=..., dry_run=False)` directly
- **Duplicate guard:** refuses to create a deposit if one already exists for the same amount within ±3 days of the payout date (`check_duplicate=True` by default) — added after discovering most of a month's payouts had already been booked manually before the automation ever touched them
- **Orders often haven't synced from Shopify into Xoro's Undeposited Funds yet** when a payout is first built, so a freshly-created deposit is frequently short. **`python3 shopify_deposits.py --retry [store]`** (or `retry_open_deposits(store)`) re-scans every deposit whose memo still has `- ERROR:`, checks each missing order against current Undeposited Funds, and tops it up in place via `BankDepositWebMethods.updateBankDeposit` — no void/recreate needed. See `XORO_API.md` → `BankDepositWebMethods.asmx` for the method.
- **Deleted Shopify orders:** a refund can reference an order Shopify has permanently deleted (confirmed via REST batch, REST single, and GraphQL all returning nothing) — unrecoverable, the raw Shopify order id survives in the memo as `shopify_id:<id>(refund -X.XX)` for manual reference, but there's no order # to retrieve.
- **Verified:** July 2026, full month, both stores — 33 US + 21 CAD payouts total. 25 were genuinely new (no pre-existing deposit) and created live; 29 already had a manually-created deposit and were left alone. Of the 25 created, several were initially short on deleted-order refunds or not-yet-synced orders; `--retry` swept and closed every gap that had a real order # behind it (12 CAD deposits topped up to exact balance in one pass) — the only unresolved gaps left afterward are deleted-order refunds (unrecoverable) and one order still pending Shopify→Xoro sync.
- **See:** `XORO_API.md` → `BankDepositWebMethods.asmx` for the underlying API methods (`createBankDeposit`, `updateBankDeposit`, `getBankDepositObjInfoFromId`, `voidBankDeposit`).

## ✅ Afterpay Deposits (settlement CSV + bank export → Xoro bank deposit)

Afterpay pays the US store net of its merchant fee, batching by **settlement date**, and the money lands in Umpqua 1–3 days later — so one `AFTERPAY … EDI PAYMNT` credit can cover several settlement dates (a refund on one date is carried into the *next* payout). Matches each bank credit to the contiguous run of settlement dates that sums to it, then books the deposit.

- **Folder:** `Projects/momentum/Afterpay Reconcile/`
- **Inputs:** the Afterpay settlement export (Business Hub → Reconciliation → Settlement Export) and the Umpqua/Columbia activity CSV for the month
- **Run:** `python3 afterpay_deposits.py <settlements.csv> <umpqua_export.csv>` (dry-run), `--create` to post. `afterpay_reconcile.py <settlements.csv>` remains as the per-settlement-date summary view.
- **The join is two hops:** Afterpay only knows its own `Merchant Order ID` token, so token → **Shopify order search** → `order_number` → Xoro `ChequeNo` (same search trick the PayPal flow uses — the token isn't a field on the order but Shopify indexes it). Confirmed working on all 13 Aug/Sep tokens.
- **Accounts:** deposit-to **1140 Umpqua Bank 1729 (USD)**, fee **7456 CC Processing Fees (USD)**. Memo `afterpay usd`.
- **Fee line = gross − bank amount**, not the sum of Afterpay's stated fees: Afterpay nets the batch and rounds **half-up** (311.00 − 14.295 → pays 296.71), so the stated fees can be a cent off what the bank actually paid. The bank is the authority; the ≤1¢ difference is absorbed in the fee line.
- **Split tenders work as-is:** an order part-paid by Afterpay has a Xoro undeposited row for the Afterpay portion only (order 68251: $716.25 order, $431.25 Afterpay row) — which is what the settlement's Order Amount matches.
- **Refunds keep the fee:** a refunded order gets both its charge and refund Xoro rows; Afterpay doesn't return the original merchant fee, so booking both legs surfaces the real loss (order 68281, Aug 2026).
- **Duplicate guard:** refuses if a Bank Deposit of the same amount already sits on 1140 within ±3 days.
- **Verified:** Aug 2026 — 5 Umpqua credits ($296.71 / $411.54 / $594.50 / $138.99 / $272.78) all explained and balanced to the cent; the $138.99 one nets the 08/12 refund against the 08/17 settlement. Settlements dated 07/30 (paid before the window) and 08/30–09/03 (after) correctly left for their own months.

## ✅ PayPal Monthly CSV (API → FY folder)

Replaces the manual "download the monthly activity CSV from PayPal's reports UI" step. Pulls the month from `/v1/reporting/transactions` and writes PayPal's own 18-column export format into `Bank Reconciliations/FY{fy}/Paypal/{YY MM}/`.

- **File:** `paypal_statements.py` — **Run:** `python3 paypal_statements.py 2026-08` (`--stdout` to preview, `--out DIR` to write elsewhere)
- **Auth:** `PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET` in `.env` (live; the app has the `reporting/search/read` scope). Stdlib only.
- **Verified 2026-09-22:** generated August 2026 and diffed against the hand-downloaded export — **145/145 transactions, identical header, zero field mismatches, identical row order.**
- **Format details that had to be matched:** `sales_tax_amount` is reported by the API positive but the export negates it; a currency-conversion/refund row inherits the parent payment's `shipping_amount` (the API only puts it on the parent); `Reference Txn ID` is filled only for rows derived from another transaction (an Express Checkout Payment carries a `paypal_reference_id` pointing at the *order*, and the export leaves the column blank); rows are grouped by currency — primary (USD) first, then alphabetically — each block oldest-first, with a row following the transaction it references when timestamps tie.
- **⚠️ There is no PDF.** PayPal's REST API exposes only `/v1/reporting/transactions` and `/v1/reporting/balances`; every statement/document endpoint 404s and the transactions endpoint rejects `Accept: application/pdf` (406). The monthly statement PDF can only come from the web reports UI — download it by hand if the audit file is wanted.
- **`month_end_balances(month)`** wraps `/v1/reporting/balances` — the 1143 reconciliation's ending balance straight from PayPal (Aug 2026: USD 2,548.06), instead of reading it off the last transaction row.
- **For the reconciliation itself**, the API also carries two things the CSV doesn't: `custom_field.shop_id` (which Shopify store an order belongs to — the deposit split is by store, so this removes the documented "check both stores" ambiguity) and a structured `transaction_event_code`. `invoice_id` (the Shopify order token) is in **both** the CSV and the API.

## 🔄 PayPal Payout Reconciliation (deposits scripted; Fund Transfers, 1143 statement and workbook still manual)

Turns a monthly PayPal CSV export into per-currency reconciliation sheets, then matches each transaction to a Shopify order and books it into a Xoro bank deposit. Currently run step-by-step via ad hoc scripts in a scratch folder — not yet consolidated into a repo script.

- **Input:** PayPal CSV export (`Bank Reconciliations/FYxxxx/Paypal/{YY MM}/*.CSV`)
- **Output:** save the working workbook (all sheets, with highlighting) **in that same month folder**, not a scratch/temp dir — `Bank Reconciliations/FYxxxx/Paypal/{YY MM}/Paypal Reconciliation - {YYYY MM}.xlsx`, plus a `.csv` copy of just the "All" sheet alongside it
- **Excel prep (per month):**
  1. Drop columns Time Zone, Bank Name, Bank Account, Shipping and Handling Amount, Sales Tax
  2. Split into one sheet per currency (USD/CAD/GBP/EUR/AUD/…); an "All" sheet keeps every row
  3. On each per-currency sheet only: **AutoFilter** the rows where Description = "General Currency Conversion" (FX-conversion side-entries, not real sales) — hide them via the filter, don't delete/exclude them from the sheet. They stay retrievable by clearing the filter, and are also the rows to target (per sheet's own Currency + Description = "General Currency Conversion") for any later manual cross-check of non-USD-received-as-USD amounts.
  4. Sort each per-currency sheet by Description, then Date (oldest first)
- **Match chain (per Transaction ID on a currency sheet):**
  1. Search the transaction ID in the matching Shopify store's order search (GraphQL `orders(query: "<id>")` — the raw PayPal Transaction ID is indexed by Shopify's search even though it isn't literally any single field on the order/transaction record)
  2. Take the matched order's `order_number`
  3. Look it up as `ChequeNo` in Xoro's undeposited transactions for that currency (`BankDepositWebMethods.getBankDepositLinkedUndepositedTransactions`, wrapped by `xoro_webmethods.WebMethodClient.get_undeposited_transactions`)
  4. **Note:** CAD store is "Momentum Watches Service Centre" (`ca-momentumwatch.myshopify.com`, `SHOPIFY_STORE_2`/`SHOPIFY_ADMIN_TOKEN_2`) — not every CAD-currency PayPal transaction belongs to it; some CAD-currency payments turned out to be US-store orders instead, so a miss in one store is worth checking the other before calling it unmatched. Xoro's own `ThirdPartyRefNo`/`ThirdPartySource` fields on undeposited rows are unused (always blank) in this tenant — don't rely on them.
- **Three bank deposits per month, not one per currency sheet** — the split is by *which Shopify store the order actually belongs to* (plus one deposit that's genuinely currency-native), not by PayPal's reported currency:
  1. **Service Centre deposit** — transactions matching the CAD store ("Momentum Watches Service Centre"). Currency **CAD**. Only ever draws from the CAD sheet. Deposit-to: **1145 - Temporary Bank Account (CAD)**.
  2. **Combined "everything else" deposit** — transactions matching the **US store** ("momentum"), regardless of which currency sheet they came from (CAD/GBP/EUR/AUD customers all paying in local currency for a USD-denominated US-store order all land in this one deposit). Currency **USD**. Deposit-to: **1145 - Temporary Bank Account (CAD)**.
  3. **USD sheet's own deposit** — the USD sheet's Express Checkout Payment/Payment Refund rows (already natively USD, no bridging needed). Deposit-to: **1143 - Paypal USD** (`FAccountingId B7D04105A81AC13AE701924645D2`) directly, not 1145.
  - For #1/#2, deposit-to is **1145 - Temporary Bank Account (CAD)** (`FAccountingId B7D1A739F88DCEF8CFF09CA340F8`) — a multi-currency bridging account, but the deposit header `CurrencyCode`/`CurrencyId` must match the **source (Deposit-From) lines' currency** or `createBankDeposit` rejects it (`"Deposit-From account's currency must be same as deposit currency"`). Service Centre lines are CAD (1) → header CAD. US-store lines are USD (1001) → header USD. #3 deposits straight into the currency's own PayPal holding account (1143 USD; presumably 1103 CAD / 1153 GBP / 1157 EUR for those currencies' own sheets if PayPal ever settles them directly instead of through a Shopify-store deposit — not yet tested).
  - **Multiple PayPal transactions can map to the same Shopify order** (e.g. one order paid in two charges, or one charge + several partial refunds) — group matched transactions by `order_number` first, then pull **every** Xoro undeposited row sharing that `ChequeNo` as its own payment line, rather than assuming a 1:1 transaction↔line mapping.
  - One payment line per matched order (the exact undeposited row, `LinkedFlag: true`). A refunded order gets **both** its charge and refund Xoro rows added even though they net to zero — PayPal usually keeps its fee on the original charge even after a full refund, so including both lines (plus that fee in the combined fee line) correctly surfaces the real net loss instead of hiding it. A partially-refunded order can have 3+ Xoro rows (one charge + multiple partial refunds) — add all of them.
  - One combined fee line = sum of the PayPal Fee column values across every matched transaction in that deposit (face value, no FX conversion) — CAD deposit → account 7455 - Credit Card Processing Fees (CAD) (`FAccountingId B7D04105A81C07FA7E88869F40C7`); USD deposit → account **7456 - Credit Card Processing Fees (USD)** (`FAccountingId B7D1B02C7EB5CD837D800F3B405B`, looked up live via `AccountingWebMethods.getAllAccountsForApi` by `AccountNumber`). The fee account's currency must match the deposit header currency, same rule as above.
  - **Timing differs for the combined deposit specifically:** Service Centre and the USD-native deposit get their fee line added upfront, as part of the initial build. The **combined deposit's fee line goes in last** — only once every CAD/GBP/EUR/AUD order for the month is fully resolved and sitting in the deposit (orders often trickle in / get manually resolved over several passes) — summing the fees for whatever's actually present at that point. Adding it early just means recomputing and replacing it again later; better to wait.
  - `TxnDate`: last day of the working month
  - `Memo`: Service Centre deposit uses `"PayPal Service Centre Payout - {Month} {Year}"` (matches prior real deposits found in the GL history for account 1145, e.g. January 2026). The combined deposit uses `"PayPal Payout {CURRENCIES} - {Month} {Year}"` listing every currency sheet that contributed a transaction (e.g. "CAD, GBP, EUR, AUD").
  - **Unmatched transactions** (no Shopify order found in either store, or an order found but no Xoro undeposited row for it) don't get a payment line — instead append a note to the memo naming the order/transaction (e.g. `" - MISSING: order 67604 (txn ...) -- no Xoro undeposited match found"`), and exclude that transaction's fee from the combined fee-line sum (nothing to offset it against).
  - Created via `xoro_webmethods.WebMethodClient.create_bank_deposit`; memo/detail fixes after creation go through `BankDepositWebMethods.updateBankDeposit` (fetch the full object first via `getBankDepositObjInfoFromId`, param name is `bnkDepId` not `bankDepositId`)
- **Excel follow-up:** on each currency sheet, **yellow-highlight** rows whose transaction was added to a bank deposit; **red-highlight** rows that came up missing (no Shopify match at all, or no Xoro undeposited row) — matching the note added to the deposit's memo
- **Verified:** July 2026 — all three deposit types created and reconciled to the penny: `BD052057` (Service Centre/CAD), `BD052058` (combined US-store/USD — **left as a work-in-progress at the user's direction**, they were editing it live in the Xoro UI in parallel with these API calls, e.g. order 67517 was added by them directly, not a bug; the combined fee line is deliberately not added yet; **don't edit it further via API without checking first**), `BD052059` (USD sheet, into 1143 — verified clean, no stray extra lines). Matched/unmatched rows highlighted yellow/red across the CAD/GBP/EUR/AUD/USD sheets. (Dollar totals and row counts are month-specific — not worth recording here; what matters is that each month's run should reconcile the deposit total to the matched lines minus fees, with unmatched items called out in the memo.)
- **TODO:** consolidate the scratch scripts into a real repo script/folder once the process is confirmed across more currencies/months

### Scripted: `paypal_workbook.py`

`python3 paypal_workbook.py 2026-08` writes `Paypal Reconciliation - {YYYY} {MM}.xlsx` (+ a `.csv` of the All sheet) into the month folder, mirroring the hand-built workbooks.

- **All** sheet — every transaction in PayPal's own export order (**grouped by currency**, each block oldest-first), no fills. Sorting the All sheet by date across currencies looks reasonable but doesn't match the hand-built files. **One sheet per currency** (alphabetical after All), holding only the sale rows plus USD's withdrawals, sorted by Description then Date. The General Currency Conversion rows are deliberately **not** on the currency sheets, which is why the check block's conversions figure is a `SUMIFS` against All.
- **Columns** drop Time Zone / Bank Name / Bank Account / Shipping and Handling / Sales Tax, matching the manual prep.
- **Fills reflect Xoro, not a re-match:** yellow where the transaction is on one of the month's posted deposits (or, for a withdrawal, has a Fund Transfer), red where it isn't. After creation the rows have left Undeposited Funds, so status is read from the deposits themselves via `paypal_deposits.deposit_status`.
- **The exchange rate now lives in its own labelled cell** and the formulas reference it, instead of the number being typed into two formulas as `*1.38587` / `/1.38587`.
- **The `.csv` copy** is the All sheet in the same order, no BOM, dates written as datetimes — matching an export-from-Excel of the July file. It is skipped when reading input: only PayPal's own `…-CSR-…​.CSV` is read, or a re-run would consume its own output.
- **Verified:** August 2026 — All + CAD/EUR/GBP/USD, 83 rows yellow, 2 red (the two items named in BD057313's memo). No AUD sheet: no AUD activity that month.
- **Gotcha found:** Xoro's GL names the fund-transfer type **"Transfer Funds"**, not "Fund Transfer" — the duplicate guard matched the wrong string and was silently useless until fixed.

### Scripted: `paypal_deposits.py`

`python3 paypal_deposits.py 2026-08` dry-runs the month; `--create [--only KEY]` posts. Reads the CSV `paypal_statements.py` filed (or `--api`), tags each transaction with its Shopify store from `custom_field.shop_id`, resolves `invoice_id` → Shopify order → Xoro `ChequeNo`, and builds the three deposits.

- **Two things the manual process description left implicit**, both found live:
  1. **Undeposited rows sit in the deposit's currency, not the customer's** — a US-store order is a USD row in Xoro even when PayPal took GBP for it, and Service Centre orders are CAD. Look them up by the deposit's header currency.
  2. **Service Centre order numbers differ between systems** — Shopify names them `C36337`, Xoro holds `36337` (refs `SC-CD…`). The matcher tries the name and then the name without its leading letters.
- **Rows are bounded to the working month.** The documented "pull every Xoro row sharing that ChequeNo" rule drags in later months: order 68503 sold in August and refunded 09/10 pulled that September refund into August, leaving the USD deposit 230.00 short of PayPal's own figure. With the bound, the USD deposit equals PayPal's `USD amounts − refunds − fees` exactly.
- **The combined deposit's fee line is withheld automatically** while anything is still missing, per the documented timing; unmatched items are named in the memo.
- **Verified:** August 2026 — `BD057312` Service Centre 228.56 CAD (5 lines), `BD057313` combined 6,962.85 USD (25 lines, fee held, 2 items flagged in the memo), `BD057314` USD-native 10,447.61 USD (49 lines) — which equals the USD sheet's `Deposits in USD, MINUS refunds, MINUS fees` to the cent.
- **Still manual:** the Fund Transfers for withdrawals, the 1143 bank statement, and the reconciliation workbook.

### PayPal "User Initiated Withdrawal" rows → Xoro Fund Transfers

A PayPal CSV's `User Initiated Withdrawal` rows are PayPal sweeping cash to the real bank (here, Umpqua) — they're not Shopify-order transactions, so they don't go through the match chain above. Each one becomes its own **Fund Transfer** in Xoro: from **1143 - Paypal USD** to **1140 - Umpqua Bank 1729 (USD)**, dated to that row's own date (not end-of-month), memo `"PayPal -> Umpqua"`.

- **The real API is `FundTransferWebMethods.asmx/createFundTransfer`** — found by capturing a live browser save (self-doc GET listing for this service returns nothing/blocked, and the module has no discoverable name via guessing — `FundTransferWebMethods`, `FundsTransferWebMethods`, `TransferFundsWebMethods` etc. all 404 on GET, so brute-force naming alone won't find it; the page is `/Accounting/FundTransfer/FundTransfer.aspx`, transaction type `FUNDS_TRANSFER` = 117, table `TBL_FUND_TRANSFER`).
- **Payload is flat** (no header/line-array nesting like Bank Deposit/Outgoing Payment), param name `fundTransferObjJson` (JSON-stringified, same double-encoding pattern as `bankStmtData`/`bankDepositObjJson`):
  ```json
  {
    "Id": -1, "TxnId": null, "TxnNumber": -1, "TxnDate": "7/10/2026",
    "FundTransferNumber": null, "HomeCurrencyId": 1,
    "TransferFromAccntName": "1143 - Paypal USD", "TransferFromAccntId": "B7D04105A81AC13AE701924645D2", "TransferFromAccntCurrencyId": 1001,
    "TransferToAccntName": "1140 - Umpqua Bank 1729 (USD)", "TransferToAccntId": "B7D04105A81AED1CB3EA3AB9426A", "TransferToAccntCurrencyId": 1001,
    "TransferAmount": "2500", "FinalTransferAmount": 2500,
    "CurrencyId": "1001", "CurrencyCode": "USD", "ExchangeRate": "1.37725", "Memo": "PayPal -> Umpqua"
  }
  ```
  Response `Message` on success: `"Funds transfer record FT###### created Successfully !"`.
- **`voidFundTransfer`** takes `fundTransferId` (not `ftId`/`id`/`FTId` — those all fail with a generic error) → `"Fund transfer deleted successfully"`.
- **False start, worth remembering:** Outgoing Payment (`OutgoingPaymentWebMethods.createOutgoingPayment`) can *also* move money between two of the company's own bank accounts (set header `EntityAccntId`/`EntityCurrencyId`/`EntityName` to the destination account, plus a required int `PaymentMethodId`) and posts a real, balanced GL entry — but it's numbered "OP" (Outgoing Payment), not "FT" (Fund Transfer), which is the wrong transaction type for this use case even though the accounting effect looks identical. Void via `voidOutGoingPayment` (needs both `opId` **and** `ChequeVoidDttm`, or it 500s).
- **⚠️ `SimulationFlag` on `OutgoingPaymentWebMethods.createOutgoingPayment` is NOT a dry-run** — every call that returns `Result: true`, `SimulationFlag` true or false, actually commits a real record. (Unconfirmed whether this also applies to `JournalEntryWebMethods.saveJournalEntry`'s `SimulatationFlag` — treat that as suspect too until verified.) This caused 2 accidental duplicate Outgoing Payments during exploration, since voided.
- **Verified:** July 2026 — all 6 "User Initiated Withdrawal" rows booked as Fund Transfers (`FT001487`–`FT001492`), each dated/amounted to match its own CSV row. USD sheet's withdrawal rows highlighted yellow. (Amounts and dates are month-specific — not recorded here.)
- **Scripted:** `python3 paypal_deposits.py <YYYY-MM> --create --transfers` — one Fund Transfer per withdrawal, each on its own date and amount, `ExchangeRate` from Xoro's GL for that date (both sides are USD, so the rate only states the CAD value; a tied day resolves to the lower rate via `exchange_rate_for(..., on_tie="lowest")`). Skips a transfer that already exists on 1140 for the same date and amount.
- **Verified:** August 2026 — `FT001504`–`FT001510`, 7 transfers totalling $17,000, GL confirmed balanced (1143 net 0.00, each leg mirrored on 1140).

### USD sheet reconciliation check block

A manual check block the user adds below the USD sheet's data rows (starts a few rows after the last data row) cross-checks the USD sheet's own totals against what actually landed in Xoro. Two columns, headed `CAD` / `USD`:

- **USD Equivalent Conversions** — `E` = `SUMIFS(All!E:E, All!D:D, "USD", All!C:C, "General Currency Conversion")` (Gross total of the USD sheet's own hidden/filtered General Currency Conversion rows — pulled from the **All** tab since those rows are only *hidden*, not present as data, on the per-currency sheet itself; see the AutoFilter note above). `D` = that USD figure × the real CAD/USD exchange rate (see below) — **not** a hardcoded guess.
- **CAD amounts of PP received - SC** — `D` = the Service Centre deposit's own total (`BankDepositWebMethods.getBankDepositObjInfoFromId` → `BankDepositHeaderObj.TotalAmount`, e.g. `BD052057`). `E` = `D` ÷ the same exchange rate.
- **Non-USD amounts received as USD in Xoro** — the combined US-store deposit's total in both currencies (e.g. `BD052058`): `E` = its `TotalAmount` (USD), `D` = that × the exchange rate (or the CAD figure Xoro's own UI shows for it, if that deposit mixes original currencies whose individual conversions don't reduce to one flat multiply). **This deposit is often still being live-edited** — re-pull its current `TotalAmount`/`ExchangeRate` before trusting these two cells, don't reuse a stale number.
- **USD amounts** — `=SUMIF(C2:C{lastrow},"Express Checkout Payment",E2:E{lastrow})` (USD sheet's own Gross total for real sales)
- **FEES** — `=SUM(F2:F{lastrow})` (USD sheet's own Fee column total)
- **REFUNDS** — `=SUMIF(C2:C{lastrow},"Payment Refund",E2:E{lastrow})`
- **Deposits in USD, MINUS refunds, MINUS fees** — `=SUM()` of the three rows above
- **⚠️ Exchange rate lesson:** don't hardcode the deposit-to-CAD exchange rate, and don't force a bank deposit's own `ExchangeRate` field to `1` when creating it (see the `BD052058` note above) — Xoro has a live default rate that populates on its own; overriding it just creates a mismatch to untangle later. Once a deposit is created, its real posted rate is `BankDepositHeaderObj.ExchangeRate` — use that (not a hand-picked constant) for any CAD conversion tied to that deposit.
- **The block's actual sanity check:** `USD Equivalent Conversions` (E82, PayPal's own record of what it converted non-USD receipts into) should equal `CAD amounts of PP received - SC` (E83) + `Non-USD amounts received as USD in Xoro` (E84) — i.e. PayPal's internal conversion total should equal what actually landed across the two non-USD deposits (`BD052057` converted to USD + `BD052058`'s own USD total). Confirmed tying out to the penny on the July 2026 run once E84 was refreshed to `BD052058`'s current live total — if it doesn't tie out, re-pull `BD052058`/`BD052057`'s current totals before assuming something's wrong, since they're being live-edited and go stale fast.

#### Troubleshooting

- **A deposit total not matching the PayPal-sheet-derived figure doesn't necessarily mean an error** — it can mean a *real* refund happened outside PayPal. Example from July 2026: order 67929 showed a $223.25 gap between PayPal's Gross ($228.95, one charge, no PayPal-side refund) and Xoro's actual deposit line total ($5.70) — because Xoro had a **separate $223.25 refund on that order that never went through PayPal at all** (different gateway/manual/store-credit), invisible to the PayPal CSV but real in Xoro. Since building a deposit pulls *every* Xoro line sharing a matched `ChequeNo`, a non-PayPal refund on the same order rides along and reduces the total. To find the exact order behind a gap: sum PayPal Gross per matched order number, sum Xoro's actual line amounts per the same order number (from the live deposit, not the sheet), and diff — the offending order surfaces immediately.
- **Formula cells written via a script (not Excel itself) show blank/0 when read back** (e.g. via `openpyxl` with `data_only=True`) **until Excel actually opens, recalculates, and re-saves the file** — a script-written formula has no cached result yet. This can look like "the numbers don't match" when really nothing has been computed yet. Fix: set `wb.calculation.fullCalcOnLoad = True` before saving (openpyxl `CalcProperties`) so Excel is forced to recompute everything the moment it opens, instead of trusting a (nonexistent) cache.

### PayPal USD bank statement (account 1143)

Once the reconciliation check block above is filled in, post a monthly bank statement for **1143 - Paypal USD** itself, via `xoro_webmethods.WebMethodClient.create_bank_statement` (`ConnectBankWebMethods.uploadBankStatementManual` — same working path documented in `XORO_API.md`). A reference CSV in the `BankStatementImport.csv` template shape (`**Date,**Amount,Payee,Description,Reference,ChequeNumber`) can be built alongside it, but the real action is the API call, not the file.

Lines:
1. **"Deposits in USD, MINUS refunds, MINUS fees"** — date = last day of the working month, amount = the USD sheet's `D90` (USD amounts − refunds − fees; positive/deposit)
2. **"USD Equivalent Conversions"** — same end-of-month date, amount = `D82` (the `E82` SUMIFS-from-All total × that month's *real* posted exchange rate — see the exchange-rate lesson above; never a hardcoded rate)
3. **One line per "User Initiated Withdrawal"** row on the USD sheet — each keeps **its own actual date and amount** (negative/debit), not end-of-month like the two rows above
- **Verified:** July 2026 — 8 lines posted to account 1143 ($13,133.71 + $9,160.00 + six withdrawals), Xoro confirmed `"8 transaction(s) imported successfully"`.

### Final step: Fund Transfer 1145 → 1143

Once all three bank deposits are done for the month, transfer the "USD Equivalent Conversions" amount out of the bridging account into PayPal USD proper: **Fund Transfer from 1145 - Temporary Bank Account (CAD) to 1143 - Paypal USD**, dated the last day of the working month, using that same month's real posted exchange rate (not hardcoded — see the exchange-rate lesson above).

- **Transfer Currency = USD, Amount = E82** (the raw USD Equivalent Conversions figure, not its CAD-converted D82) — since the destination account (1143) is USD-native, specify the transfer in USD and let Xoro compute the CAD/home-currency side via the exchange rate. (First guess was backwards — CAD/D82 — corrected to USD/E82.)
- Same `FundTransferWebMethods.createFundTransfer` mechanism as the withdrawal transfers (see above); `TransferFromAccntId` = 1145's FAccountingId, `TransferToAccntId` = 1143's FAccountingId, `TransferFromAccntCurrencyId` = 1 (CAD), `TransferToAccntCurrencyId` = 1001 (USD), `CurrencyCode`/`CurrencyId` = USD/1001, `ExchangeRate` = that month's real rate.
- Memo convention: `"PayPal -> Umpqua FX transfer"`.
- **Verified:** July 2026 — `FT001496`, $9,160.00 CAD from 1145 → $6,609.57 USD into 1143, dated 07/31/2026. Confirmed via GL on account 1143.

### TODO: Bank Reconciliation for account 1143

Once the bank statement is uploaded, the last remaining step each month is to actually **Bank Reconcile** account 1143 in Xoro — not yet built or attempted.

- **Setup only is automated:** `xoro_webmethods.WebMethodClient.start_reconciliation` → `BankReconcileWebMethods.addBankRecHeader` creates the reconciliation with a beginning/ending balance and statement date. Ending balance = PayPal's own last-reported `Balance` for the month (the USD sheet's last real transaction row before month-end — July 2026 was $1,319.11, dated 7/29 since PayPal had no activity 7/30–31).
- **Line-by-line matching and finishing it is completely unexplored** — `finishBankRec`, `reconcileBankAccountLine`, `reconcileMultipleBankAccountLine`, `getBankTransactionsToReconcile` (all on `BankReconcileWebMethods`/`ConnectBankWebMethods`) exist per the service listing but have never been called or captured from a live UI flow in this codebase. Given how differently Fund Transfer's real payload shape turned out from a first guess, expect the same here — plan to capture a live browser save (same method used to crack Fund Transfer) rather than guessing the shape blind.

## ✅ Wise Statements

Pulls Wise (CAD/EUR/GBP) balance statements for a given month and drops PDF + CSV straight into the OneDrive bank-reconciliation tree — only for currencies that actually had activity that month.

- **Folder:** `Projects/momentum/`
- **Run:** `python3 wise_statements.py 2026-07` (all of CAD/EUR/GBP) or `--currency GBP` for one
- **Auth:** reads `WISE_API_KEY` from `Projects/momentum/.env` (gitignored). Personal access token from Wise → Settings → API tokens. Business profile (ST. MORITZ WATCH CORP) is resolved automatically via `GET /v2/profiles`.
- **Logic:** for each of CAD/EUR/GBP, checks the compact JSON statement for the month first — skips the download entirely if there are zero transactions, so empty months don't clutter the reconciliation folders
- **Output:** `.../Bank Reconciliations/FY{fy}/Wise {CUR}/{YY MM}/statement_{balanceId}_{CUR}_{start}_{end}.{pdf,csv}` — reuses the month folder if it already exists (e.g. from a manual pull), otherwise creates it
- **Stdlib only** — no `pip install` needed
- **Verified:** July 2026 — GBP had 1 transaction (£41.91 deposit from AVIVA PLC) and got PDF+CSV written to a newly created `Wise GBP/26 07/`; CAD and EUR had no activity and were skipped
