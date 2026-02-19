import stripe
import os
from dotenv import load_dotenv

load_dotenv()
stripe.api_key = os.getenv("STRIPE_LIVE_KEY")


def list_payout_transactions(limit=1):
    payouts = stripe.Payout.list(limit=limit)

    for payout in payouts['data']:
        print(f"Payout: ${payout['amount'] / 100:.2f} (arrival: {payout['arrival_date']})")
        print()

        txns = stripe.BalanceTransaction.list(payout=payout['id'])

        for txn in txns['data']:
            print(f"  {txn['type']:10} ${txn['amount'] / 100:>8.2f}  {txn['description']}")

        print()


if __name__ == "__main__":
    list_payout_transactions()
