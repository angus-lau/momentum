// File a XoroDesk support ticket from /TicketServer/Ticket.aspx over CDP. Fill __SUBJECT__/__BODY__ (HTML) and __SEND__.
// SEND=false captures submitTicket's payload only (execAjx stubbed). Reload the page between tickets.
(async () => {
  const SEND = __SEND__, w = ms => new Promise(r => setTimeout(r, ms));
  $('#txt_ticket_subject').val(__SUBJECT__).trigger('change');
  $('#txt_ticket_description').summernote('code', __BODY__);
  $('#dd_ticket_severity').selectpicker('val', '10').trigger('change');   // Urgent — user: always Urgent (2026-11-23)
  $('#dd_ticket_type').selectpicker('val', '10').trigger('change');       // Seeing an error
  $('#dd_ticket_module').selectpicker('val', '50').trigger('change'); await w(2500);   // E-Commerce Connection
  $('#dd_ticket_subModule').selectpicker('val', '121').trigger('change'); await w(500); // Shopify
  let cap = null; const real = window.execAjx, conf = window.showConfirm;
  window.execAjx = (p, cb) => { if (/submitTicket/.test(p.wUrl)) cap = {name: p.name, url: p.wUrl, data: JSON.parse(JSON.stringify(p.data))}; else real(p, cb); };
  window.showConfirm = (m, t, cb) => cb && cb(true);
  try { submitTicketObj(); } finally { window.execAjx = real; window.showConfirm = conf; }
  if (!cap) return {err: 'nothing captured (validation?)'};
  const t = typeof cap.data === 'string' ? JSON.parse(cap.data) : (cap.data.ticketJson ? JSON.parse(cap.data.ticketJson) : cap.data);
  const sum = {subject: t.Subject, sev: t.SeverityLevelId, type: t.TicketTypeId, mod: t.ModuleId, sub: t.SubModuleId, bodyLen: (t.Description || '').length};
  if (!SEND) return sum;
  const r = await new Promise(res => execAjx({name: cap.name, data: cap.data, wUrl: cap.url, context: $('<span>')}, res));
  return Object.assign(sum, {Result: r.Result, Message: r.Message, Ticket: r.Data && r.Data.TicketNumber});
})()
