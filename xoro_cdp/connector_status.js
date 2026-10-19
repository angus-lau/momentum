// Which of ORDERS are still in the connector's unresolved order errors, and why.
(async () => {
  const ORDERS = '__ORDERS__'.split(',');
  const q = new URLSearchParams(location.search);
  const auth = 'user=' + q.get('user') + '&hmac=' + encodeURIComponent(q.get('hmac')) + '&account=' + encodeURIComponent((q.get('account') || '').trim());
  const rows = [];
  for (let n = 1; n <= 40; n++) {
    const body = JSON.stringify({size: 50, number: n, importExportSearchExp: JSON.stringify({OrderRefNumber: "", Keyword: "", ShowOnlyUnResolved: true, PendingFlag: true}), sorder: "desc", sname: "IMPORT_EXPORT_DATE"});
    const j = await (await fetch('/ImportExport/getOrderDetail?' + auth, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest'}, body})).json();
    rows.push(...j.rows); if (rows.length >= j.total || !j.rows.length) break;
  }
  return ORDERS.map(o => { const r = rows.find(r => (r.MESSAGE || '').includes('Order</b> - ' + o + ':'));
    return o + '  ' + (r ? r.IMPORT_EXPORT_DATE + '  ' + r.MESSAGE.replace(/<[^>]+>/g, '').replace(/^Order - \d+: /, '').slice(0, 150) : 'CLEARED (no longer in errors)'); });
})()
