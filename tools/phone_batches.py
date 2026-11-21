"""Export to phone in batches. QQ's "export to phone" packs only the 200 most recent private chats, so let the client
see one batch at a time: a temporary database keeps only that batch's messages.

  python phone_batches.py split   <nt_msg.db> --key-env K --out-dir <dir> [--batches 3]
  python phone_batches.py build   <nt_msg.db> --key-env K --batch <batchN.tsv> --out <temp nt_msg.db>
  python phone_batches.py collect <original nt_msg.db> --temp <temp nt_msg.db as QQ left it> --key-env K --out <new_rows.json>
  python phone_batches.py merge   <original nt_msg.db> --key-env K --rows <new_rows.json ...> --out <new nt_msg.db>

split   sorts all conversations by last-message time (newest first) and writes batch1.tsv .. batchN.tsv
        (kind, peer, rows, last-message date). Edit them freely; a batch should hold at most 200 private chats.
build   keeps every table, but removes c2c_msg_table / group_msg_table / group_at_me_msg rows of conversations outside
        the batch. Contacts, mapping table and chat list stay unchanged.
collect after QQ has quit: message rows present in the temporary database but not in the original (by msgId), i.e.
        messages received while the temporary database was live. A -wal left by QQ is read too.
merge   inserts the collected rows into a copy of the original; nothing else changes.

Procedure per batch: quit QQ from the tray, back up the live database, build, put the temporary database in place,
open QQ, export all to the phone and wait until the phone has finished, quit QQ, collect, put the original back,
merge if collect found rows. The new_rows.json files contain message content; delete them when done.
"""
import argparse
import base64
import collections
import datetime as dt
import json
from pathlib import Path

import qqnt


def enc(v):
    return {'b64': base64.b64encode(v).decode()} if isinstance(v, bytes) else v


def dec(v):
    return base64.b64decode(v['b64']) if isinstance(v, dict) else v


