// Apply one vendor credit to several bills with a $0-cash bill payment on /Payment/Bills/BillPayment.aspx over CDP.
// Fill __PLACEHOLDERS__ with sed into a scratch copy; SEND=false captures paySelectedBills's payload only.
// __BILLS__ is a JSON map {"CA-B001913": 154.00, ...}. Reload the page before each run.
// Proven 2026-10-31: CA-V000083 → 4 Lowen bills, CA-V000084 → 2 Mistal bills.
(async () => {
  const SEND = __SEND__, bills = __BILLS__;
  const w = ms => new Promise(r => setTimeout(r, ms)), G = billPymntGlobal;
  $('#dd_billPymnt_ApAccount').selectpicker('val', '__AP_ID__').trigger('change'); await w(2500);
  $('#dd_billPymnt_vendor').selectpicker('val', '__VENDOR_ID__').trigger('change'); await w(5000);
  const out = [];
  for (let i = 0; i < G.BillPaymentViewArr.length; i++) {
    const b = G.BillPaymentViewArr[i]; if (!(b.BillNumber in bills)) continue;
    G.editRowIndex = i;
    populateVendorCreditDetailArr(i); await w(2500);
    if (typeof populatePrePaymentDetailArr == 'function') { populatePrePaymentDetailArr(i); await w(2000); }
    const vc = G.VendorCreditAvailableArr.find(v => /__CREDIT__/.test(JSON.stringify(v)));
    if (!vc) return {err: '__CREDIT__ not available', avail: G.VendorCreditAvailableArr.map(v => JSON.stringify(v).slice(0, 200))};
    G.VendorCreditAvailableArr.forEach(v => v.AmountToUse = 0);
    vc.AmountToUse = bills[b.BillNumber];
    billPymnt_ApplyCredit(); await w(800);
    out.push([b.BillNumber, vc.AmountToUse, 'credit rate ' + vc.ExchangeRate, 'bill rate ' + b.ExchangeRate]);
  }
  if (out.length !== Object.keys(bills).length) return {err: 'bills not all found', out};
  $('#tbl_billPymnt_searchResult').bootstrapTable('checkBy', {field: 'BillNumber', values: Object.keys(bills)}); await w(1000);
  const dateEl = $('input').filter((i, e) => /date/i.test(e.id) && $(e).closest('.form-group,div').text().match(/Payment Date/)).first();
  dateEl.val('__DATE__').trigger('change'); await w(500);
  let cap = null; const real = window.execAjx, conf = window.showConfirm, te = toastr.error, errs = [];
  window.execAjx = (p, cb) => { if (/paySelectedBills/.test(p.wUrl)) cap = {url: p.wUrl, name: p.name, data: JSON.parse(JSON.stringify(p.data))}; else real(p, cb); };
  window.showConfirm = (m, t, cb) => cb(true); toastr.error = m => errs.push(m);
  try { paySelectedBills(); } finally { window.execAjx = real; window.showConfirm = conf; toastr.error = te; }
  if (!cap) return {err: 'nothing captured', errs};
  const h = cap.data[0].billPaymentHeader;
  // payment method/account stay at the page default (Cash / Amazon 3P); harmless because cash paid is 0
  const summary = {out, date: h.TxnDate, total: h.TotalAmount, cashPaid: h.TotalAmountPaid, ap: h.AccountPayableName, vendor: h.VendorName};
  if (!SEND) return summary;
  if (h.TotalAmountPaid !== 0) return Object.assign(summary, {err: 'cash paid is not 0 — not sent'});
  const r = await new Promise(res => execAjx({name: cap.name, data: cap.data, wUrl: cap.url, context: $('<span>')}, res));
  return Object.assign(summary, {Result: r.Result, Message: r.Message});
})()
