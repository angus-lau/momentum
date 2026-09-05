# Momentum — Progress Tracker

Monthly accounting automation for St. Moritz Watch (Xoro ERP).

## Overall Flow

| Step | Task | Script | Status |
|------|------|--------|--------|
| 1 | Download CC/bank activity files to OneDrive folders | Manual | — |
| 2 | Convert activity files to Xero import format | `convert_activity.py --all` | Done |
| 3 | Upload bank statements to Xoro | `upload_bank_statement.py --all` | Done |
| 4 | Reconcile accounts in Xoro | — | Not started (API-driven) |
| 5 | Download Stripe payout details | `Stripe Payouts/payouts.py` | Done |

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

### Reconciliation — Not started (API-driven)
- The old `reconcile.py` (browser-click automation via `agent-browser` + Claude CLI GL classification) was removed 2026-08-22 — it had no API-driven consumers and predated the confirmed working `JournalEntryWebMethods.saveJournalEntry` endpoint (see `XORO_API.md`).
- `reconciliation_rules.json` (learned payee → GL code mappings) and `GL_ACCOUNTS.md` (valid GL code reference) were kept — reusable by a future API-driven rebuild.
- Blocker before rebuilding: capture a real `saveJournalEntry` POST from an actual UI reconcile to confirm which field links the JE back to the specific bank-statement row (see `XORO_API.md` → "The real internal API").

### Stripe Download (`Stripe Payouts/payouts.py`) — Done
- Prints Stripe payouts + underlying balance transactions (charges, refunds, fees) for any date/range; terminal-only, no Xoro write-side yet. Superseded the root `stripe_download.py`, removed 2026-08-22 (no code depended on it).

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