def split(a, tmp):
    body, _ = qqnt.strip(a.db, tmp)
    db = qqnt.connect(body, qqnt.key_from_env(a.key_env), readonly=True)
    convs = []
    for t in qqnt.MSG_TABLES:
        for peer, cnt, last in db.execute(f'SELECT "40030",count(*),max("40050") FROM {t} WHERE "40030"<>0 GROUP BY 1'):
            convs.append((last or 0, t, peer, cnt))
    db.close()
    convs.sort(reverse=True)
    a.out_dir.mkdir(parents=True, exist_ok=True)
    size = -(-len(convs) // a.batches)
    for i in range(a.batches):
        part = convs[i * size:(i + 1) * size]
        if not part:
            break
        lines = [f'{"c2c" if t == "c2c_msg_table" else "group"}\t{p}\t{c}\t{dt.datetime.fromtimestamp(l, dt.timezone.utc).date()}'
                 for l, t, p, c in part]
        (a.out_dir / f'batch{i + 1}.tsv').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        private = sum(1 for x in part if x[1] == 'c2c_msg_table')
        print(f'batch{i + 1}: {len(part)} conversations ({private} private, {len(part) - private} groups), '
              f'{sum(x[3] for x in part)} rows, last message {lines[-1].split(chr(9))[3]} .. {lines[0].split(chr(9))[3]}')


def build(a, tmp):
    keep = collections.defaultdict(set)
    for line in a.batch.read_text(encoding='utf-8').splitlines():
        if line.strip() and not line.startswith('#'):
            k, p = line.split('\t')[:2]
            keep['c2c_msg_table' if k == 'c2c' else 'group_msg_table'].add(int(p))
    body, header = qqnt.strip(a.db, tmp)
    db = qqnt.connect(body, qqnt.key_from_env(a.key_env))
    qqnt.check_integrity(db)
    removed = {}
    db.execute('BEGIN IMMEDIATE')
    db.execute('CREATE TEMP TABLE k(p INTEGER PRIMARY KEY)')
    for t in qqnt.MSG_TABLES:
        db.execute('DELETE FROM k')
        db.executemany('INSERT INTO k VALUES (?)', [(p,) for p in keep[t]])
        removed[t] = db.execute(f'DELETE FROM {t} WHERE "40030" NOT IN (SELECT p FROM k)').rowcount
        if t == 'group_msg_table':
            removed['group_at_me_msg'] = db.execute('DELETE FROM group_at_me_msg WHERE "40027" NOT IN (SELECT p FROM k)').rowcount
    db.execute('COMMIT')
    qqnt.check_integrity(db)
    left = {t: db.execute(f'SELECT count(*), count(DISTINCT "40030") FROM {t}').fetchone() for t in qqnt.MSG_TABLES}
    db.close()
    qqnt.package(body, header, a.out)
    print(json.dumps({'removed_rows': removed, 'kept_rows_and_conversations': left}))


def collect(a, tmp):
    key = qqnt.key_from_env(a.key_env)
    ob, _ = qqnt.strip(a.db, tmp, name='orig.sqlite')
    tb, _ = qqnt.strip(a.temp, tmp, name='temp.sqlite', allow_wal=True)
    o, t2 = qqnt.connect(ob, key, readonly=True), qqnt.connect(tb, key)
    res = {}
    for t in qqnt.MSG_TABLES:
        have = {r[0] for r in o.execute(f'SELECT "40001" FROM {t}')}
        cols = [r[1] for r in t2.execute(f'PRAGMA table_info({t})')]
        res[t] = {'cols': cols, 'rows': [[enc(v) for v in r] for r in t2.execute(f'SELECT * FROM {t}') if r[0] not in have]}
    o.close()
    t2.close()
    a.out.write_text(json.dumps(res), encoding='utf-8')
    print({t: len(v['rows']) for t, v in res.items()})


def merge(a, tmp):
    body, header = qqnt.strip(a.db, tmp)
    db = qqnt.connect(body, qqnt.key_from_env(a.key_env))
    qqnt.check_integrity(db)
    before = qqnt.snapshot(db, qqnt.user_tables(db))
    added, seen = collections.Counter(), set()
    db.execute('BEGIN IMMEDIATE')
    for f in a.rows:
        for t, v in json.loads(Path(f).read_text(encoding='utf-8')).items():
            if t not in qqnt.MSG_TABLES:
                raise SystemExit(f'{f}: unexpected table {t}')
            cols = ','.join(f'"{c}"' for c in v['cols'])
            for r in v['rows']:
                r = [dec(x) for x in r]
                if (t, r[0]) in seen or db.execute(f'SELECT 1 FROM {t} WHERE "40001"=?', (r[0],)).fetchone():
                    continue
                db.execute(f'INSERT INTO {t}({cols}) VALUES ({",".join("?" * len(r))})', r)
                seen.add((t, r[0]))
                added[t] += 1
    db.execute('COMMIT')
    qqnt.check_integrity(db)
    qqnt.check_only_added(before, db, added)
    db.close()
    qqnt.package(body, header, a.out)
    print(json.dumps({'added': dict(added)}))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='mode', required=True)
    for name in ('split', 'build', 'collect', 'merge'):
        p = sub.add_parser(name)
        p.add_argument('db', type=Path)
        p.add_argument('--key-env', required=True)
    sub.choices['split'].add_argument('--out-dir', required=True, type=Path)
    sub.choices['split'].add_argument('--batches', type=int, default=3)
    sub.choices['build'].add_argument('--batch', required=True, type=Path)
    sub.choices['build'].add_argument('--out', required=True, type=qqnt.new_path)
    sub.choices['collect'].add_argument('--temp', required=True, type=Path)
    sub.choices['collect'].add_argument('--out', required=True, type=qqnt.new_path)
    sub.choices['merge'].add_argument('--rows', required=True, nargs='+', type=Path)
    sub.choices['merge'].add_argument('--out', required=True, type=qqnt.new_path)
    a = ap.parse_args()
    with qqnt.workdir('qqnt-batch-') as tmp:
        {'split': split, 'build': build, 'collect': collect, 'merge': merge}[a.mode](a, tmp)


if __name__ == '__main__':
    main()
