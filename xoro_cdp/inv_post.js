// Post one +DELTA adjustment at SAMPLES & VANCOUVER SERVICE CENTRE INVENTORY (store SC 10008), dated DATE.
(async () => {
  const ITEM = __ITEM__, SKU = '__SKU__', ADJ = '__ADJ__', DELTA = __DELTA__, DATE = '09/01/2026', SEND = __SEND__;
  const LOC = 'SAMPLES & VANCOUVER SERVICE CENTRE INVENTORY', STORE = '10008';
  const $ = jQuery, wait = ms => new Promise(r => setTimeout(r, ms));
  const call = p => new Promise(res => execAjx(Object.assign({context: $('<span>')}, p), res));
  if (!window.__scLocId) {  // location id from an item known to have an SC row (AAAAAGAI 15217)
    const v = await call({name: ["storeId","ItemId","UomId","UomName"], data: [STORE, "15217", 0, ""], wUrl: "/WebServices/WM/LocationWebMethods.asmx/getInvAdjLocationInventoryAllocationView"});
    window.__scLocId = (v.Data.find(x => x.LocationName === LOC) || {}).LocationId;
  }
  $('#dd_invAdj_store').selectpicker('val', STORE).trigger('change'); await wait(1500);
  const s = $('#dd_invAdj_itemNumber')[0].selectize;
  s.addOption({ID: ITEM, NAME: SKU, DESCRIPTION: '', ADJ_ACCNT_ID: ADJ, IS_3A_ITEM: false, ITEM_UPC: ''});
  s.setValue(ITEM); await wait(3500);
  let data = (invAdjGlobal.inventoryArr || []).map(r => Object.assign({}, r));
  let row = data.find(r => r.LocationName === LOC && String(r.StoreId) === STORE);
  if (row) { row.NewQty = row.OnHandQty + DELTA; row.Diff = DELTA; }
  else {
    const L = getLocationDetailFromId(window.__scLocId);
    if (!L) return {sku: SKU, err: 'cannot resolve SC location ' + window.__scLocId};
    row = {StoreId: L.StoreId, StoreName: L.StoreName, ItemId: String(ITEM), ItemNumber: SKU, LocationId: L.Id, LocationName: L.DisplayLocation,
           LocationTypeName: L.LocationTypeName, LocationClassId: L.LocationClassId, LocationClassName: L.LocationClassName,
           OnHandQty: 0, AvailableQty: 0, AllocatedQty: 0, NetAvailable: 0, Flag: false, NewQty: DELTA, Diff: DELTA,
           LpnNumber: '', LotNumber: '', QualityName: '', BatchNumber: '', MfgDate: '', QualityId: '', ExpDate: '', Upc: '',
           UomId: invAdjGlobal.inventoryHeaderObj.BaseUomId, UomName: invAdjGlobal.inventoryHeaderObj.BaseUomName,
           SellUomName: invAdjGlobal.inventoryHeaderObj.BaseUomName, LpnLength: '', LpnWidth: '', LpnHeight: '', LpnWeight: ''};
    data.push(row);
  }
  invAdjGlobal.inventoryArr = data;
  $('#tbl_invAdj_ByQuantity').bootstrapTable('load', data);
  $('#txt_invAdj_adjDate').val(DATE);
  $('#dd_invAdj_adjRCode').selectpicker('val', '2');
  $('#txt_invAdj_adjMemo').val('Sep 2026 close: stock for Shopify SC orders failing import (' + SKU + ' +' + DELTA + ')');
  invAdjCalcSummary();
  let cap = null; const real = window.execAjx, conf = window.showConfirm;
  window.execAjx = (p, cb) => { if (/adjustInventory/.test(p.wUrl)) cap = JSON.parse(JSON.stringify(p.data)); else real(p, cb); };
  window.showConfirm = (m, t, cb) => cb(true);
  try { invAdjAdjustInventory(); } finally { window.execAjx = real; window.showConfirm = conf; }
  if (!cap) return {sku: SKU, err: 'page validation failed, nothing built'};
  const h = cap.invAdjHeaderObj, lines = cap.invAdjLocationListObj;
  const ok = h.TxnDate === DATE && h.StoreId === STORE && +h.QtyAdj === DELTA && lines.length === 1 && lines[0].LocationName === LOC && +lines[0].Diff === DELTA;
  const summary = {sku: SKU, date: h.TxnDate, store: h.StoreName, acct: h.AdjAccntName, qtyAdj: h.QtyAdj, line: lines.map(l => l.LocationName + ' ' + l.OnHandQty + '->' + l.NewQty), checks: ok};
  if (!ok || !SEND) return summary;
  const r = await call({name: "jsonData", data: cap, wUrl: "/WebServices/WM/InventoryWebMethods.asmx/adjustInventory"});
  return Object.assign(summary, {Result: r.Result, Message: r.Message});
})()
