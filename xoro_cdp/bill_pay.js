// Pay one Xoro bill through the real Bill Payment page (/Payment/Bills/BillPayment.aspx) over CDP.
// Fill __PLACEHOLDERS__ with sed into a scratch copy; SEND=false captures paySelectedBills's payload only.
// Reload the page before each run — the bill checkbox toggles. Proven 2026-10-19: B001690 (CA-B002033).
(async () => {
  const SEND = __SEND__;
  const $ = jQuery, wait = ms => new Promise(r => setTimeout(r, ms));
  $('#dd_billPymnt_ApAccount').selectpicker('val', '__AP_ID__').trigger('change'); await wait(2500);
  $('#dd_billPymnt_vendor').selectpicker('val', '__VENDOR_ID__').trigger('change'); await wait(4000);
  $('#dd_billPymnt_paymentMethod').selectpicker('val', '__METHOD_ID__').trigger('change'); await wait(2000);
  $('#dd_billPymnt_paymentAccount').selectpicker('val', '__ACCOUNT_ID__').trigger('change'); await wait(1500);
  const dateEl = $('input').filter((i, e) => /date/i.test(e.id) && $(e).closest('.form-group,div').text().match(/Payment Date/)).first();
  dateEl.val('__DATE__').trigger('change');
  const tbl = $('table').filter((i, t) => /Bill#/.test($(t).text())).first();
  const rows = tbl.find('tbody tr').filter((i, tr) => /__BILL__/.test($(tr).text()));
  if (rows.length !== 1) return {err: '__BILL__ row not found', rowsText: tbl.find('tbody tr').map((i, tr) => $(tr).text().replace(/\s+/g, ' ').slice(0, 120)).get().slice(0, 5)};
  rows.find('input[type=checkbox]').first().click(); await wait(1200);
  let cap = null; const real = window.execAjx, conf = window.showConfirm;
  window.execAjx = (p, cb) => { if (/paySelectedBills/.test(p.wUrl)) cap = {url: p.wUrl, name: p.name, data: JSON.parse(JSON.stringify(p.data))}; else real(p, cb); };
  window.showConfirm = (m, t, cb) => cb(true);
  try { paySelectedBills(); } finally { window.execAjx = real; window.showConfirm = conf; }
  if (!cap) return {err: 'nothing captured (validation?)', dateField: dateEl.attr('id'), date: dateEl.val()};
  const flat = JSON.stringify(cap.data);
  const summary = {dateField: dateEl.attr('id'), date: dateEl.val(), amountsSeen: (flat.match(/__AMOUNT_RE__/g) || []).length, billSeen: /__BILL__/.test(flat), acct1160: /__ACCOUNT_ID__/.test(flat), payload: flat.slice(0, 900)};
  if (!SEND) return summary;
  const r = await new Promise(res => execAjx({name: cap.name, data: cap.data, wUrl: cap.url, context: $('<span>')}, res));
  return Object.assign(summary, {Result: r.Result, Message: r.Message, Data: JSON.stringify(r.Data || '').slice(0, 300)});
})()
