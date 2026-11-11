// Load an item receipt into /ItemReceipt/ItemReceipt.aspx by its numeric Id (getitemreceipt billHeader.Id).
// Session entity for receipts is 16 (createRcptGlobal.RcptData.IRSessionEntity), not 17. Run on any Xoro page.
(() => {
  let r = null;
  execAjx({name: ["EntityId", "Id"], data: [16, __RECEIPT_ID__], wUrl: "/WebServices/Common.asmx/editEntity", async: false}, res => { r = res; });
  if (r && r.Result) location.href = '/ItemReceipt/ItemReceipt.aspx';
  return r && r.Message;
})()
