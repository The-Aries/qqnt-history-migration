"""Compare one account's nt_msg.db between two devices, message by message (read-only, on temporary copies).

Usage:
  python compare_devices.py --a <nt_msg.db> --a-key-env A_KEY --a-name windows
                            --b <nt_msg.db> --b-key-env B_KEY --b-name mac --out <dir>

Messages are matched on (table, peer 40030, time 40050, random 40002). For matched rows the tool also compares the
message ID 40001 and the content (element types + text; fields each client writes for itself, such as local paths
and download state, are ignored). Output in <dir>:
  report.json           totals, integrity, matched / same msgId / same content, rows found on one side only
  by_conversation.tsv   per conversation: rows on one side only (with first/last date) and content differences
No message text is written.
"""
import argparse
import collections
import contextlib
import datetime as dt
import json
from pathlib import Path

import qqnt


def summary(blob):
    out = []
    for v in qqnt.elements(blob):
        out.append((v.get(45002), v.get(45101) or v.get(45406) or v.get(47601) or v.get(48271) or b''))
    return tuple(out)


def load(db):
    rows, dups = {}, 0
    for t in qqnt.MSG_TABLES:
        for mid, peer, ts, rnd, blob in db.execute(f'SELECT "40001","40030","40050","40002","40800" FROM {t}'):
            k = (t, int(peer or 0), int(ts or 0), int(rnd or 0) & 0xffffffff)
            if k in rows:
                dups += 1
                k = k + (mid,)
            rows[k] = (mid, summary(blob))
    recent = {(int(r[0] or 0), str(r[1])) for r in db.execute('SELECT "40010","40021" FROM recent_contact_v3_table')}
    return rows, recent, dups, db.execute('PRAGMA quick_check').fetchone()[0]


def day(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime('%Y-%m-%d')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for s in ('a', 'b'):
        ap.add_argument(f'--{s}', required=True, type=Path)
        ap.add_argument(f'--{s}-key-env', required=True)
        ap.add_argument(f'--{s}-name', required=True)
    ap.add_argument('--out', required=True, type=Path)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    data = {}
    with qqnt.workdir('qqnt-cmp-') as tmp:
        for s in ('a', 'b'):
            body, _ = qqnt.strip(getattr(a, s), tmp, name=f'{s}.sqlite')
            with contextlib.closing(qqnt.connect(body, qqnt.key_from_env(getattr(a, f'{s}_key_env')), readonly=True)) as db:
                data[s] = load(db)
    (ra, reca, dupa, ia), (rb, recb, dupb, ib) = data['a'], data['b']
    na, nb = a.a_name, a.b_name
    both = ra.keys() & rb.keys()
    only = {na: ra.keys() - rb.keys(), nb: rb.keys() - ra.keys()}
    diff_content = [k for k in both if ra[k][1] != rb[k][1]]
    per = collections.defaultdict(collections.Counter)
    span = {}
    for side, keys in only.items():
        for k in keys:
            c = (k[0], k[1])
            per[c][side] += 1
            lo, hi = span.get((c, side), (k[2], k[2]))
            span[(c, side)] = (min(lo, k[2]), max(hi, k[2]))
    for k in diff_content:
        per[(k[0], k[1])]['content_differs'] += 1
    count = lambda keys: {t: sum(1 for k in keys if k[0] == t) for t in qqnt.MSG_TABLES}
    report = {
        'integrity': {na: ia, nb: ib},
        'rows': {na: count(ra), nb: count(rb)},
        'duplicate_keys': {na: dupa, nb: dupb},
        'matched': len(both),
        'matched_same_msgid': sum(1 for k in both if ra[k][0] == rb[k][0]),
        'matched_same_content': len(both) - len(diff_content),
        'only': {side: count(keys) for side, keys in only.items()},
        'recent_contacts': {na: len(reca), nb: len(recb), f'{na}_only': len(reca - recb), f'{nb}_only': len(recb - reca)},
        'conversations_with_differences': len(per),
    }
    (a.out / 'report.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    with (a.out / 'by_conversation.tsv').open('w', encoding='utf-8') as f:
        f.write(f'table\tpeer\t{na}_only\t{na}_first\t{na}_last\t{nb}_only\t{nb}_first\t{nb}_last\tcontent_differs\n')
        for c, cnt in sorted(per.items(), key=lambda x: -sum(x[1].values())):
            sa, sb = span.get((c, na)), span.get((c, nb))
            f.write('\t'.join(map(str, [c[0], c[1],
                                         cnt[na], day(sa[0]) if sa else '', day(sa[1]) if sa else '',
                                         cnt[nb], day(sb[0]) if sb else '', day(sb[1]) if sb else '',
                                         cnt['content_differs']])) + '\n')
    print(json.dumps(report, indent=1))


if __name__ == '__main__':
    main()
