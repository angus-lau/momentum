# Momentum — Progress Tracker

Monthly accounting automation for St. Moritz Watch (Xoro ERP).

## Overall Flow

| Step | Task | Script | Status |
|------|------|--------|--------|
| 1 | Download CC/bank activity files to OneDrive folders | Manual | — |
| 2 | Convert activity files to Xero import format | `convert_activity.py --all` | Done |
| 3 | Upload bank statements to Xoro | `upload_bank_statement.py --all` | Done |
| 4 | Open reconciliation in Xoro (rec header + ending balance) | `xoro_webmethods.WebMethodClient.start_reconciliation` | Done |
| 5 | Match lines / GL-code in Xoro rec screen | Manual | Manual by design |
| 6 | Stripe payouts → Xoro bank deposits | `stripe_deposits.py` | Done |

## Detailed Status

### Convert Activity (`convert_activity.py`) — Done
- Reads CSV/XLS/XLSX activity files from OneDrive folders
- Outputs `BankStatementImport.csv` in Xero format
- Extracts ending balance + closing date from PDF statements
- Saves balances to `balances.json` for reconciliation
- Supports: BOA, TD, CIBC, Amex (4009, Blue, Delta, Sofia), BMO, Umpqua, Wise
- DHL filtering for Amex Sofia (separates DHL transactions into reconcile spreadsheet)

### Upload Bank Statements (`upload_bank_statement.py`) — Done
- Logs into Xoro (skips if already authenticated)
- Selects bank account from dropdown
- Uploads CSV file
- Clicks Verify & Upload, confirms dialog
- Loops through all configured accounts with `--all`

### Reconciliation — Done up to opening the rec; line matching is manual by design
- **Automated:** `statement_pipeline.py` commits the converted statement via API, then `WebMethodClient.start_reconciliation` (`BankReconcileWebMethods.addBankRecHeader`) opens the reconciliation with the PDF's ending balance and statement date.
- **Manual (decided 2026-09-21):** matching statement lines to existing Xoro transactions and GL-coding the rest happens in the Xoro rec screen. GL coding is a judgment call (new vendors, splits, reimbursements), so it isn't automated on purpose.
- If this ever gets painful, the next increment is "auto-match the obvious lines, hand back a short list needing a decision" — not full automation. `JournalEntryWebMethods.saveJournalEntry` is confirmed working (see `XORO_API.md`); the open question for a rebuild is which field links a JE back to its bank-statement row — capture a live UI reconcile before guessing.
- `reconciliation_rules.json` (learned payee → GL code mappings) and `GL_ACCOUNTS.md` (valid GL code reference) are kept for that. The old browser-driven `reconcile.py` was removed 2026-08-22.

### Stripe Deposits (`stripe_deposits.py`) — Done
- Matches each payout's charges to Xoro undeposited Customer Deposits by `LineRefNo` and books the bank deposit (CAD → 1160, USD → 1170, fees 7455/7456) with a duplicate guard. Live since 2026-09-21. `Stripe Payouts/payouts.py` remains as a read-only listing.

## Browser Automation Setup

Migrated from Playwright to `agent-browser` (Chrome CDP):
- Chrome runs with `--remote-debugging-port=9222 --user-data-dir=~/.chrome-debug-profile`
- Launch with `chrome-debug` alias (defined in `~/.zshrc`)
- `agent-browser` auto-connects via `~/.agent-browser/config.json`
- Persistent Chrome session — stays logged into Xoro between runs

## Accounts

| Key | Account | Folder | Notes |
|-----|---------|--------|-------|
| boa | BOA Visa 9180 | BOA VISA USD 9180 | |
| td | TD Visa 0926 | TD VISA 0926 | Skipped |
| cibc | CIBC MC 2244 | CIBC MC 2244 | |
| amex_4009 | Amex Corporate 4009 | Amex Corporate 4009 | |
| amex_blue | Amex Blue 1005 | Amex USD Blue 1005 | |
| amex_delta | Amex Delta 1003 | Amex USD Delta 1003 | |
| amex_sofia | Amex Sofia 4002 | Amex Sofia 4002 | DHL filter |
| bmo_cad | BMO CAD 41547651 | — | No folder (manual) |
| bmo_usd | BMO USD 44569097 | — | No folder (manual) |
| umpqua | Umpqua 1729 | — | No folder (manual) |
| wise_cad | Wise CAD | — | No folder (manual) |
| wise_eur | Wise EUR | — | No folder (manual) |
| wise_gbp | Wise GBP | — | No folder (manual) |
