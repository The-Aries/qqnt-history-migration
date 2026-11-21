"""Find the legacy PC QQ records that QQNT does not have yet (read-only).

Usage: python legacy_reconcile.py --legacy <Msg3.0 plain.db> [...] --qqnt <nt_msg.db> --key-env QQ_KEY
                                  --out <reconcile.sqlite> [--exclude <list.tsv>] [--max-dt 300]

--legacy takes one or more DECRYPTED Msg3.0.db files (e.g. the live one and an older backup); identical records
(same conversation, Rand and Time) are counted once. --exclude takes conversations to leave out, in the list format
of purge_conversations.py (c2c|group TAB number).

Pairing is one to one within the same (conversation, Rand): exact time first, then the nearest QQNT row within
--max-dt seconds. Output tables:
  pairs    legacy records that QQNT already has, with the QQNT msgId (input of legacy_check.py)
  missing  legacy records without a partner, with the real group sequence where one exists (input of legacy_write.py)
Both hold the legacy content, so the file contains message text: keep it private and delete it when done.
"""
import argparse
import collections
import contextlib
import sqlite3
from pathlib import Path

import legacy
import qqnt

SCHEMA = '''
CREATE TABLE missing (qtable TEXT, peer TEXT, rand INTEGER, time INTEGER, sender INTEGER, content BLOB, info BLOB,
                      legacy_table TEXT, seq INTEGER, conv_in_qqnt INTEGER, rand_in_qqnt INTEGER,
                      PRIMARY KEY (qtable, peer, rand, time));
CREATE TABLE pairs (qtable TEXT, peer TEXT, rand INTEGER, time INTEGER, sender INTEGER, content BLOB, info BLOB,
                    legacy_table TEXT, msgid INTEGER, dt INTEGER);
'''


def read_exclude(path):
    out = set()
    if path:
        for line in Path(path).read_text(encoding='utf-8').splitlines():
            if line.strip() and not line.startswith('#'):
                kind, peer = line.split('\t')[:2]
                out.add((legacy.C2C if kind == 'c2c' else legacy.GROUP, peer.strip()))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--legacy', required=True, nargs='+', type=Path)
    ap.add_argument('--qqnt', required=True, type=Path)
    ap.add_argument('--key-env', required=True)
    ap.add_argument('--out', required=True, type=qqnt.new_path)
    ap.add_argument('--exclude', type=Path)
    ap.add_argument('--max-dt', type=int, default=300)
    a = ap.parse_args()
    exclude = read_exclude(a.exclude)
    q = collections.defaultdict(list)            # (table, peer, rand) -> [(time, msgid)]
    qconv = set()
    with qqnt.workdir('qqnt-reconcile-') as tmp:
        body, _ = qqnt.strip(a.qqnt, tmp)
        with contextlib.closing(qqnt.connect(body, qqnt.key_from_env(a.key_env), readonly=True)) as db:
            for t in qqnt.MSG_TABLES:
                for mid, peer, rnd, ts in db.execute(f'SELECT "40001","40030","40002","40050" FROM {t}'):
                    q[(t, str(peer), int(rnd or 0) & 0xFFFFFFFF)].append((int(ts or 0), mid))
                    qconv.add((t, str(peer)))
    old = collections.defaultdict(dict)          # (table, peer, rand) -> {time: record}
    stats = collections.Counter()
    for path in a.legacy:
        src = sqlite3.connect(f'file:{path.as_posix()}?mode=ro&immutable=1', uri=True)
        for name, t, peer in legacy.legacy_tables(src):
            if (t, peer) in exclude:
                stats['legacy_rows_excluded'] += src.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
                continue
            for ts, rnd, snd, content, info in src.execute(f'SELECT Time, Rand, SenderUin, MsgContent, Info FROM "{name}"'):
                stats['legacy_rows_read'] += 1
                k, ts = (t, peer, int(rnd or 0) & 0xFFFFFFFF), int(ts or 0)
                if ts in old[k]:
                    stats['legacy_duplicates'] += 1
                    continue
                seq = legacy.real_group_seq(src, name, info, ts, k[2]) if t == legacy.GROUP else None
                old[k][ts] = (snd, content, info, name, seq)
        src.close()
    out = sqlite3.connect(a.out)
    out.executescript(SCHEMA)
    for k, rows in old.items():
        cand = list(q.get(k, ()))
        left = []
        for ts in sorted(rows):
            hit = next((c for c in cand if c[0] == ts), None)
            if hit:
                cand.remove(hit)
                out.execute('INSERT INTO pairs VALUES (?,?,?,?,?,?,?,?,?,0)', (*k, ts, *rows[ts][:4], hit[1]))
                stats['paired_exact'] += 1
            else:
                left.append(ts)
        used_o, used_q = set(), set()
        for d, ts, c in sorted((abs(ts - c[0]), ts, c) for ts in left for c in cand if abs(ts - c[0]) <= a.max_dt):
            if ts in used_o or c in used_q:
                continue
            used_o.add(ts)
            used_q.add(c)
            out.execute('INSERT INTO pairs VALUES (?,?,?,?,?,?,?,?,?,?)', (*k, ts, *rows[ts][:4], c[1], c[0] - ts))
            stats['paired_near'] += 1
        for ts in left:
            if ts not in used_o:
                snd, content, info, name, seq = rows[ts]
                out.execute('INSERT INTO missing VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                            (*k, ts, snd, content, info, name, seq, int(k[:2] in qconv), int(k in q)))
                stats['missing'] += 1
    out.commit()
    print(dict(stats))
    for row in out.execute('SELECT qtable, count(*), count(DISTINCT peer), sum(conv_in_qqnt=0), sum(qtable=? AND seq IS NULL) '
                           'FROM missing GROUP BY qtable', (legacy.GROUP,)):
        print('{}: missing {}, conversations {}, in conversations QQNT lacks {}, group rows without a real sequence {}'.format(*row))
    print('conversations with the most missing records (a conversation deleted on purpose comes back unless it is '
          'passed with --exclude):')
    for t, peer, n, inq in out.execute('SELECT qtable, peer, count(*), max(conv_in_qqnt) FROM missing '
                                       'GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 10'):
        print(f'  {"c2c" if t == legacy.C2C else "group"}\t{peer}\t{n}\t{"in QQNT" if inq else "not in QQNT"}')
    out.close()


if __name__ == '__main__':
    main()
