"""Write the missing legacy records found by legacy_reconcile.py into a copy of a QQNT nt_msg.db.

Usage: python legacy_write.py --reconcile <reconcile.sqlite> --qqnt <nt_msg.db> --key-env QQ_KEY --self <QQ number>
                              --tz Asia/Shanghai --out <new nt_msg.db>
                              [--nt-data <account nt_data>] [--kinds content,gray_tip,market_face,ark,text]
                              [--recall-notices skip|write] [--only <list.tsv>] [--write-possible-duplicates]

Rules:
  * only conversations QQNT already has; others are reported (conversation_not_in_qqnt). A conversation deleted on
    purpose still "exists" while new messages arrive in it: leave it out with legacy_reconcile.py --exclude;
  * a record whose Rand already exists in that QQNT conversation (more than --max-dt seconds apart) is most likely
    the same message: skipped as possible_duplicate unless --write-possible-duplicates;
  * group rows only with their real sequence (Info or a unique hit in the legacy Seq table) that is not used yet in
    that group; never borrowed from a neighbour, because the client shows one row per sequence;
  * --kinds limits what is written: content = elements QQNT has a native form for, gray_tip = local notices,
    market_face, ark = app cards with JSON, text = cards rendered as the text old PC QQ displayed;
  * recall notices from system account 10000 are skipped by default, as the importer does; --recall-notices write
    stores them as gray tips;
  * with --nt-data, pictures whose original is not in <nt_data>/Pic/<month>/Ori are marked expired, as QQNT does
    after a failed download; without it, every picture is written as downloadable;
  * --tz is the time zone of the computer that runs QQ (40058 = local midnight of the message day).
Every existing row must stay identical, integrity_check must pass and no group sequence may collide.
Writes <out>.report.json with counts per reason and <out>.samples.tsv (table, peer, msgId, time, kind) for checking a
few new rows in the client.
"""
import argparse
import collections
import datetime as dt
import json
import sqlite3
from pathlib import Path

import legacy
import qqnt

KINDS = ('content', 'gray_tip', 'market_face', 'ark', 'text')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--reconcile', required=True, type=Path)
    ap.add_argument('--qqnt', required=True, type=Path)
    ap.add_argument('--key-env', required=True)
    ap.add_argument('--self', required=True, type=int, dest='me')
    ap.add_argument('--tz', required=True)
    ap.add_argument('--out', required=True, type=qqnt.new_path)
    ap.add_argument('--nt-data', type=Path)
    ap.add_argument('--kinds', default=','.join(KINDS))
    ap.add_argument('--recall-notices', choices=('skip', 'write'), default='skip')
    ap.add_argument('--only', type=Path, help='write only these conversations (purge list format)')
    ap.add_argument('--write-possible-duplicates', action='store_true',
                    help='also write records whose Rand already exists in that QQNT conversation at another time')
    a = ap.parse_args()
    kinds = set(a.kinds.split(','))
    if kinds - set(KINDS):
        raise SystemExit(f'unknown kind in --kinds: {sorted(kinds - set(KINDS))}')
    only = None
    if a.only:
        only = {(legacy.C2C if l.split('\t')[0] == 'c2c' else legacy.GROUP, l.split('\t')[1].strip())
                for l in a.only.read_text(encoding='utf-8').splitlines() if l.strip() and not l.startswith('#')}
    pics = None
    if a.nt_data:
        index = qqnt.picture_index(a.nt_data)
        pics = lambda md5, size, month: (month, size) in index.get(md5, ())
    status, per_conv = collections.Counter(), collections.Counter()
    rows, samples = collections.defaultdict(list), []
    rec_db = sqlite3.connect(f'file:{a.reconcile.as_posix()}?mode=ro', uri=True)
    with qqnt.workdir('qqnt-legacy-') as tmp:
        body, header = qqnt.strip(a.qqnt, tmp)
        db = qqnt.connect(body, qqnt.key_from_env(a.key_env))
        qqnt.check_integrity(db)
        before = qqnt.snapshot(db, qqnt.user_tables(db))
        dups_before = db.execute(qqnt.GROUP_SEQ_DUPLICATES).fetchall()
        cols = {t: [r[1] for r in db.execute(f'PRAGMA table_info({t})')] for t in qqnt.MSG_TABLES}
        w = legacy.Writer(db, a.me, a.tz)
        for t, peer, rnd, ts, snd, content, info, seq, rand_known in rec_db.execute(
                'SELECT qtable, peer, rand, time, sender, content, info, seq, rand_in_qqnt FROM missing '
                'ORDER BY qtable, peer, time'):
            if only is not None and (t, peer) not in only:
                continue
            if rand_known and not a.write_possible_duplicates:
                status['skipped_possible_duplicate'] += 1
                continue
            if a.recall_notices == 'skip' and legacy.is_recall_notice(snd, content):
                status['skipped_recall_notice'] += 1
                continue
            rec = {'time': ts, 'rand': rnd, 'sender': snd, 'content': content, 'info': info}
            try:
                r, kind = w.row(t, peer, rec, seq=seq, picture_exists=pics)
            except legacy.Unsupported as e:
                status['not_written_' + str(e)] += 1
                continue
            if kind not in kinds:
                status['not_written_kind_' + kind] += 1
                continue
            rows[t].append(tuple([r['40001']] + [r[c] for c in cols[t]]))
            status['written_' + kind] += 1
            per_conv[(t, peer, kind)] += 1
            if per_conv[(t, peer, kind)] == 1 and len(samples) < 200:
                samples.append((t, peer, r['40001'], dt.datetime.fromtimestamp(ts, w.tz).isoformat(), kind))
        rec_db.close()
        db.execute('BEGIN IMMEDIATE')
        for t, batch in rows.items():
            names = ','.join(f'"{c}"' for c in cols[t])
            db.executemany(f'INSERT INTO {t}(rowid,{names}) VALUES ({",".join("?" * (len(cols[t]) + 1))})', batch)
        db.execute('COMMIT')
        qqnt.check_integrity(db)
        qqnt.check_only_added(before, db, {t: len(v) for t, v in rows.items()})
        if db.execute(qqnt.GROUP_SEQ_DUPLICATES).fetchall() != dups_before:
            raise SystemExit('a group sequence now collides')
        counts = {t: db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in qqnt.MSG_TABLES}
        db.close()
        qqnt.package(body, header, a.out)
    report = {'written': {t: len(v) for t, v in rows.items()}, 'status': dict(sorted(status.items())),
              'counts_after': counts, 'size': a.out.stat().st_size}
    Path(str(a.out) + '.report.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    Path(str(a.out) + '.samples.tsv').write_text(
        'table\tpeer\tmsgid\ttime\tkind\n' + ''.join('\t'.join(map(str, s)) + '\n' for s in samples), encoding='utf-8')
    print(json.dumps(report, indent=1))


if __name__ == '__main__':
    main()
