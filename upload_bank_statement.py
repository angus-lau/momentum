"""
Upload bank statements to Xoro and reconcile.
Uses agent-browser (Chrome CDP) for browser automation.
Chrome must be running with --remote-debugging-port=9222.

Usage:
  python upload_bank_statement.py --all [month]
  python upload_bank_statement.py <bank> [csv_file | month]
"""

import base64
import json
import os
import subprocess
import sys
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "upload_configs.json")
BALANCES_PATH = os.path.join(SCRIPT_DIR, "balances.json")

with open(CONFIG_PATH, "r") as f:
    CONFIG = json.load(f)

ACCOUNTS = CONFIG["accounts"]


def load_balances():
    if os.path.exists(BALANCES_PATH):
        with open(BALANCES_PATH, "r") as f:
            return json.load(f)
    return {}


def get_month_str(month=None):
    """Return 'YY MM' string. Defaults to previous month."""
    if month:
        return month
    first_of_this_month = date.today().replace(day=1)
    prev_month = first_of_this_month - timedelta(days=1)
    return prev_month.strftime("%y %m")


def get_csv_path(bank: str, month: str = None):
    """Build the CSV path: base_path/YE YYYY/folder/YY MM/BankStatementImport.csv"""
    cfg = ACCOUNTS[bank]
    if "folder" not in cfg:
        return None
    month = get_month_str(month)
    year = 2000 + int(month[:2])
    return os.path.join(
        CONFIG["base_path"], f"YE {year}", cfg["folder"], month, CONFIG["filename"]
    )


# --- agent-browser helpers ---


