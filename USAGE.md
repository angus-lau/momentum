# Momentum Scripts

## convert_activity.py
Converts bank/credit card activity files into Xero import format.

```bash
# Convert ALL credit cards (previous month)
python convert_activity.py --all

# Convert ALL credit cards (specific month)
python convert_activity.py --all "26 01"

# Convert one card (previous month, auto-finds activity file)
python convert_activity.py boa

# Convert one card (specific month)
python convert_activity.py boa "26 01"

# Manual mode (explicit input/output files)
python convert_activity.py boa input.csv output.csv
```

## upload_bank_statement.py
Uploads converted CSV to Xoro via browser automation (Playwright).

```bash
# Upload using default path (previous month)
python upload_bank_statement.py boa

# Upload for a specific month
python upload_bank_statement.py boa "26 01"

# Upload with explicit file path
python upload_bank_statement.py boa /path/to/BankStatementImport.csv
```

## Stripe Payouts/payouts.py
Prints Stripe payouts and their underlying balance transactions (charges, refunds, fees) for any date or range. See `Stripe Payouts/README.md`.

```bash
python3 "Stripe Payouts/payouts.py" [start_date] [end_date]
```

## Setup
```bash
# Activate venv
source venv/bin/activate

# Install dependencies
pip install python-dotenv stripe playwright openpyxl xlrd requests
playwright install chromium
```
