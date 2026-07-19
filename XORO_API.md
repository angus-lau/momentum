# Xoro ERP API Reference

Consolidated reference for Momentum (St. Moritz Watch accounting automation). Combines:

1. The **official Xoro-supplied guide** (5 documented endpoints, dated 23 Jun 2026 — source `.docx`).
2. **Additional endpoints** discovered by read-only probing.
3. The **real internal API** (ASP.NET `.asmx` WebMethods) that the Xoro UI actually uses.
4. The **auth situation**, including a known **security hole**.

The shared client lives in `xoro_api.py` (`/xerp/` REST path) and `xoro_webmethods.py` (`.asmx` path); auth/cookie handling is in `xoro_login.py`.

---

## ⚠️ Security Notice — read before using

**The `/xerp/` REST API has an ASP.NET forms-auth case-sensitivity bypass.**

- The API-key + password Xoro provided do **not** authenticate the documented lowercase `/xerp/...` path — it always `302`s to `login.aspx`.
- Capitalizing the first path segment — **`/Xerp/...`** — reaches the *entire* API **unauthenticated**, exposing all financial data (reads) and very likely the write endpoints (`creditmemo/import`, `bill/import`, `bankdeposit/import`).
- This is a genuine security hole. Momentum was informed; the decision was to build on it anyway ("they won't fix it"). If Xoro ever fixes it, flip the client's `path_prefix` from `Xerp` back to `xerp` and rely on the session-cookie auth described below.
- **Do not commit any Xoro credentials.** The provided API-key/password are non-functional against `/xerp/` regardless; real auth is the browser session cookie.

