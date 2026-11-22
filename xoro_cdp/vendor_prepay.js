// Create one vendor prepayment on /VendorPrepayment/VendorPrepayment.aspx. Fill __VENDOR__ (id), __DATE__, __AMOUNT__,
// __ACCOUNT__ (bank FAccountingId), __ASSET__ (13xx prepaid FAccountingId), __MEMO__, __SEND__. Reload between runs.
(async () => {
  const SEND = __SEND__, w = ms => new Promise(r => setTimeout(r, ms));
  $('#dd_crtVnPrp_store').selectpicker('val', '10001').trigger('change'); await w(1200);
  $('#dd_crtVnPrp_vendor').selectpicker('val', '__VENDOR__').trigger('change'); await w(3000);
  $('#txt_crtVnPrp_prepaymentDate').val('__DATE__').trigger('change'); await w(2000);
  $('#dd_crtVnPrp_PaymentMethod').selectpicker('val', '1').trigger('change'); await w(1000);
  $('#dd_crtVnPrp_PaymentAccountId').selectpicker('val', '__ACCOUNT__').trigger('change'); await w(1500);
  $('#dd_crtVnPrp_AssetAccountId').selectpicker('val', '__ASSET__').trigger('change'); await w(800);
  $('#txt_crtVnPrp_prepaymentAmount').val('__AMOUNT__').trigger('change').trigger('blur'); await w(800);
  $('#txt_crtVnPrp_Memo').val(__MEMO__).trigger('change');
  let cap = null; const real = window.execAjx, conf = window.showConfirm;
  window.execAjx = (p, cb) => { if (/VendorPrepayment/i.test(p.wUrl) && /save|create/i.test(p.wUrl)) cap = {name: p.name, url: p.wUrl, data: JSON.parse(JSON.stringify(p.data))}; else real(p, cb); };
  window.showConfirm = (m, t, cb) => cb && cb(true);
  try { saveVendorPrepayment($('#btn_crtVnPrp_savePayment')[0]); } finally { window.execAjx = real; window.showConfirm = conf; }
  if (!cap) return {err: 'nothing captured (validation?)'};
  const o = typeof cap.data === 'string' ? JSON.parse(cap.data) : (Array.isArray(cap.data) ? cap.data : cap.data);
  const flat = JSON.stringify(cap.data);
  const sum = {url: cap.url, name: cap.name, has: ['__AMOUNT__', '__DATE__', '__ACCOUNT__', '__ASSET__'].map(x => flat.includes(x)), preview: flat.slice(0, 700)};
  if (!SEND) return sum;
  const r = await new Promise(res => execAjx({name: cap.name, data: cap.data, wUrl: cap.url, context: $('<span>')}, res));
  return Object.assign(sum, {Result: r.Result, Message: r.Message});
})()