def ab(*args):
    """Run an agent-browser command and return stdout."""
    result = subprocess.run(
        ["agent-browser"] + list(args), capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"agent-browser {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout.strip()


def ab_eval(js):
    """Run JavaScript in the browser via agent-browser eval --stdin."""
    result = subprocess.run(
        ["agent-browser", "eval", "--stdin"],
        input=js, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"eval error: {result.stderr.strip()}")
    return result.stdout.strip()


def js_fill(selector, value):
    """Fill an input field by CSS selector."""
    value = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    ab_eval(f'''
const el = document.querySelector("{selector}");
el.focus();
el.value = "";
el.value = "{value}";
el.dispatchEvent(new Event("input", {{bubbles: true}}));
el.dispatchEvent(new Event("change", {{bubbles: true}}));
''')


def js_click(selector):
    """Click an element by CSS selector."""
    ab_eval(f'document.querySelector("{selector}").click()')


def upload_file(selector, file_path):
    """Upload a file to an input element using the DataTransfer API."""
    with open(file_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    filename = os.path.basename(file_path)
    ab_eval(f'''
const b64 = "{b64}";
const bin = atob(b64);
const bytes = new Uint8Array(bin.length);
for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
const file = new File([bytes], "{filename}", {{type: "text/csv"}});
const dt = new DataTransfer();
dt.items.add(file);
const input = document.querySelector("{selector}");
input.files = dt.files;
input.dispatchEvent(new Event("change", {{bubbles: true}}));
''')


# --- Core workflow ---


def ensure_on_upload_page():
    """Navigate to Xoro Upload Bank Statement page, logging in if needed."""
    url = ab("get", "url")
    if "UploadBankStatement.aspx" in url:
        return

    ab("open", "https://momentum.xoro.one")
    ab("wait", "--load", "networkidle")

    url = ab("get", "url")
    if "Login" in url or "login" in url:
        js_fill("#UserName", os.getenv("XORO_USERNAME"))
        js_fill("#Password", os.getenv("XORO_PASSWORD"))
        js_click("#LoginButton")
        ab("wait", "--url", "**/Dashboards/BusinessDashboard.aspx")

    ab("find", "text", "Upload Bank Statement", "click")
    ab("wait", "--url", "**/UploadBankStatement.aspx")


def upload_one(bank: str, csv_path: str):
    """Upload a single bank statement (assumes already on Upload Bank Statement page)."""
    cfg = ACCOUNTS[bank]

    print(f"\n[{bank}] Uploading: {csv_path}")
    if not os.path.exists(csv_path):
        print(f"  Skipping — file not found: {csv_path}")
        return False

    # Select bank from dropdown
    js_click("button[data-id='dd_bnkStmt_BankAccount']")
    ab("wait", "500")
    js_fill(".bs-searchbox input", cfg["search"])
    ab("wait", "500")
    ab("find", "text", cfg["option"], "click")
    ab("wait", "1000")

    # Upload CSV
    upload_file("#bankStmtFileUpload", csv_path)
    ab("wait", "1000")

    # Click Verify & Upload
    js_click("#btn_verifyFileContents")

    # Wait for success alert
    ab("wait", ".alert-success")
    alert_text = ab_eval('document.querySelector(".alert-success").textContent')
    print(f"  Verified: {alert_text.strip()}")

    # Click Upload Statement
    js_click("#bnkStmtFileUploadBtn")

    # Click OK on confirmation dialog
    ab("wait", ".swal2-confirm")
    js_click(".swal2-confirm")
    ab("wait", "2000")

    print(f"  Upload complete.")
    return True


def reconcile_one(bank: str, month: str):
    """Reconcile a single bank account (assumes on Bank Reconciliation Centre)."""
    cfg = ACCOUNTS[bank]
    print(f"\n[{bank}] Reconciling...")

    balances = load_balances()
    acct_bal = balances.get(month, {}).get(bank)
    if not acct_bal or acct_bal.get("balance") is None or not acct_bal.get("date"):
        print(f"  ERROR — missing balance/date in balances.json for {bank} ({month})")
        print(f"  Run convert_activity.py first, or add the balance manually")
        return False

    # Select account from dropdown
    js_click("button[data-id='dd_BnkRecAccount']")
    ab("wait", "500")
    js_fill(".bs-searchbox input", cfg["search"])
    ab("wait", "500")
    ab("find", "text", cfg["option"], "click")
    ab("wait", "1000")

    # Override window.open so reconciliation opens in same tab
    ab_eval("window.open = function(url) { window.location.href = url; };")

    # Click Reconcile Now
    ab("find", "text", "Reconcile Now", "click")
    ab("wait", "--load", "networkidle")

    # Fill ending balance
    js_fill("#txt_bankRec_EndBalnce", str(acct_bal["balance"]))

    # Fill ending date (set via JS to avoid datepicker popup)
    date_val = acct_bal["date"]
    ab_eval(f'''
const el = document.getElementById("txt_bankRec_EndDate");
el.value = "{date_val}";
el.dispatchEvent(new Event("change", {{bubbles: true}}));
''')

    print(f"  Filled balance: ${acct_bal['balance']:,.2f}, date: {date_val}")

    # Click Start Reconciling
    js_click("#btn_bankRec_start")
    ab("wait", "--load", "networkidle")
    ab("wait", "2000")

    # Auto-apply matched rules: click each green checkmark
    applied = 0
    while True:
        has_check = ab_eval(
            'document.querySelector("i.fa-check-circle-o") ? "yes" : "no"'
        )
        if has_check != "yes":
            break
        ab_eval('''
const check = document.querySelector("i.fa-check-circle-o");
const btn = check.closest("a") || check.closest("button") || check;
btn.click();
''')
        ab("wait", "--load", "networkidle")
        ab("wait", "500")
        applied += 1

    print(f"  [{bank}] Reconciliation started. Applied {applied} matched rules.")

    # Navigate back to Bank Reconciliation Centre for next account
    ab("open",
       "https://momentum.xoro.one/Accounting/BankReconcile/BankReconciliationCentre.aspx")
    ab("wait", "--load", "networkidle")
    return True


def run(bank: str, csv_path: str, month: str = None):
    """Upload a single bank statement and reconcile."""
    month = get_month_str(month)
    ensure_on_upload_page()
    upload_one(bank, csv_path)

    # Navigate to Bank Reconciliation Centre
    js_click("#BankReconciliationCentre")
    ab("wait", "--url", "**/BankReconciliationCentre.aspx")
    reconcile_one(bank, month)


def run_all(month: str = None):
    """Upload all configured bank statements in a single session."""
    month = get_month_str(month)
    print(f"Uploading all accounts for {month}...")

    ensure_on_upload_page()

    success = 0
    skipped = 0
    for bank, cfg in ACCOUNTS.items():
        if "folder" not in cfg or cfg.get("skip"):
            continue
        csv_path = get_csv_path(bank, month)
        if upload_one(bank, csv_path):
            success += 1
        else:
            skipped += 1

    print(f"\nDone: {success} uploaded, {skipped} skipped")

    # Navigate to Bank Reconciliation Centre
    js_click("#BankReconciliationCentre")
    ab("wait", "--url", "**/BankReconciliationCentre.aspx")
    print("\nReconciling accounts...")

    for bank, cfg in ACCOUNTS.items():
        if "folder" not in cfg or cfg.get("skip"):
            continue
        reconcile_one(bank, month)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} --all [month]")
        print(f"       python {sys.argv[0]} <bank> [csv_file | month]")
        print(f"  month format: 'YY MM' (e.g. '26 01')")
        print(f"Banks: {', '.join(ACCOUNTS.keys())}")
        sys.exit(1)

    arg1 = sys.argv[1]

    if arg1 == "--all":
        month = sys.argv[2] if len(sys.argv) > 2 else None
        run_all(month)
    else:
        bank_name = arg1.lower()
        if bank_name not in ACCOUNTS:
            print(f"Unknown bank: {bank_name}")
            print(f"Supported: {', '.join(ACCOUNTS.keys())}")
            sys.exit(1)

        month = None
        if len(sys.argv) > 2:
            arg = sys.argv[2]
            if len(arg) == 5 and arg[2] == " " and arg[:2].isdigit() and arg[3:].isdigit():
                month = arg
                csv_file = get_csv_path(bank_name, arg)
            else:
                csv_file = arg
        else:
            csv_file = get_csv_path(bank_name)

        if csv_file is None:
            print(f"No folder configured for {bank_name} — provide an explicit CSV path")
            sys.exit(1)

        run(bank_name, csv_file, month)
