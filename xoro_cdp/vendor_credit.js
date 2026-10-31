// Create a Xoro vendor credit (one expense line) through /VendorCredit/VendorCredit.aspx over CDP.
// Fill __PLACEHOLDERS__ with sed into a scratch copy; SEND=false captures createVendorCredit's payload only.
// Open a fresh page per credit. Proven 2026-10-31: CA-V000083 (Lowen, 7000) and CA-V000084 (Mistal, 1145).
(async () => {
  const SEND = __SEND__;
  const w = ms => new Promise(r => setTimeout(r, ms)), g = createVendorCreditGlobal, o = g.vendorCreditObj;
  const vid = [...document.querySelectorAll('#dd_crtVenCr_Vendor option')].filter(x => /__VENDOR_RE__/i.test(x.text)).map(x => x.value);
  if (vid.length !== 1) return {err: 'vendor match', vid};
  const acc = (g.expenseAccntListForSelect || []).flatMap(x => x.children).filter(c => /__ACCOUNT_RE__/.test(c.text));
  if (acc.length !== 1) return {err: 'account match', acc: acc.map(a => a.text)};
  $('#dd_crtVenCr_store').val('__STORE_ID__').trigger('change'); await w(800);
  $('#crtVenCr_Date').val('__DATE__').trigger('change'); await w(500);
  $('#dd_crtVenCr_Vendor').val(vid[0]).trigger('change'); await w(4000);  // sets AP, currency, rate for the date
  o.vendorCreditExpenseLineDetailData.length = 0;
  o.vendorCreditExpenseLineDetailData.push(Object.assign({}, g.vendorCreditExpenseLineDetailSchema, {
    LineSeq: 0, AccountId: acc[0].value, AccountName: acc[0].text, TaxCodeId: null,
    Amount: __AMOUNT__, TaxAmtNonCl: 0, Memo: '__LINE_MEMO__', EditState: true}));
  calcTaxDataExpenseLine(0); calculateExpenseNcTaxAmount(0); rg_calculateSummary();
  $('#tbl_crtVenCr_expenseLineTable').bootstrapTable('load', o.vendorCreditExpenseLineDetailData);
  $('#txt_crtVenCMemo').val('__MEMO__');
  $('#txt_crtVenCr_RefNo').val('__REF__');
  let cap = null; const real = window.execAjx, conf = window.showConfirm, te = toastr.error, errs = [];
  window.execAjx = p => { cap = {url: p.wUrl, name: p.name, data: JSON.parse(JSON.stringify(p.data))}; };
  window.showConfirm = (m, t, cb) => cb(true); toastr.error = m => errs.push(m);
  try { saveVenCrClick($('<a>')[0], false); } finally { window.execAjx = real; window.showConfirm = conf; toastr.error = te; }
  if (!cap) return {err: 'nothing captured', errs};
  const h = cap.data.vendorCreditHeader, l = cap.data.vendorCreditExpenseLineArr[0];
  const summary = {vendor: h.VendorId, ap: $('#dd_crtVenCr_accountPayable option:selected').text(), currency: h.CurrencyCode,
    date: h.TxnDate, rate: h.ExchangeRate, total: h.TotalAmt, store: h.StoreName, line: [l.AccountName, l.Amount], ref: h.RefNo, memo: h.Memo};
  if (!SEND) return summary;
  // send through execAjx: it double-encodes the named param; a raw fetch of {name: obj} is rejected
  const r = await new Promise(res => execAjx({name: cap.name, data: cap.data, wUrl: cap.url, context: $('<span>')}, res));
  return Object.assign(summary, {Result: r.Result, Message: r.Message});
})()