The legitimate auth path is the **session cookie** (`__Auth` + `ASP.NET_SessionId`), used by the `.asmx` internal API. See [Authentication](#authentication).

---

## Base URL & conventions

- **Base URL:** `https://momentum.xoro.one`
- **REST route convention:** `Xerp/{controller}/{action}` (capital `X` = the bypass; lowercase = the intended, currently-broken auth path).
- **Response envelope:** `{ Result, ErrorCode, Message, Data, Page, TotalPages }`. Success = `Result == True && ErrorCode == 0`.
  - Most endpoints return a list under `Data`.
  - GL returns `Data = { transactionList, deletedTxnNumbers }`.
- **GL pagination:** `page_size` is capped at **100**.

---

## Documented API endpoints (official guide)

### 1. Invoice API

Retrieve invoice records by number, sales-order reference, customer, date range, status, shipment, or third-party reference.

**`GET Xerp/invoice/getinvoice`**

| Parameter | Description |
|---|---|
| `id` | Internal Xoro invoice identifier. |
| `invoice_number` | Invoice number assigned to the transaction. |
| `so_number` | Sales Order number associated with the invoice. |
| `created_at_min` / `created_at_max` | Invoices created on/after or on/before the date-time. |
| `updated_at_min` / `updated_at_max` | Invoices updated on/after or on/before the date-time. |
| `status` | Invoice status filter. |
| `since_id` | Records with an identifier greater than the value (incremental sync). |
| `customer_name` | Customer associated with the invoice. |
| `store_name` | Store associated with the invoice transaction. |
| `has_shipment` | Whether the invoice has an associated shipment record. |
| `third_party_ref_no` | Reference number assigned by an external/third-party system. |
| `ref_no` | Reference number associated with the invoice transaction. |
| `page` | Page number for paginated results. |
| `header_only` | When enabled, returns only header info (no line-level details). |
| `third_party_customer_id` | Customer identifier from the originating third-party system. |
| `third_party_company_id` | Company identifier from the originating third-party system. |
| `so_ref_no` | Reference number of the linked Sales Order. |
| `third_party_source` | Name/identifier of the third-party application. |

---

### 2. Credit Memo API

Retrieve existing credit memos and import new ones. Used for customer returns and refunds.

**`GET Xerp/creditmemo/getcreditmemo`**

| Parameter | Description |
|---|---|
| `id` | Internal Credit Memo identifier. |
| `credit_memo_number` | Credit Memo number. |
| `created_at_min` / `created_at_max` | Created on/after or on/before the date-time. |
| `updated_at_min` / `updated_at_max` | Updated on/after or on/before the date-time. |
| `status` | Credit Memo status filter. |
| `since_id` | Records with an identifier greater than the value (incremental sync). |
| `customer_name` | Customer associated with the transaction. |
| `store_name` | Store associated with the transaction. |
| `third_party_ref_no` | Reference number assigned by an external/third-party system. |
| `ref_no` / `ref_no_2` | Primary / secondary reference number. |
| `third_party_source` | Third-party application name/identifier. |
| `page` | Page number for paginated results. |
| `account3pl_id` | Third-party logistics account identifier. |
| `include_lines` | When enabled, returns line-level details. |
| `exclude_processed` | When enabled, excludes already-processed records. |

**`POST Xerp/creditmemo/import`**

Create and import credit memo transactions — header, returned item details, inventory location adjustments, refund info, and related customer credit data.

<details>
<summary>Sample request body</summary>

```json
{
  "creditMemoHeader": {
    "ThirdPartyRefNo": 933018730668,
    "ThirdPartySource": "https://development-xoro.myshopify.com-Test Shopify",
    "ExchangedGiftCardItem": null,
    "TxnDate": "23 April, 2025 02:43:45",
    "CustomerName": "Shopify development-xoro ",
    "CustomerId": 13,
    "StoreName": "OFFICIAL_XYZ",
    "SaleStoreName": "piyush_store",
    "InvoiceNumber": "piy123-I000896",
    "CurrencyCode": "CAD",
    "ShipToAddr": "431 2nd Avenue West",
    "ShipToCity": "North Bay",
    "ShipToState": "ON",
    "ShipToCountry": "CA",
    "ShipToZpCode": "P1B 3L4",
    "RefNo": "10174",
    "ThirdPartyNotes": null,
    "ShippingCreditAmount": 0,
    "ThirdPartyDisplayName": "Shopify",
    "ThirdPartyIconUrl": "https://cdn.shopify.com/assets/images/logos/shopify-bag.png",
    "ProjectClassId": 4,
    "ThirdPartyTotalRefundAmount": 120.0,
    "ThirdPartyAdjustTotalFlag": true,
    "ThirdPartyAdjSku": "Mouse_mo",
    "ExpenseShippingAccntName": "Shipping Expense",
    "SalesRepFullName": "Piyush Rep"
  },
  "creditMemoItemLineArry": [
    {
      "ItemNumber": "TubeLight-metallic-RD",
      "Description": "TubeLight-metallic-RD",
      "ReturnNotes": "TubeLight-metallic-RD returned and restocked.",
      "Qty": 1.0,
      "DiscountTypeId": 10,
      "Discount": 0,
      "ProjectClassId": 4,
      "InventoryLocChangeList": [
        { "LocationName": "META POLIS", "DeltaQty": 1.0 }
      ]
    }
  ]
}
```
</details>

**Credit Memo Header fields**

| Field | Description |
|---|---|
| `ThirdPartyRefNo` | Unique reference in the third-party system. |
| `ThirdPartySource` | Third-party application the credit memo originated from. |
| `TxnDate` | When the transaction was created in the source system. |
| `CustomerName` / `CustomerId` | Customer name / internal Xoro customer id. |
| `StoreName` | Store processing the credit memo. |
| `SaleStoreName` | Sales store of the original invoice/order being credited. |
| `InvoiceNumber` | Invoice the credit memo is created against. |
| `CurrencyCode` | Transaction currency. |
| `RefNo` | Reference number for the transaction. |
| `ShippingCreditAmount` | Shipping amount credited/refunded. |
| `ProjectClassId` | Project/Class association. |
| `ThirdPartyTotalRefundAmount` | Total refund from the third-party source. |
| `ThirdPartyAdjustTotalFlag` | Whether to adjust the refund using the adjustment SKU. |
| `ThirdPartyAdjSku` | SKU used to post refund adjustments when refund total differs from item return value. |
| `ExpenseShippingAccntName` | Expense account for shipping credit/refund adjustments. |
| `SalesRepFullName` | Sales rep of the original sale/credit memo. |

**Credit Memo Item Line fields**

| Field | Description |
|---|---|
| `ItemNumber` | Item being returned/credited. |
| `Description` | Description of the returned item. |
| `ReturnNotes` | Reason for the return/credit. |
| `Qty` | Quantity returned. |
| `DiscountTypeId` | Discount type applied to the line. |
| `Discount` | Discount amount applied. |
| `ProjectClassId` | Project/Class association. |
| `InventoryLocChangeList` | Inventory location updates on return. |

**InventoryLocChangeList fields**

| Field | Description |
|---|---|
| `LocationName` | Inventory location where the returned quantity is adjusted. |
| `DeltaQty` | Quantity adjustment for the location. |

---

### 3. Item Receipt API

**`GET Xerp/bill/getitemreceipt`**

Retrieve item receipt details for vendor receipt tracking and inventory receiving analysis.

| Parameter | Description |
|---|---|
| `id` | Internal Item Receipt identifier. |
| `item_receipt_number` | Item Receipt number. |
| `created_at_min` / `created_at_max` | Min/max creation date. |
| `updated_at_min` / `updated_at_max` | Min/max update date. |
| `status` | Item Receipt status. |
| `since_id` | Records after the specified identifier. |
| `vendor_name` | Vendor associated with the receipt. |
| `store_name` | Store associated with the receipt. |
| `ref_no` | Reference number. |
| `page` | Page number for paginated results. |
| `account3pl_id` | Third-party logistics account identifier. |

---

### 4. Bank Statement API

> ⚠️ The documented **`POST Xerp/bankdeposit/createstatement`** is **not deployed on the Momentum tenant** — it returns `404` every way (its sibling actions `bankdeposit/import` and `bankdeposit/create` return `200`, so the *action* is missing, not auth). **Do not use it.** The real bank-statement upload lives on the internal `.asmx` service below and is fully working. See [Bank statement upload — the working path](#bank-statement-upload--the-working-path).

<details>
<summary>Sample request body (per the doc)</summary>

```json
{
    "BankStatementHeader": {
        "ImportTypeId": 20,
        "StartDate": "06/01/2026",
        "EndDate": "06/30/2026",
        "EndBalance": "45000"
    },
    "BankStatementLineArr": [{
        "AccntId": "B7744F2949A203AACB31C82743EA",
        "DeleteFlag": false,
        "Amount": 15230,
        "TypeName": "debit",
        "Date": "06/06/2026",
        "Description": "Create API Test",
        "Payee": "MT Inc.",
        "ChequeNumber": "5698-45687",
        "ReferenceNumber": "5698-45687"
    }]
}
```

`ImportTypeId`: `CSV = 10`, `OFX = 20`, `QIF = 30`, `AUTO = 999`.
</details>

**Header fields:** `ImportTypeId`, `StartDate`, `EndDate`, `EndBalance`.
**Line fields:** `AccntId`, `DeleteFlag`, `Amount`, `TypeName` (debit/credit), `Date`, `Description`, `Payee`, `ChequeNumber`, `ReferenceNumber`.

---

### 5. General Ledger Transactions API

**`GET Xerp/accounting/getgltransactions`**

Retrieve GL transaction records for financial reporting, auditing, and account reconciliation.

| Parameter | Description |
|---|---|
| `start_date` / `end_date` | Date range for GL transactions. |
| `txn_numbers` | Transaction number(s) filter. |
| `ref_numbers` | Reference number(s) filter. |
| `entity_accnt_ids` | Entity account identifiers. |
| `account_ids` | GL account identifiers. |
| `account_gl_codes` | GL account codes. |
| `page_size` | Records per page (**capped at 100**). |
| `page_number` | Page number for paginated results. |
| `exclude_closing_entries` | Excludes accounting closing entries when enabled. |

> Response `Data = { transactionList, deletedTxnNumbers }`. There is **no chart-of-accounts endpoint** in the `/xerp/` API — `xoro_api.get_chart_of_accounts()` derives `{GLCode: {id, name, type}}` from GL rows. `F_AccountingId` on a GL row is the `AccntId` the bank-statement upload needs. (For a cleaner COA, use the `.asmx` `AccountingWebMethods.getAllAccountsForApi` below.)

---

## Additional `/xerp/` endpoints (discovered, not in the doc)

Probed read-only 2026-06-23 via the `/Xerp` bypass. Route convention `Xerp/{controller}/{action}`: a real route returns the Xoro envelope; an unknown action under a real controller `400`s ("The request is invalid"); a missing controller `404`s; a POST-only route `405`s on GET.

**Reads:**
- `Xerp/bill/getbill` — vendor bills (used for 4009 FedEx matching)
- `Xerp/salesorder/getsalesorder`
- `Xerp/purchaseorder/getpurchaseorder`
- `Xerp/product/getproduct`
- `Xerp/refund/getrefund`
- `Xerp/currency/getcurrency`

**Writes** (405-on-GET, i.e. POST-only):
- `Xerp/bill/import`
- `Xerp/bankdeposit/import`

**Not found by name-guessing:** a journal-entry import, or a bank-line reconcile/match endpoint. Those live in the `.asmx` internal API instead.

---

## The real internal API — ASP.NET `.asmx` WebMethods

The Xoro UI does **not** use `/xerp/` for reconcile and most write flows. It calls **ScriptService WebMethods**:

```
POST /WebServices/{Service}.asmx/{method}
Content-Type: application/json
Cookie: <session cookie>
Body: JSON of named params, e.g. { "jeObjJson": ... }
Response: { "d": <result or json-string> }
```

Authenticated by the `.ASPXAUTH`-style **session cookie** — the *legitimate* auth path, not the case bypass.

**These services self-document:** `GET /WebServices/{Svc}.asmx` lists methods; `?op={Method}` shows the signature. Many expose `get…ObjSchema` getters that return the exact payload shape with no write — use them to learn schemas safely.

> Responses are **not uniform**: some return a Xoro envelope (`getBankReconcileAccountList` → accounts under `.Data`), others return the bare object (`getJournalEntryObjSchema`). `WebMethodClient.call()` returns the raw `d`; each caller handles its own shape.

### Key services & methods

**`JournalEntryWebMethods.asmx`** — the reconcile workhorse
- **`saveJournalEntry(jeObjJson)`**, `updateJournalEntry`, `voidJournalEntry`
- `getJournalEntryObjSchema` (returns the JE shape), `getDataForJE`, `getJournalEntryObjInfoFromId`
- `jeObjJson` shape:
  ```
  {
    IsBeingAutoCreated,
    SimulatationFlag,   // (sic) likely a dry-run/validate-without-commit mode — use to test safely
    JournalEntryHeaderObj { TxnDate, Memo, RefNumber, CurrencyId,
                            TotalDebitAmount, TotalCreditAmount,
                            AutoCreateBankReconcileRule, … },
    JournalEntryLineArr [ { AccountId, DebitAmount, CreditAmount, Memo,
                            GLCode, EntityAccountId, LineNumber, … } ]
  }
  ```
  This one POST replaces the entire browser click-flow in `reconcile.py`.

**`BankReconcileWebMethods.asmx`**
- `getBankReconcileAccountList` (returns 13 accounts on this tenant)
- `getBankReconciledTransactions(size, number, bankRecId, sorder, sname, strExpressions)`
- `addBankRecHeader(brHeaderObj)`, `finishBankRec`
- `getLastReconcileHeaderDetailsFromAccountId`, `getReconcileStatsFromBankRecId`, `updateBankRecEndingBalAndDate`
- Native rules engine: `createBankRecRuleObj`, `UpdateBankRecRule`, `voidReconcileRule` → `reconciliation_rules.json` could become native Xoro rules.

**`BillWebMethods.asmx`** — `createNewBill`, `createNewItemReceipt`, `createBillFromItemReceiptLines`, `updateBill` (4009 FedEx case).

**`CreditMemoWebMethods.asmx`** — `createCreditMemo`, `createCreditMemoFromInvoice` (refunds).

**`InvoiceWebMethods.asmx`** — `createInvoice` / `updateInvoice` (~50 methods).

**`AccountingWebMethods.asmx`** — chart of accounts, incl. `getAllAccountsForApi` (cleaner than GL-derived COA).

Also present: `CustomerWebMethods`, `VendorWebMethods`, `PaymentWebMethods`, `Common.asmx`.

**Implication:** the whole bookkeeping workflow can run browser-free via `.asmx` + session cookie.

---

## Bank statement upload — the working path

The documented REST `createstatement` is not deployed here (§4). The real upload is on **`ConnectBankWebMethods.asmx`** — discovered 2026-07-19 by capturing the live `UploadBankStatement.aspx` UI flow (page routes through a client-side router at `/Accounting/BankStatement/UploadBankStatement.aspx`; bare-filename URLs 404). Wrapped in `xoro_webmethods.py` (`build_bank_statement`, `WebMethodClient.create_bank_statement` / `get_bank_statement_accounts` / `get_last_bank_transaction`).

The UI does **three** calls; you only need the last for code:

1. **Parse file** *(read-only, optional — skip it and build the payload yourself)* — multipart `POST /Handlers/BankStatementUpload/BankStatementUpload.ashx` (FormData: file + `StmtAccountId`=FAccountId + `utilityId`; `impExpEntityId=94`, `typeId=10`=CSV) → returns `{ Data: { BankStatementHeader, BankStatementLineArr } }`, i.e. the parsed statement.
2. **Commit (the write)** — `POST /WebServices/ConnectBankWebMethods.asmx/uploadBankStatementManual`, JSON body:
   ```json
   { "bankStmtData": "<JSON-STRINGIFIED object below>" }
   ```
   > ⚠️ **`bankStmtData` is a double-encoded JSON *string*, not a nested object.**

   The object:
   ```json
   {
     "BankStatementHeader": { "ImportTypeId": 10, "StartDate": null, "EndDate": null, "StartBalance": null, "EndBalance": null },
     "BankStatementLineArr": [
       { "AccntId": "<FAccountId>", "Date": "6/6/2026", "Amount": "-1", "Payee": "...", "Description": "...",
         "Reference": "...", "ChequeNumber": null, "Seq": 1,
         "AllowDuplicate": null, "HasError": false, "IsDuplicate": false, "ErrorText": null }
     ]
   }
   ```
   - `ImportTypeId`: CSV=10, OFX=20, QIF=30, AUTO=999.
   - `Amount` is a **string**; negative = debit/withdrawal, positive = deposit. Xoro trims trailing zeros (`-1.00` → `-1`).
   - `Date` = `M/D/YYYY`. `AccntId` = the bank account's **FAccountId**.
   - Success = envelope `Result == true`.

**Supporting reads** (same service): `getDataForBankStmtUpload` (bank account list + home currency), `getLastBankTransactionFromFAccountId(fAccountId)` (dedup cursor), `generateTemplateBankStmtCSVFile` (template = repo `BankStatementImport.csv`, columns `**Date,**Amount,Payee,Description,Reference,ChequeNumber`; `**` = mandatory).

**Amazon 3P FAccountIds:** base `72FEF96E2FEF86436FAEF3A8344BB`, AUD `B7D04105A81ACC54A8CEEADB483E`, CAD `B7D04105A81AFF597271FCF241F9`, EUR `B7D04105A81AB63C5C969DC14236`, GBP `B7D04105A81AAD1B201F4593405F`, USD `B7D04105A81A6B4359BC7D114B47`.

**Credit cards** — a raw card export lists charges as **positive** and payments as **negative**; the import wants the opposite, so multiply every amount **by −1** (`load_activity_csv(..., credit_card=True)`). The **same flip applies to `EndBalance`** (New Balance $489.71 → `-489.71`). Xoro does *not* auto-flip, so the ×−1 is required, not redundant.

**`StartBalance` auto-populates — don't set it.** The opening balance carries forward from the prior statement's `EndBalance`. Confirm/inspect via `BankReconcileWebMethods.getLastReconcileHeaderDetailsFromAccountId(bnkrcAccntId=<FAccountingId>)` → `beginningBal`, `lastStatementDate`, plus `balanceMatch` / `balDifference` (a `True` / `0.0` after upload is the end-to-end correctness check — wrong signs give a nonzero difference).

> **Proven live 2026-07-19:** first real commit created `BankStatementId 1970` on the Amex Delta 1003 credit card (8 lines from `activity (6).csv`, `EndDate 06/02/2026`, `EndBalance -489.71`); reconcile came back `balanceMatch: true`, `balDifference: 0.0`. **Single-session account:** a script-side `xoro_login.login()` and a browser login evict each other — don't run both at once.

Example:
```python
from xoro_webmethods import WebMethodClient
c = WebMethodClient.from_config()
c.create_bank_statement(
    "B7D04105A81A6B4359BC7D114B47",  # Amazon 3P USD
    [{"date": "2026-06-06", "amount": -401.90, "payee": "FACEBK", "description": "Ads", "reference": "R1"}],
    end_balance="45000",
)
```

---

## Authentication

Xoro does **not** use `.ASPXAUTH`. Its session cookies are **`__Auth` + `ASP.NET_SessionId`** (both HttpOnly).

- **Cookie storage:** the full cookie header lives in `XORO_COOKIE` in `.env` (gitignored).
- **Verified behavior:** an `.asmx` POST without the cookie → `302 /Authentication/login.aspx`; with `Cookie: <XORO_COOKIE>` → `200` JSON `{"d": …}`.
- **Programmatic login (`xoro_login.py`):** `login()` performs the ASP.NET WebForms postback to `/Authentication/login.aspx` — GETs `__VIEWSTATE` / `__VIEWSTATEGENERATOR` / `__EVENTVALIDATION`, POSTs `loginCtrl$UserName` / `$Password` / `$LoginButton`, harvests `__Auth` + `ASP.NET_SessionId`, writes `XORO_COOKIE` to `.env`, and returns the header. Creds are `XORO_USERNAME` / `XORO_PASSWORD` in `.env`. **No 2FA** is enforced on the `alau` account (the OTP controls render but aren't checked).
- **Self-healing (`xoro_webmethods.py`):** `WebMethodClient.call(service, method, **params)` POSTs to `/WebServices/{svc}.asmx/{method}`, unwraps `{"d": …}`, and on a login-bounce (3xx → login.aspx) auto-calls `xoro_login.login()` and retries once. `from_config()` reads `XORO_COOKIE`. Verified live incl. a forced self-heal (corrupted cookie → auto-relogin → recovered).

Client resilience knobs (`xoro_api.py`): `path_prefix` (currently `Xerp`; flip to `xerp` if the bypass is fixed) and `session_cookie` (`XORO_SESSION_COOKIE` in `.env`) as the long-term auth if the bypass ever closes.

---

## Stage-by-stage status

- **Stage 3 — Bank statement upload:** Clean API **found** — `ConnectBankWebMethods.uploadBankStatementManual` (see [Bank statement upload — the working path](#bank-statement-upload--the-working-path)). The documented REST `createstatement` is not deployed and `bankdeposit/*` are bank *deposits*, not statements — ignore both. The browser-driven `upload_bank_statement.py` is now superseded for statement creation.
- **Stage 4 — Reconcile:** Also a clean API — `JournalEntryWebMethods.saveJournalEntry`.

## Roadmap (agreed, not yet built)

1. ~~Replace browser `upload_bank_statement.py` with a statement API~~ **Done** — `ConnectBankWebMethods.uploadBankStatementManual` via `create_bank_statement` in `xoro_webmethods.py`.
2. Reconcile dedup/verify via GL `ReconciledFlag`.
3. Payout ↔ invoice matching via `getinvoice` ref join.
4. Refund → credit-memo via `creditmemo/import`.

---

## Files

| File | Purpose |
|---|---|
| `xoro_api.py` | `/xerp/` REST client (stdlib-only) + `get_chart_of_accounts()`, `build_statement()`. |
| `xoro_webmethods.py` | `.asmx` WebMethod client with self-heal (`WebMethodClient.call`). |
| `xoro_login.py` | Programmatic ASP.NET WebForms login → refreshes `XORO_COOKIE`. |
| `xoro_config.json` | Base URL / path-prefix config. |
| `test_xoro_api.py`, `test_xoro_login.py`, `test_xoro_webmethods.py` | Tests (34 total). |
| `upload_bank_statement.py` | Browser-driven bank-statement upload (Stage 3 — no clean API). |
| `reconcile.py`, `reconciliation_rules.json` | Reconciliation logic / rules. |
