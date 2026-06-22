# Shopify Consolidated & Service Center Payouts — Undeposited Payments Reconcile

A reusable **Claude web** prompt for ticking off payments in Xoro's **Undeposited
Payments** dialog when reconciling Shopify consolidated payouts and service-center
payouts.

You feed Claude a list of reference numbers; it searches each one in the open
dialog, ticks every matching row, and reports what it couldn't find. Because each
bank deposit is independent, you can run several at once in **parallel sessions**.

---

## When to use

- Reconciling a **Shopify consolidated payout** or a **service center payout** in Xoro.
- You have a list of payment **reference numbers** (often prefixed with `C`, e.g.
  `C35528`) that need to be selected in the **Undeposited Payments** dialog so they
  roll into the bank deposit.

## Prerequisites

- Claude on the web with **browser / computer use** enabled.
- The Xoro **Undeposited Payments** dialog open and visible on screen, with the
  filter bar showing the **"Ref Number"** field (the first text box after "Amount").

## Running multiple deposits in parallel

1. In Xoro, open a bank deposit and bring up its **Undeposited Payments** dialog.
2. For **each** deposit you're reconciling, open a **separate Claude web session**
   (new conversation / browser tab) pointed at that deposit's dialog.
3. Split your reference numbers by which deposit they belong to.
4. Paste the prompt below into each session, swapping in that deposit's batch of
   numbers.
5. Let them run concurrently, then collect each session's found / not-found summary.

> **Tip:** Keep each batch to one deposit. Don't point two sessions at the same
> dialog — they'll fight over the same filter field.

---

## The prompt (copy-paste)

Replace the number list at the bottom with the batch for this deposit.

```
I have a list of reference numbers I need you to search through one by one in the
Undeposited Payments dialog that is currently open on screen. Here's what to do:

- The search field is labeled "Ref Number" (the first text box after "Amount" in
  the filter bar).
- For each number: clear the field, type the number, press Enter, and wait for
  results to load.
- If a result appears in the table → tick the checkbox on that row (click the
  checkbox on the left side of the row).
- If multiple rows appear for the same number → tick all of them.
- If no result appears → add that number to a "not found" list.
- Move on to the next number and repeat.

Once all numbers are done, give me a summary of:
- How many were found and ticked.
- A list of any numbers that were not found.

Important: disregard the leading "C" in each number when searching (search the
digits only).

Numbers to search:
C35528 C35527 C35526 C35523 C35525 C35516 C35518 C35521 C35519 C35517 C35516 C35515
```

---

## Notes

- **Strip the leading `C`** — the dialog stores the references as digits only, so
  `C35528` is searched as `35528`. The prompt already tells Claude to do this.
- **Duplicates** in your list (e.g. `C35516` above appears twice) are fine — Claude
  searches each occurrence; if a row is already ticked it stays ticked.
- After each session finishes, reconcile its **found count + not-found list**
  against your source list so nothing is silently dropped.
- The **not-found** numbers usually mean the payment hasn't landed in Undeposited
  Payments yet (not imported / wrong date / already deposited) — investigate those
  manually.
