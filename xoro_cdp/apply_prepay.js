// Apply vendor prepayments to bills with a $0-cash bill payment on /Payment/Bills/BillPayment.aspx (prepayment twin of
// apply_credit.js). __ALLOC__ = {"<bill#>": {"<CA-VP…>": amount, ...}, ...}. Reload the page before each run.
(async () => {
  const SEND = __SEND__, alloc = __ALLOC__;
  const w = ms => new Promise(r => setTimeout(r, ms)), G = billPymntGlobal;
  $('#dd_billPymnt_ApAccount').selectpicker('val', '__AP_ID__').trigger('change'); await w(2500);
  $('#dd_billPymnt_vendor').selectpicker('val', '__VENDOR_ID__').trigger('change'); await w(6000);
  const out = [];
  for (let i = 0; i < G.BillPaymentViewArr.length; i++) {
    const b = G.BillPaymentViewArr[i]; if (!(b.BillNumber in alloc)) continue;
    G.editRowIndex = i;
    populateVendorCreditDetailArr(i); await w(2000);
    populatePrePaymentDetailArr(i); await w(2500);
    G.VendorCreditAvailableArr.forEach(v => v.AmountToUse = 0);
    G.VendorPrePaymentAvailableArr.forEach(v => v.AmountToUse = 0);
    for (const [vp, amt] of Object.entries(alloc[b.BillNumber])) {
      const p = G.VendorPrePaymentAvailableArr.find(v => v.PrepaymentNumber === vp);
      if (!p) return {err: vp + ' not available for ' + b.BillNumber, avail: G.VendorPrePaymentAvailableArr.map(v => v.PrepaymentNumber + ' ' + v.AmountRemaining)};
      p.AmountToUse = amt;
    }
    billPymnt_ApplyCredit(); await w(1000);
    out.push([b.BillNumber, alloc[b.BillNumber]]);
  }
  if (out.length !== Object.keys(alloc).length) return {err: 'bills not all found', out};
  $('#tbl_billPymnt_searchResult').bootstrapTable('checkBy', {field: 'BillNumber', values: Object.keys(alloc)}); await w(1000);
  const dateEl = $('input').filter((i, e) => /date/i.test(e.id) && $(e).closest('.form-group,div').text().match(/Payment Date/)).first();
  dateEl.val('__DATE__').trigger('change'); await w(500);
  let cap = null; const real = window.execAjx, conf = window.showConfirm, te = toastr.error, errs = [];
  window.execAjx = (p, cb) => { if (/paySelectedBills/.test(p.wUrl)) cap = {url: p.wUrl, name: p.name, data: JSON.parse(JSON.stringify(p.data))}; else real(p, cb); };
  window.showConfirm = (m, t, cb) => cb(true); toastr.error = m => errs.push(m);
  try { paySelectedBills(); } finally { window.execAjx = real; window.showConfirm = conf; toastr.error = te; }
  if (!cap) return {err: 'nothing captured', errs};
  const h = cap.data[0].billPaymentHeader;
  const used = cap.data[0].billPaymentDetailArr.map(d => [d.BillNumber, (d.VendorPrePaymentItemArr || []).map(v => v.PrepaymentNumber + ' ' + v.AmountToUse)]);
  const summary = {used, date: h.TxnDate, cashPaid: h.TotalAmountPaid, vendor: h.VendorName};
  if (!SEND) return summary;
  if (h.TotalAmountPaid !== 0) return Object.assign(summary, {err: 'cash paid is not 0 — not sent'});
  const r = await new Promise(res => execAjx({name: cap.name, data: cap.data, wUrl: cap.url, context: $('<span>')}, res));
  return Object.assign(summary, {Result: r.Result, Message: r.Message});
})()
