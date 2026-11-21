"""Local HTML page for reviewing every conversation of a QQNT nt_msg.db and choosing which to delete.

Usage: python review_conversations.py <nt_msg.db> --key-env QQ_KEY --self <own QQ number> --out <page.html>
                                      [--title "..."] [--preticked <list.tsv> ...]

The page shows the conversation list on the left and the full chat on the right, with a delete checkbox per
conversation, filters and search. "导出删除清单" gives a list in the format purge_conversations.py reads
(kind TAB peer TAB name TAB rows). Lists passed with --preticked (same format) are ticked at the start.
Ticks are kept in the browser's local storage. The page contains message text: keep it on this computer and delete
it when done.
"""
import argparse
import contextlib
import hashlib
import json
from pathlib import Path

import qqnt

TAG = {2: '[图片]', 4: '[语音]', 5: '[视频]', 6: '[表情]', 7: '[回复]', 9: '[红包]', 10: '[卡片]', 16: '[合并转发]'}
SYSTEM_SENDERS = (0, 10000, 1000000)


def render(blob):
    parts = []
    for v in qqnt.elements(blob):
        t = v.get(45002)
        if t == 1:
            parts.append((v.get(45101) or b'').decode('utf-8', 'replace'))
        elif t == 3:
            parts.append('[文件] ' + (v.get(45402) or b'').decode('utf-8', 'replace'))
        elif t == 8:
            j = (v.get(48271) or b'').decode('utf-8', 'replace')
            try:
                j = ''.join(i.get('txt', '') for i in json.loads(j).get('items', []))
            except (ValueError, AttributeError):
                j = ''
            parts.append(j or '[提示]')
        elif t == 11:
            parts.append((v.get(80900) or '[商城表情]'.encode('utf-8')).decode('utf-8', 'replace'))
        else:
            parts.append(TAG.get(t, f'[类型{t}]'))
    return ''.join(parts)


def read_lists(paths):
    out = set()
    for p in paths:
        for line in Path(p).read_text(encoding='utf-8').splitlines():
            if line.strip() and not line.startswith('#'):
                kind, peer = line.split('\t')[:2]
                out.add(('c' if kind == 'c2c' else 'g') + peer.strip())
    return out


