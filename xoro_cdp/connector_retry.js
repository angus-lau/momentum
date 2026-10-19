// Retry connector order imports (= the ▶ button), then report. Runs inside the shopify*.xorosoft.io frame.
// Edit ORDERS before running. SEND=false only lists what would be retried.
(async () => {
  const ORDERS = '__ORDERS__'.split(','), SEND = __SEND__;
  const q = new URLSearchParams(location.search);
  const auth = 'user=' + q.get('user') + '&hmac=' + encodeURIComponent(q.get('hmac')) + '&account=' + encodeURIComponent((q.get('account') || '').trim());
  const rows = [];
  for (let n = 1; n <= 40; n++) {
    const body = JSON.stringify({size: 50, number: n, importExportSearchExp: JSON.stringify({OrderRefNumber: "", Keyword: "", ShowOnlyUnResolved: true, PendingFlag: true}), sorder: "desc", sname: "IMPORT_EXPORT_DATE"});
    const j = await (await fetch('/ImportExport/getOrderDetail?' + auth, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest'}, body})).json();
    rows.push(...j.rows); if (rows.length >= j.total || !j.rows.length) break;
  }
  const num = r => ((r.MESSAGE || '').match(/Order<\/b> - (\d+):/) || [])[1];
  const hit = ORDERS.map(o => rows.find(r => num(r) === o)).filter(Boolean);
  const missing = ORDERS.filter(o => !hit.some(r => num(r) === o));
  if (!SEND) return {wouldRetry: hit.map(r => num(r) + ' ' + r.REF_ID_APP + '/' + r.ID), notInErrors: missing};
  const url = '/ImportExport/importExportOrderErrorSyncDetailRun?commaSepRefIdList=' + hit.map(r => r.REF_ID_APP).join(',') + '&commaSepImportDetailIdList=' + hit.map(r => r.ID).join(',') + '&' + auth;
  const res = await fetch(url, {redirect: 'follow'});
  return {retried: hit.map(num), status: res.status, notInErrors: missing};
})()
