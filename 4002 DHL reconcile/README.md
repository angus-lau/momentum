# DHL Invoice Reconciler

Parses DHL invoice PDFs for the **Amex Sofia 4002** card into the monthly `DHL Reconcile.xlsx` and a bank-statement import CSV.

## Run it

```sh
python3 dhl_reconcile.py "/Users/angus/Library/CloudStorage/OneDrive-St.MoritzWatch/Accounting Docs/CC Expenses/YE 2026/Amex Sofia 4002/26 05"
```

Replace `26 05` with whatever the current month folder is named.

The script auto-discovers all `*.pdf` files in that folder.

## What it writes (in the same folder as the PDFs)

| File | Purpose |
|---|---|
| `DHL Reconcile.xlsx` | Per-invoice GL allocation, matches the template column structure (overwrites; existing file is auto-backed-up to `DHL Reconcile.bak-<timestamp>.xlsx`) |
| `dhl_reconcile_lines.csv` | Same data as the xlsx, in case you'd rather paste into Excel manually |
| `dhl_bank_statement.csv` | Four-line summary matching `DHLBankStatementImport.csv` format for bank import |
| `dhl_review.csv` | Only written when at least one row failed a sanity check |

## First-time setup

```sh
python3 -m pip install --user -r requirements.txt
```

Only needed once per machine.

## How it works

Three DHL PDF formats are recognized:

1. **`YVRIR*` ("INBOUND INVOICE")** — Outbound shipments billed as `DUTIES & TAXES`.
   - `IMPORT EXPORT TAXES` is routed per-shipment by destination country:
     - EU country → **EU VAT** (2250)
     - GB → **UK VAT** (2240)
     - anywhere else → **Duties & Brokerage** (7020)
   - `IMPORT EXPORT DUTIES`, `DUTY TAX PAID`, `CLEARANCE PROCESSING`, `EXCISE TAX`, `OTHER GOVT DEPT FEE` → **Duties & Brokerage**

2. **`YVRR*` ("OUTBOUND INVOICE")** — `EXPRESS WORLDWIDE NONDOC` outbound shipments.
   - Entire invoice (Standard Shipping Charge + Fuel + GoGreen + Remote / Emergency / anything) → **Customer Delivery Fees** (7060)
   - If `DUTIES TAXES PAID` line ever appears → **Duties & Brokerage**

3. **`E10*` ("CUSTOMS DUTY INVOICE")** — Imports to Canada (YVR / ST MORITZ WATCH CORP).
   - All TAX amounts → **GST** (Tax Adjustment)
   - All Excl-TAX amounts (DHL fees, duties, clearance) → **Freight Amounts** (7070)
   - Fills column K (Origin code, e.g. HKG / DEL) and column L (Invoice #)

## Column layout (matches the xlsx template exactly)

| Col | Field | GL code |
|---|---|---|
| A | Amount (invoice total) | — |
| B | Check (live formula: `=A-SUM(C:I)`; 0 = balanced) | — |
| C | Freight Amounts | 7070 |
| D | Customer Delivery Fees | 7060 |
| E | Duties & Brokerage (Drawbacks) | 7025 |
| F | Duties & Brokerage | 7020 |
| G | GST | Tax Adjustment |
| H | EU VAT | 2250 |
| I | UK VAT | 2240 |
| K | Origin (for inbound rows) | — |
| L | Invoice number | — |
| M | Notes (warnings) | — |

## EU country list

The 27 EU member states (Norway, Switzerland, Iceland, Liechtenstein are NOT in this list and route to D&B):

`AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE`

If the EU expands / contracts, update `EU_COUNTRIES` at the top of `dhl_reconcile.py`.

## Sanity checks

For every invoice the script verifies that the sum of allocations equals the invoice total. Mismatches > $0.02 are written to `dhl_review.csv` and shown in the Notes column. The Check formula in column B also lets you spot any drift visually.

## Re-running next month

1. Drop the new month's PDFs into a folder like `Amex Sofia 4002/26 05/` (each file's name prefixed with the CC statement amount is fine — the script ignores the prefix).
2. Run the command at the top of this file with the new folder path.
3. Open the generated `DHL Reconcile.xlsx`, scan column B for any non-zero value, and import `dhl_bank_statement.csv`.

## Adding a new dispatch rule

If DHL adds a new charge type or you want a different routing:

- For per-charge routing (YVRIR / E10), edit the `allocate()` function in `dhl_reconcile.py`.
- For new PDF formats, add a `parse_<format>()` function and a branch in `detect_format()`.
- For a new GL column, add it to the `headers` / `gl_codes` lists and the `col_map` dict.
