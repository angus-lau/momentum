import json
import os
import sys
from datetime import date, timedelta
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

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
        CONFIG["base_path"],
        f"YE {year}",
        cfg["folder"],
        month,
        CONFIG["filename"],
    )


def login(page):
    """Navigate to Xoro and sign in."""
    page.goto("https://momentum.xoro.one")
    page.wait_for_load_state("networkidle")
    page.fill("#UserName", os.getenv("XORO_USERNAME"))
    page.fill("#Password", os.getenv("XORO_PASSWORD"))
    page.click("#LoginButton")
    page.wait_for_url("**/Dashboards/BusinessDashboard.aspx", timeout=30000)

    # Navigate to Upload Bank Statement page
    page.click("text=Upload Bank Statement")
    page.wait_for_url("**/Accounting/BankStatement/UploadBankStatement.aspx")


def upload_one(page, bank: str, csv_path: str):
    """Upload a single bank statement (assumes already on Upload Bank Statement page)."""
    cfg = ACCOUNTS[bank]

    print(f"\n[{bank}] Uploading: {csv_path}")
    if not os.path.exists(csv_path):
        print(f"  Skipping — file not found: {csv_path}")
        return False

    # Select bank from dropdown
    page.click("button[data-id='dd_bnkStmt_BankAccount']")
    page.wait_for_timeout(500)
    page.fill(".bs-searchbox input", cfg["search"])
    page.wait_for_timeout(500)
    page.click(f"text={cfg['option']}")
    page.wait_for_timeout(1000)

    # Upload the CSV file
    page.set_input_files("#bankStmtFileUpload", csv_path)
    page.wait_for_timeout(1000)

    # Click Verify & Upload
    page.click("#btn_verifyFileContents")

    # Wait for success alert
    page.wait_for_selector(".alert-success", timeout=30000)
    alert_text = page.text_content(".alert-success")
    print(f"  Verified: {alert_text.strip()}")

    # Click Upload Statement
    page.click("#bnkStmtFileUploadBtn")

    # Click OK on confirmation dialog
    page.wait_for_selector(".swal2-confirm", timeout=30000)
    page.click(".swal2-confirm")
    page.wait_for_timeout(2000)

    print(f"  Upload complete.")
    return True


def reconcile_one(page, bank: str, month: str):
    """Reconcile a single bank account (assumes already on Bank Reconciliation Centre)."""
    cfg = ACCOUNTS[bank]
    print(f"\n[{bank}] Reconciling...")

    # Look up balance from balances.json
    balances = load_balances()
    acct_bal = balances.get(month, {}).get(bank)
    if not acct_bal or acct_bal.get("balance") is None or not acct_bal.get("date"):
        print(f"  ERROR — missing balance/date in balances.json for {bank} ({month})")
        print(f"  Run convert_activity.py first, or add the balance manually to balances.json")
        return False

    # Select account from dropdown
    page.click("button[data-id='dd_BnkRecAccount']")
    page.wait_for_timeout(500)
    page.fill(".bs-searchbox input", cfg["search"])
    page.wait_for_timeout(500)
    page.click(f"text={cfg['option']}")
    page.wait_for_timeout(1000)

    # Click Reconcile Now (opens new window)
    with page.expect_popup() as popup_info:
        page.click("button:has-text('Reconcile Now')")
    reconcile_page = popup_info.value
    reconcile_page.wait_for_load_state("networkidle")

    # Fill in ending balance
    reconcile_page.fill("#txt_bankRec_EndBalnce", str(acct_bal["balance"]))

    # Fill in ending date (set via JS to avoid datepicker popup)
    reconcile_page.evaluate(
        "(date) => {"
        "  const el = document.getElementById('txt_bankRec_EndDate');"
        "  el.value = date;"
        "  el.dispatchEvent(new Event('change', {bubbles: true}));"
        "}",
        acct_bal["date"],
    )

    print(f"  Filled balance: ${acct_bal['balance']:,.2f}, date: {acct_bal['date']}")

    # Click Start Reconciling
    reconcile_page.click("#btn_bankRec_start")
    reconcile_page.wait_for_load_state("networkidle")
    reconcile_page.wait_for_timeout(2000)

    # Auto-apply matched rules: click each green checkmark button
    total = len(reconcile_page.query_selector_all("i.fa-check-circle-o"))
    applied = 0
    while True:
        check = reconcile_page.query_selector("i.fa-check-circle-o")
        if not check:
            break
        btn = check.evaluate_handle("el => el.closest('a') || el.closest('button') || el")
        btn.click()
        # Wait for the row to be processed (checkmark disappears or count decreases)
        reconcile_page.wait_for_load_state("networkidle")
        reconcile_page.wait_for_timeout(500)
        applied += 1

    print(f"  [{bank}] Reconciliation started. Applied {applied} matched rules.")
    return True


def run(bank: str, csv_path: str, month: str = None):
    """Upload a single bank statement and reconcile."""
    month = get_month_str(month)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_context().new_page()
        login(page)
        upload_one(page, bank, csv_path)

        # Navigate to Bank Reconciliation Centre
        page.click("#BankReconciliationCentre")
        page.wait_for_url("**/Accounting/BankReconcile/BankReconciliationCentre.aspx")
        reconcile_one(page, bank, month)

        input("Press Enter to close the browser...")
        browser.close()


def run_all(month: str = None):
    """Upload all configured bank statements in a single browser session."""
    month = get_month_str(month)
    print(f"Uploading all accounts for {month}...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_context().new_page()
        login(page)

        success = 0
        skipped = 0
        for bank, cfg in ACCOUNTS.items():
            if "folder" not in cfg or cfg.get("skip"):
                continue
            csv_path = get_csv_path(bank, month)
            if upload_one(page, bank, csv_path):
                success += 1
            else:
                skipped += 1

        print(f"\nDone: {success} uploaded, {skipped} skipped")

        # Navigate to Bank Reconciliation Centre
        page.click("#BankReconciliationCentre")
        page.wait_for_url("**/Accounting/BankReconcile/BankReconciliationCentre.aspx")
        print("\nReconciling accounts...")

        for bank, cfg in ACCOUNTS.items():
            if "folder" not in cfg or cfg.get("skip"):
                continue
            reconcile_one(page, bank, month)

        input("Press Enter to close the browser...")
        browser.close()


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
