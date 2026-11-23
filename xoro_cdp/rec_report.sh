#!/bin/zsh
# Export a finished Xoro bank rec's report PDF and file it: ./rec_report.sh <bankRecId> "<YY MM>"
# Debug Chrome must have a logged-in Xoro tab. Flow: entityNavigator -> BankReconcilation.aspx -> printBankRecReportClick
# -> Print Preview (Telerik ReportViewer tab) -> export PDF to ~/Downloads -> file_rec.py.
set -e
R=$1; M=$2; cd "$(dirname "$0")"
T=$(curl -s http://127.0.0.1:9222/json | python3 -c "import json,sys;print([t['url'] for t in json.load(sys.stdin) if t['type']=='page' and 'xoro.one' in t['url'] and 'ReportViewer' not in t['url']][0].split('/')[-1])")
CDP_TYPE=page timeout 40 node cdp_eval.mjs "$T" "(()=>{ let r; execAjx({name:['EntityId','value','customFlag'],data:['34','$R','reconcile'],wUrl:'/WebServices/Common.asmx/entityNavigator',async:false},x=>{r=x}); location.href='/Accounting/BankReconcile/BankReconcilation.aspx'; return r&&r.Result })()" >/dev/null || true
sleep 16
CDP_TYPE=page timeout 30 node cdp_eval.mjs "BankReconcilation" "(printBankRecReportClick('$R'), new Promise(r=>setTimeout(()=>{ document.getElementById('btn_reportTemplatePromptSelect').click(); r('preview') },4000)))" >/dev/null
sleep 15
timeout 20 node download_allow.mjs >/dev/null
t0=$(date +%s)
CDP_TYPE=page timeout 40 node cdp_eval.mjs "ReportViewer" "(async()=>{ for (let i=0;i<20;i++){ const el=[...document.querySelectorAll('*')].find(e=>jQuery(e).data('telerik_ReportViewer')); if(el){ jQuery(el).data('telerik_ReportViewer').commands.export.exec('PDF'); return 'ok' } await new Promise(r=>setTimeout(r,1000)) } return 'no' })()" >/dev/null
f=""; for i in $(seq 1 15); do sleep 4; c=$(ls -t ~/Downloads/*.pdf | head -1); [ "$(stat -f %m "$c")" -ge $t0 ] && { f="$c"; break; }; done
[ -z "$f" ] && { echo "no PDF downloaded for rec $R"; exit 1; }
echo "rec $R -> $f"
# close the viewer tab so the next run doesn't pick it up
curl -s http://127.0.0.1:9222/json | python3 -c "import json,sys,urllib.request;[urllib.request.urlopen('http://127.0.0.1:9222/json/close/'+t['id']) for t in json.load(sys.stdin) if 'ReportViewer' in t['url']]" >/dev/null
cd .. && python3 file_rec.py "$f" "$M" 2>&1 | tail -3
