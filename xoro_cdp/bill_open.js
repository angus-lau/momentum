// Load a bill into /Bill/Bill.aspx by its numeric Id (GL RefId). Bill session entity is 17. Run on any Xoro page.
(() => {
  let r = null;
  execAjx({name: ["EntityId", "Id"], data: [17, __BILL_ID__], wUrl: "/WebServices/Common.asmx/editEntity", async: false}, res => { r = res; });
  if (r && r.Result) location.href = '/Bill/Bill.aspx';
  return r && r.Message;
})()
