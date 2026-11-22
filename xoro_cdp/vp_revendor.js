// Move the vendor prepayment loaded in edit mode on VendorPrepayment.aspx to another vendor (same bank, amount, date).
(async () => {
  const SEND = __SEND__, w = ms => new Promise(r => setTimeout(r, ms));
  const before = {num: vendorPrepaymentGlobal.VendorPrepaymentObj.PrepaymentNumber, date: $('#txt_crtVnPrp_prepaymentDate').val(),
    amt: $('#txt_crtVnPrp_prepaymentAmount').val(), acct: $('#dd_crtVnPrp_PaymentAccountId').val(), rate: $('#txt_crtVnPrp_exchangeRate').val()};
  $('#dd_crtVnPrp_vendor').selectpicker('val', '__VENDOR__').trigger('change'); await w(3000);
  // vendor change can reset these — put them back
  $('#txt_crtVnPrp_prepaymentDate').val(before.date).trigger('change'); await w(1200);
  $('#dd_crtVnPrp_PaymentAccountId').selectpicker('val', before.acct).trigger('change'); await w(800);
  $('#txt_crtVnPrp_exchangeRate').val(before.rate).trigger('change');
  $('#dd_crtVnPrp_AssetAccountId').selectpicker('val', 'B7D0FD556E43A04058E4906F44CD').trigger('change'); await w(500);
  $('#txt_crtVnPrp_prepaymentAmount').val(before.amt).trigger('change'); await w(500);
  let cap = null; const real = window.execAjx, conf = window.showConfirm;
  window.execAjx = (p, cb) => { if (/VendorPrepaymentWebMethods/.test(p.wUrl) && /update|create/i.test(p.wUrl)) cap = {name: p.name, url: p.wUrl, data: JSON.parse(JSON.stringify(p.data))}; else real(p, cb); };
  window.showConfirm = (m, t, cb) => cb && cb(true);
  try { saveVendorPrepayment($('#btn_crtVnPrp_savePayment')[0]); } finally { window.execAjx = real; window.showConfirm = conf; }
  if (!cap) return {err: 'nothing captured', before};
  const o = cap.data;
  const s = {url: cap.url, num: o.PrepaymentNumber, vendor: o.VendorName, date: o.TxnDate, amt: o.Amount, acct: o.PaymentAccntName, rate: o.ExchangeRate, before};
  if (!SEND) return s;
  if (!/update/.test(cap.url)) return Object.assign(s, {err: 'would create, not update — not sent'});
  const r = await new Promise(res => execAjx({name: cap.name, data: cap.data, wUrl: cap.url, context: $('<span>')}, res));
  return Object.assign(s, {Result: r.Result, Message: r.Message});
})()
