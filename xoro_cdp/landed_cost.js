// Add inbound freight/duty to item cost: Item Receipt Reconciliation on /ItemReceipt/ItemReceipt.aspx over CDP.
// Open the receipt first (receipt_open.js), then fill __CFG__ via sed into a scratch copy and run with cdp_eval.mjs.
// CFG = {receipt: "CA-B002055", vendorId: "<carrier IntAccntId>", send: false,
//        charges: [{item: "5100", cad: 100.00, calc: "30"}]}   // calc: 10 Unit Cost, 20 Quantity, 30 Amount, 40 CBM
// Each charge is added in CAD and spread over every inventory line, kept OFF the supplier's bill
// (Add to Vendor Bill? unticked) and accrued to 2145 Accrued Payables - Landed Cost against the carrier.
// send:false stubs execAjx AND blocks write-looking XHR/fetch until reload; returns the updateItemReceipt payload.
// Reload the page before each run. Do not call WebMethodClient/xoro_login from Python meanwhile: it logs the tab out.
(async () => {
  const CFG = __CFG__;
  const ACCRUAL_2145 = 'B7D04105A81B8E2096BE4BAB44D0';
  const w = ms => new Promise(r => setTimeout(r, ms));
  const log = window.__lcLog = [], mark = m => log.push(m);  // where a hung run stopped
  const g = createRcptGlobal, h = g.billObj.billHeaderData;
  if (h.BillNumber !== CFG.receipt) return {err: 'wrong receipt loaded', loaded: h.BillNumber};
  if (h.ReconcileFlag) return {err: 'receipt already reconciled; undo first or use a new reconcile'};
  if (!(g.RcptData.VendorList || []).some(v => v.IntAccntId === CFG.vendorId)) return {err: 'carrier vendor not found', vendorId: CFG.vendorId};
  const before = g.billObj.billItemLineDetailData.reduce((s, l) => s + (+l.LandedUnitCost || +l.Rate) * l.Qty, 0);
  const btn = t => [...document.querySelectorAll('#crtItmRcptReconcileOrder button')].find(b => b.offsetParent && b.textContent.trim() === t);

  // Test mode: refuse every write-looking request for the rest of this page's life (reads like the item
  // search still go through). Stubbing execAjx alone leaked a real updateItemReceipt on 2026-11-10.
  const WRITE = /update|save|create|delete|void|finish|post|apply|reconcile/i;
  const blocked = window.__lcBlocked = window.__lcBlocked || [];
  if (!CFG.send && !window.__lcGuard) {
    window.__lcGuard = true;
    const xo = XMLHttpRequest.prototype.open, xs = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function (m, u, ...r) { this.__u = String(u); return xo.call(this, m, u, ...r); };
    XMLHttpRequest.prototype.send = function (b) {
      if (WRITE.test(this.__u.split('?')[0].split('/').pop())) { blocked.push({url: this.__u, body: b}); return; }
      return xs.call(this, b);
    };
    const f = window.fetch;
    window.fetch = (u, o) => WRITE.test(String(u).split('?')[0].split('/').pop()) ? (blocked.push({url: String(u), body: o && o.body}), new Promise(() => {})) : f(u, o);
  }

  mark('step1');
  // Step 1: every receipt line
  showReconcileModal(); await w(1500);
  $('#tbl_crtItmRcpt_Reconcile').bootstrapTable('checkAll'); await w(300);
  btn('Next').click(); await w(1500);

  // Step 2: one service line per charge, entered in CAD (Xoro converts to the receipt currency)
  mark('step2');
  for (const c of CFG.charges) {
    mark('add ' + c.item);
    $('#dd_crtItmRcpt_itemType').val('30').trigger('change'); await w(1200);
    const sz = $('#dd_crtItmRcpt_itemNumber')[0].selectize;
    sz.focus(); sz.setTextboxValue(c.item); sz.onSearchChange(c.item); await w(3500);
    const opt = Object.values(sz.options).filter(o => o.NAME === c.item);
    if (opt.length !== 1) return {err: 'charge item match', item: c.item, found: Object.values(sz.options).map(o => o.NAME)};
    sz.setValue(String(opt[0].ID)); await w(2500);
    $('#crtItmRcptReconcileAddItemQty').val('1').trigger('change');
    $('#dd_crtItmRcpt_reconcCurrency').val('1').trigger('change').selectpicker('refresh'); await w(1500);
    $('#crtItmRcptReconcileAddItemRate').val(String(c.cad)).data('val', c.cad).trigger('change').trigger('blur'); await w(500);
    $('#dd_crtItmRcptItemTaxCode').selectpicker('val', '4').trigger('change'); await w(300);  // Z: no GST on the allocation
    addReconcileItem(); await w(800);
  }
  document.querySelector('#addItemsNextBtn').click(); await w(1500);

  mark('step3');
  // Step 3: allocate each charge across all lines
  for (const c of CFG.charges) {
    const id = Object.values($('#dd_crtItmRcpt_itemNumber')[0].selectize.options).find(o => o.NAME === c.item);
    const used = [...document.querySelectorAll('#dd_crtItmRcpt_itemUsedToReconcile option')].find(o => o.text === c.item);
    $('#dd_crtItmRcpt_itemUsedToReconcile').val(used ? used.value : (id && id.ID)).trigger('change'); await w(800);
    $('#dd_crtItmRcpt_landedCostCalcType')[0].selectize.setValue(c.calc || '30'); await w(800);
    $('#tbl_crtItmRcpt_ItemsToReconcile').bootstrapTable('checkAll'); await w(300);
    calculateLandedCost(); await w(1500);
  }
  btn('Next').click(); await w(1500);   // -> step 4
  btn('Next').click(); await w(1500);   // calculateReconcileSummary -> summary

  mark('summary');
  // Summary: keep charges off the supplier's bill
  document.querySelectorAll('#tbl_crtItmRcpt_ItemsUsedToReconcileSummary input[data-field=ReconcileAddToVendorBillFlag]')
    .forEach(i => { if (i.checked) i.click(); });
  await w(600);
  finishReconcile(); await w(2500);
  (g.billObj.accrualAccountList || []).forEach(r => {
    r.LineReconcileEntityAccntId = CFG.vendorId; r.LineReconcileAccrualAccntId = ACCRUAL_2145; r.CreateExpenseBillsInRecon = false;
  });

  mark('save');
  let cap = null; const real = window.execAjx, conf = window.showConfirm, errs = [];
  if (!CFG.send) window.execAjx = p => { if (/updateItemReceipt/.test(p.wUrl)) cap = {url: p.wUrl, data: JSON.parse(JSON.stringify(p.data))}; };
  window.showConfirm = (m, t, cb) => cb && cb(true);
  try { addVendorAndAccrualAccount(); await w(1500); saveBillClick($('#btn_crtItemRcpt_Save')[0]); await w(CFG.send ? 6000 : 2500); }
  finally { window.execAjx = real; window.showConfirm = conf; }

  const lines = g.billObj.billItemLineDetailData;
  const inv = lines.filter(l => l.ItemTypeId !== 30);
  const after = inv.reduce((s, l) => s + l.LandedUnitCost * l.Qty, 0);
  const charges = lines.filter(l => l.ItemTypeId === 30).reduce((s, l) => s + l.Rate * l.Qty, 0);
  const summary = {receipt: h.BillNumber, currency: h.CurrencyCode, rate: h.ExchangeRate,
    itemCostBefore: +before.toFixed(2), itemCostAfter: +after.toFixed(2), charges: +charges.toFixed(2),
    balanced: Math.abs(after - before - charges) < 0.05,
    lines: inv.map(l => [l.ItemNumber, l.Qty, +(+l.Rate).toFixed(4), +(+l.LandedUnitCost).toFixed(4)]),
    accrual: lines.filter(l => l.ItemTypeId === 30).map(l => [l.ItemNumber, l.LineReconcileAccrualAccntId === ACCRUAL_2145, l.ReconcileAddToVendorBillFlag])};
  if (!CFG.send) { window.__lcCapture = cap; return Object.assign(summary, {captured: !!cap, blockedWrites: blocked.map(b => b.url)}); }
  return summary;
})()