def load(db, me, preticked):
    convs, total = [], 0
    recent = {(int(r[0] or 0), str(r[1])) for r in db.execute('SELECT "40010","40021" FROM recent_contact_v3_table')}
    for t, k, kind in (('c2c_msg_table', 'c', 1), ('group_msg_table', 'g', 2)):
        for peer, uid, n, lo, hi, mine in db.execute(
                f'SELECT "40030","40021",count(*),min(nullif("40050",0)),max("40050"),sum("40033"=?) FROM {t} '
                'GROUP BY 1 ORDER BY 3 DESC', (me,)):
            name = ''
            if kind == 1:
                r = db.execute('SELECT "40093" FROM c2c_msg_table WHERE "40030"=? AND "40033"=? AND "40093"<>\'\' '
                               'ORDER BY "40050" DESC LIMIT 1', (peer, peer)).fetchone()
                name = r[0] if r else ''
            msgs = []
            for ts, snd, blob, n90, n93, t11 in db.execute(
                    f'SELECT "40050","40033","40800","40090","40093","40011" FROM {t} WHERE "40030"=? ORDER BY "40050","40001"', (peer,)):
                txt = render(blob)
                if not txt:
                    continue
                who = 0 if snd == me else (1 if t11 == 5 or snd in SYSTEM_SENDERS else (n90 or n93 or str(snd)))
                msgs.append([int(ts or 0), who, txt])
            total += len(msgs)
            convs.append({'k': k, 'id': str(peer), 'name': name or '', 'n': n, 'mine': mine or 0, 'lo': lo or 0, 'hi': hi or 0,
                          'vis': (kind, str(uid)) in recent or (kind, str(peer)) in recent, 'nf': '*' in str(uid),
                          'pd': (k + str(peer)) in preticked, 'm': msgs})
    return convs, total


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('db', type=Path)
    ap.add_argument('--key-env', required=True)
    ap.add_argument('--self', required=True, type=int, dest='me', help='own QQ number (marks own messages)')
    ap.add_argument('--out', required=True, type=Path)
    ap.add_argument('--title', default='QQ 会话审核')
    ap.add_argument('--preticked', nargs='*', default=[], type=Path)
    a = ap.parse_args()
    preticked = read_lists(a.preticked)
    with qqnt.workdir('qqnt-review-') as tmp:
        body, _ = qqnt.strip(a.db, tmp)
        with contextlib.closing(qqnt.connect(body, qqnt.key_from_env(a.key_env), readonly=True)) as db:
            convs, total = load(db, a.me, preticked)
    pre = sorted(k for k in preticked if any(c['k'] + c['id'] == k for c in convs))
    store = 'qqnt-review-' + hashlib.sha256(f'{a.me}|{a.title}'.encode()).hexdigest()[:12]
    esc = lambda s: s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    html = (PAGE.replace('__TITLE__', esc(a.title)).replace('__STORE__', json.dumps(store))
            .replace('__PRE__', json.dumps(pre))
            .replace('__DATA__', json.dumps(convs, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(html, encoding='utf-8')
    print(a.out, 'conversations', len(convs), 'messages', total, 'preticked', len(pre),
          'size MB', round(a.out.stat().st_size / 2**20, 1))


PAGE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{color-scheme:light;--bg:#ffffff;--panel:#ffffff;--soft:#f5f6f8;--ink:#1d1d1f;--mute:#6e6e73;--line:#e5e5ea;--me:#dbeafe;--peer:#ffffff;--sys:#f2f2f2;--acc:#2563eb;--del:#c2410c;--delbg:#fff4ed}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,"Microsoft YaHei",sans-serif;height:100vh;display:flex;flex-direction:column}
header{display:flex;gap:10px;align-items:center;padding:10px 16px;border-bottom:1px solid var(--line);background:var(--panel);flex-wrap:wrap}
header h1{font-size:16px;margin:0 8px 0 0}header .sp{flex:1}
button,select,input{font:inherit;color:inherit}
button{border:1px solid var(--line);background:var(--panel);border-radius:6px;padding:5px 10px;cursor:pointer}button:hover{background:var(--soft)}
button.pri{background:var(--acc);border-color:var(--acc);color:#fff}
select,input[type=search]{border:1px solid var(--line);background:#fff;border-radius:6px;padding:5px 8px}input[type=search]{min-width:180px}
main{flex:1;display:flex;min-height:0}
#list{width:360px;border-right:1px solid var(--line);overflow:auto;background:var(--panel)}
.row{display:flex;gap:8px;align-items:flex-start;padding:8px 12px;border-bottom:1px solid var(--line);cursor:pointer}
.row:hover{background:var(--soft)}.row.on{background:var(--soft);box-shadow:inset 3px 0 var(--acc)}
.row.del .nm{color:var(--del);text-decoration:line-through}.row input{margin-top:3px}
.nm{font-weight:600;word-break:break-all}.meta{color:var(--mute);font-size:12px}
.tag{font-size:11px;border:1px solid var(--line);border-radius:4px;padding:0 4px;margin-right:4px;color:var(--mute)}.tag.w{border-color:#f5c39b;color:var(--del)}
#chat{flex:1;display:flex;flex-direction:column;min-width:0;background:var(--soft)}
#ch{padding:10px 16px;border-bottom:1px solid var(--line);display:flex;gap:12px;align-items:center;flex-wrap:wrap;background:#fff}
#ch .t{font-weight:600;font-size:15px}#ch.del{background:var(--delbg)}#ch label{margin-left:auto;font-weight:600}
#msgs{flex:1;overflow:auto;padding:12px 16px}
.m{max-width:760px;margin:4px 0;display:flex;flex-direction:column}
.m .h{font-size:12px;color:var(--mute)}.m .b{display:inline-block;padding:6px 10px;border-radius:8px;background:var(--peer);border:1px solid var(--line);white-space:pre-wrap;word-break:break-word;align-self:flex-start}
.m.me{margin-left:auto;align-items:flex-end}.m.me .b{background:var(--me);border-color:#bfdbfe;align-self:flex-end}
.m.sys{align-items:center;margin-left:auto;margin-right:auto}.m.sys .b{background:var(--sys);color:var(--mute);font-size:12px;border:0}
.day{text-align:center;color:var(--mute);font-size:12px;margin:14px 0 6px}
#more{display:block;margin:8px auto}
#out{position:fixed;inset:10% 15%;background:#fff;border:1px solid var(--line);border-radius:10px;padding:16px;display:none;flex-direction:column;gap:8px;box-shadow:0 10px 40px #0003}
#out textarea{flex:1;font:13px monospace;background:var(--soft);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:8px}
@media (max-width:700px){main{flex-direction:column}#list{width:auto;max-height:40vh;border-right:0;border-bottom:1px solid var(--line)}#out{inset:5% 16px}}
</style></head><body>
<header><h1>__TITLE__</h1>
<input type="search" id="q" placeholder="搜号码 / 名称">
<select id="f"><option value="all">全部</option><option value="c">只看私聊</option><option value="g">只看群</option><option value="sel">已勾选删除</option><option value="keep">未勾选</option><option value="pd">曾在删除清单</option><option value="hid">会话列表里没有</option><option value="nf">非好友</option></select>
<span class="sp"></span><span id="cnt" class="meta"></span>
<button id="clr">清空勾选</button><button class="pri" id="exp">导出删除清单</button></header>
<main><div id="list"></div><section id="chat"><div id="ch"><span class="meta">← 点左侧会话查看全部聊天记录；勾选 = 要删除。传入的删除清单里的会话已默认勾上。</span></div><div id="msgs"></div></section></main>
<div id="out"><b>要删除的会话（可直接作为 purge_conversations.py 的清单）</b><textarea id="ta" readonly></textarea>
<div><button class="pri" id="cp">复制</button> <button id="dl">下载 .txt</button> <button id="cl">关闭</button></div></div>
<script>
const C=__DATA__, PRE=__PRE__;
const KEY=__STORE__;let del;try{const s=localStorage.getItem(KEY);del=new Set(s?JSON.parse(s):PRE)}catch(e){del=new Set(PRE)}
const save=()=>{try{localStorage.setItem(KEY,JSON.stringify([...del]))}catch(e){}};
const uid=c=>c.k+c.id, fmt=ts=>{if(!ts)return"(无时间)";return new Date(ts*1000).toLocaleString("zh-CN",{hour12:false})};
const day=ts=>ts?new Date(ts*1000).toLocaleDateString("zh-CN"):"(无时间)";
let cur=null,shown=0;const PAGE=400;
function renderList(){const q=document.getElementById("q").value.trim().toLowerCase(),f=document.getElementById("f").value;const L=document.getElementById("list");L.innerHTML="";let shownN=0;
 for(const c of C){const d=del.has(uid(c));if(f==="c"&&c.k!=="c"||f==="g"&&c.k!=="g"||f==="sel"&&!d||f==="keep"&&d||f==="pd"&&!c.pd||f==="hid"&&c.vis||f==="nf"&&!c.nf)continue;
  if(q&&!(c.id.includes(q)||(c.name||"").toLowerCase().includes(q)))continue;shownN++;
  const r=document.createElement("div");r.className="row"+(d?" del":"")+(cur===c?" on":"");
  r.innerHTML=`<input type="checkbox" ${d?"checked":""} title="勾选 = 删除"><div><div class="nm"></div><div class="meta"><span class="tag">${c.k==="c"?"私聊":"群"}</span>${c.nf?'<span class="tag">非好友</span>':""}${c.vis?"":'<span class="tag">列表里没有</span>'}${c.pd?'<span class="tag w">曾在删除清单</span>':""}<br>${c.id} · ${c.n} 条（我发 ${c.mine}） · ${day(c.lo)} – ${day(c.hi)}</div></div>`;
  r.querySelector(".nm").textContent=c.name||"（无名称）";
  r.querySelector("input").onclick=e=>{e.stopPropagation();toggle(c)};r.onclick=()=>open(c);L.appendChild(r)}
 document.getElementById("cnt").textContent=`${C.length} 个会话（当前显示 ${shownN}） · 已勾选删除 ${del.size} 个`}
function toggle(c){const u=uid(c);del.has(u)?del.delete(u):del.add(u);save();renderList();if(cur===c)head()}
function head(){const c=cur,h=document.getElementById("ch");h.className=del.has(uid(c))?"del":"";
 h.innerHTML=`<span class="t"></span><span class="meta">${c.k==="c"?"私聊 QQ号":"群号"} ${c.id} · 共 ${c.m.length} 条可显示消息 · 我发 ${c.mine} 条${c.vis?"":" · 会话列表里没有"}</span><label><input type="checkbox" ${del.has(uid(c))?"checked":""}> 删除这个会话</label>`;
 h.querySelector(".t").textContent=c.name||"（无名称）";h.querySelector("input").onchange=()=>toggle(c)}
function open(c){cur=c;shown=0;head();const M=document.getElementById("msgs");M.innerHTML="";more();M.scrollTop=M.scrollHeight;renderList()}
function more(){const c=cur,M=document.getElementById("msgs");const end=c.m.length-shown,start=Math.max(0,end-PAGE);
 const frag=document.createDocumentFragment();if(start>0){const b=document.createElement("button");b.id="more";b.textContent=`加载更早的消息（还有 ${start} 条）`;b.onclick=()=>{const h=M.scrollHeight;b.remove();more();M.scrollTop=M.scrollHeight-h};frag.appendChild(b)}
 let last="";for(let i=start;i<end;i++){const [ts,who,txt]=c.m[i];const d=day(ts);if(d!==last){const e=document.createElement("div");e.className="day";e.textContent=d;frag.appendChild(e);last=d}
  const e=document.createElement("div");e.className="m"+(who===0?" me":who===1?" sys":"");const h=document.createElement("div");h.className="h";h.textContent=(who===0?"我":who===1?"系统":who)+"  "+fmt(ts);
  const b=document.createElement("div");b.className="b";b.textContent=txt;e.append(h,b);frag.appendChild(e)}
 const old=document.getElementById("more");if(old)old.remove();M.insertBefore(frag,M.firstChild);shown=c.m.length-start}
function exportText(){const sel=C.filter(c=>del.has(uid(c)));return `# 要删除的会话（${sel.length} 个）：类型 TAB 号码 TAB 名称 TAB 条数\n`+sel.map(c=>`${c.k==="c"?"c2c":"group"}\t${c.id}\t${(c.name||"").replace(/\s+/g," ")}\t${c.n}`).join("\n")+"\n"}
document.getElementById("q").oninput=renderList;document.getElementById("f").onchange=renderList;
document.getElementById("clr").onclick=()=>{if(confirm("清空全部勾选？")){del.clear();save();renderList();if(cur)head()}};
document.getElementById("exp").onclick=()=>{document.getElementById("ta").value=exportText();document.getElementById("out").style.display="flex"};
document.getElementById("cl").onclick=()=>document.getElementById("out").style.display="none";
document.getElementById("cp").onclick=()=>{const t=document.getElementById("ta");t.select();navigator.clipboard?navigator.clipboard.writeText(t.value):document.execCommand("copy")};
document.getElementById("dl").onclick=()=>{const a=document.createElement("a");a.href=URL.createObjectURL(new Blob([exportText()],{type:"text/plain"}));a.download="delete_list.tsv";a.click()};
renderList();
</script></body></html>"""


if __name__ == '__main__':
    main()
