// Receive a customer payment against chosen invoices on /Payment/Invoice/InvoicePayment.aspx over CDP.
// Fill __PLACEHOLDERS__ with sed into a scratch copy; SEND=false captures payInvoices's payload only.
// __PLAN__ is a JSON map {"CA-I122294": 16.80, ...} that must sum to __AMOUNT__. Customer fields come from
// any of the customer's invoices (XoroClient.get_invoices: CustomerId, CurrencyId/Code, IntAccntId).
// Reload the page before each run. Proven 2026-10-31: IP011054 (Mistal set-off, deposit to 1145).
(async () => {
  const SEND = __SEND__, plan = __PLAN__;
  const w = ms => new Promise(r => setTimeout(r, ms)), G = invPymntGlobal;
  $('#dd_invPymt_store').selectpicker('val', '__STORE_ID__').trigger('change'); await w(1000);
  // the customer dropdown is infinite-scroll: inject the option with the data fields the page reads
  const sz = $('#dd_invPymt_customer')[0].selectize;
  const opt = {CurrencySymbol: '__CURRENCY_SYMBOL__', CurrencyCode: '__CURRENCY_CODE__', CurrencyId: __CURRENCY_ID__, FullName: '__CUSTOMER_NAME__',
    IntAccntId: '__INT_ACCNT_ID__', DefaultDepositAccountId: null, DefaultPaymentMethodId: null, ProcessCardAsDefault: false, CustomerTypeId3PL: null};
  opt[sz.settings.valueField] = '__CUSTOMER_ID__'; opt[sz.settings.labelField] = '__CUSTOMER_NAME__';
  sz.addOption(opt); sz.setValue('__CUSTOMER_ID__'); await w(7000);
  $('#creditCardProcessAccess').prop('checked', false).trigger('change');  // on by default; blocks with "No card selected"
  $('#chk_crtCustDep_achProcessAccess').prop('checked', false).trigger('change');
  $('#dd_invPymt_paymentMethod').selectpicker('val', '__METHOD_ID__').trigger('change'); await w(1000);
  $('#dd_invPymt_paymentDepositAccount').selectpicker('val', '__DEPOSIT_ACCT_ID__').trigger('change'); await w(1000);
  $('#txt_invPymt_paymentDate').val('__DATE__').trigger('change'); await w(1500);
  $('#txt_invPymt_paymentAmount').val('__AMOUNT__').trigger('change');
  $('#dd_invPymt_referenceNo').val('__REF__');
  $('#dd_invPymt_memo').val('__MEMO__');
  const rows = G.invPaymentViewArr, found = [];
  rows.forEach(x => { x.AmountToPay = 0; if (x.InvoiceNumber in plan) { x.AmountToPay = plan[x.InvoiceNumber].toFixed(2); found.push(x.InvoiceNumber); } });
  if (found.length !== Object.keys(plan).length) return {err: 'invoices not all open', found};
  $('#tbl_invPymt_searchResult').bootstrapTable('load', rows); await w(500);
  $('#tbl_invPymt_searchResult').bootstrapTable('checkBy', {field: 'InvoiceNumber', values: Object.keys(plan)}); await w(800);
  let cap = null; const real = window.execAjx, conf = window.showConfirm, sa = window.showAlert, te = toastr.error, msgs = [];
  window.execAjx = (p, cb) => { if (/payInvoices/.test(p.wUrl)) cap = {url: p.wUrl, name: p.name, data: JSON.parse(JSON.stringify(p.data))}; else real(p, cb); };
  window.showConfirm = (m, t, cb) => cb(true); window.showAlert = m => msgs.push(String(m).slice(0, 200)); toastr.error = m => msgs.push(m);
  try { invPymntPaySelectedInvoices(); await w(1500); } finally { window.execAjx = real; window.showConfirm = conf; window.showAlert = sa; toastr.error = te; }
  if (!cap) return {err: 'nothing captured (overpayment modal or validation?)', msgs};
  const h = cap.data.PaymentHeader;
  const summary = {customer: h.CustomerId, date: h.TxnDate, amount: h.Amount, applied: h.AmountInvoicePaid, deposit: h.DepositAccntName,
    rate: h.ExchangeRate, invoices: cap.data.InvoiceSelectionArr.map(x => x.InvoiceNumber + ':' + x.AmountToPay)};
  if (!SEND) return summary;
  const r = await new Promise(res => execAjx({name: cap.name, data: cap.data, wUrl: cap.url, context: $('<span>')}, res));
  return Object.assign(summary, {Result: r.Result, Message: r.Message});
})()
