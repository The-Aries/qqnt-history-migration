"""Check the conversion rules against rows the QQNT importer wrote itself (read-only).

Usage: python legacy_check.py --reconcile <reconcile.sqlite> --qqnt <nt_msg.db> --key-env QQ_KEY --self <QQ number>
                              --tz Asia/Shanghai [--out <report.json>] [--examples 5]

For every pair from legacy_reconcile.py whose QQNT row is the importer's own output, the legacy record is converted
with legacy.py and compared with the importer's row: the element list (IDs 45001 and fields the client writes later,
such as local paths and download state, are ignored), the message type 40011/40012, and 40058. The result is grouped
by the element types of the legacy record. A type should be written only after its rows match here. Examples list
msgIds and the differing field numbers, never message text.
"""
import argparse
import collections
import contextlib
import json
import sqlite3
from pathlib import Path

import legacy
import qqnt

IGNORED = {45001, 45812, 45813, 45814, 45416, 45403, 45550, 45507, 45509, 45802, 45816, 45408}


def norm(blob):
    return [tuple(sorted((a, x) for a, _w, x in qqnt.parse(pl) if a not in IGNORED))
            for f, _w, pl in qqnt.parse(blob) if f == 40800]


def signature(content):
    return ','.join(str(ty) for ty, _ in (legacy.tlv(content) or []) if ty not in legacy.META) or 'none'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--reconcile', required=True, type=Path)
    ap.add_argument('--qqnt', required=True, type=Path)
    ap.add_argument('--key-env', required=True)
    ap.add_argument('--self', required=True, type=int, dest='me')
    ap.add_argument('--tz', required=True, help='IANA time zone of the computer that ran the importer')
    ap.add_argument('--out', type=Path)
    ap.add_argument('--examples', type=int, default=5)
    a = ap.parse_args()
    res = collections.defaultdict(collections.Counter)
    examples = collections.defaultdict(list)
    rec_db = sqlite3.connect(f'file:{a.reconcile.as_posix()}?mode=ro', uri=True)
    with qqnt.workdir('qqnt-check-') as tmp:
        body, _ = qqnt.strip(a.qqnt, tmp)
        with contextlib.closing(qqnt.connect(body, qqnt.key_from_env(a.key_env), readonly=True)) as db:
            w = legacy.Writer(db, a.me, a.tz)
            for t, peer, rnd, ts, snd, content, info, msgid in rec_db.execute(
                    'SELECT qtable, peer, rand, time, sender, content, info, msgid FROM pairs WHERE dt=0'):
                row = db.execute(f'SELECT "40800","40011","40012","40058" FROM {t} WHERE "40001"=?', (msgid,)).fetchone()
                if not row or not legacy.is_imported_row(row[0], msgid, ts):
                    continue
                sig = signature(content)
                c = res[sig]
                c['importer_rows'] += 1
                try:
                    els, kind = legacy.convert(content, t, snd)
                except legacy.Unsupported as e:
                    c['unsupported_' + str(e)] += 1
                    continue
                gen = [tuple(sorted((a_, x) for a_, _w, x in f if a_ not in IGNORED)) for _q, f in els]
                mine = norm(row[0])
                diff = []
                if gen != mine:
                    g, m = set().union(*map(set, gen)) if gen else set(), set().union(*map(set, mine)) if mine else set()
                    diff = sorted({f for f, _ in g ^ m}) or ['element_order_or_count']
                    c['elements_differ'] += 1
                else:
                    c['elements_same'] += 1
                key = tuple(sorted(set(q for q, _ in els)))
                if kind == 'content' and key in legacy.TYPESET and legacy.TYPESET[key] != (row[1], row[2]):
                    c['msg_type_differs'] += 1
                    diff.append('40011/40012')
                if w.local_midnight(ts) != row[3]:
                    c['40058_differs'] += 1
                    diff.append('40058')
                if diff and len(examples[sig]) < a.examples:
                    examples[sig].append({'table': t, 'msgid': str(msgid), 'fields': diff})
    rec_db.close()
    report = {'by_legacy_signature': {k: dict(v) for k, v in sorted(res.items(), key=lambda x: -x[1]['importer_rows'])},
              'examples': examples}
    if a.out:
        a.out.write_text(json.dumps(report, indent=1, default=str), encoding='utf-8')
    for sig, c in report['by_legacy_signature'].items():
        print(f'{sig:14} importer rows {c["importer_rows"]:7}  same {c.get("elements_same", 0):7}  '
              f'differ {c.get("elements_differ", 0):6}  ' + '  '.join(f'{k} {v}' for k, v in c.items()
                                                                     if k not in ('importer_rows', 'elements_same', 'elements_differ')))


if __name__ == '__main__':
    main()
