// Pull every row of a Xoro "Centre" table for a date range, via the same refreshData call the
// table uses. Fill __COMP__ (the page's centre compCode), __DATEFIELD__, __FROM__, __TO__ (MM/DD/YYYY).
(async () => {
  const COMP = __COMP__, F = '__DATEFIELD__', FROM = '__FROM__', TO = '__TO__';
  const expr = [{DataType: 'System.DateTime', FieldName: F, OperatorCode: 3, FieldValue: FROM},
                {DataType: 'System.DateTime', FieldName: F, OperatorCode: 5, FieldValue: TO}];
  const rows = []; let total = null;
  for (let n = 1; n <= 200; n++) {
    const body = JSON.stringify({size: 500, number: n, sorder: 'asc', strExpressions: JSON.stringify(expr), sname: F, compType: COMP});
    const r = await fetch('/WebServices/Common.asmx/refreshData', {method: 'POST', headers: {'Content-Type': 'application/json; charset=utf-8', 'X-Requested-With': 'XMLHttpRequest'}, body});
    const d = JSON.parse((await r.json()).d);
    if (d.Result === false) return {error: d.Message};
    const page = JSON.parse(d.Data);
    total = page.total; rows.push(...page.rows);
    if (rows.length >= total || !page.rows.length) break;
  }
  return {total, count: rows.length, rows};
})()
