"""Delete whole conversations from a copy of a QQNT nt_msg.db (any device), then VACUUM.

Usage: python purge_conversations.py <nt_msg.db> --key-env QQ_KEY --list <list.tsv> --out <new nt_msg.db>

list.tsv: one conversation per line, "c2c" or "group", a TAB, then the peer 40030 (private: QQ number, group: group
number). Further columns and lines starting with # are ignored. For each conversation its message rows and its
recent-contact row (40021 = the conversation's uid or group code) are deleted. Every other row stays identical.
Do not list peer 0 unless you checked those rows: rows with 40030 = 0 are not one conversation; many belong to kept
groups or chats (see their 40021). Listing 0 deletes all of them (recent-contact rows stay).
"""
import argparse
import json
from pathlib import Path

import qqnt


def read_list(path):
    targets = []
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if line.strip() and not line.startswith('#'):
            kind, peer = line.split('\t')[:2]
            if kind not in ('c2c', 'group'):
                raise SystemExit(f'{path}: unknown kind {kind!r}')
            targets.append(('c2c_msg_table' if kind == 'c2c' else 'group_msg_table', int(peer)))
    return targets


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('db', type=Path)
    ap.add_argument('--key-env', required=True)
    ap.add_argument('--list', required=True, type=Path)
    ap.add_argument('--out', required=True, type=qqnt.new_path)
    a = ap.parse_args()
    targets = read_list(a.list)
    with qqnt.workdir('qqnt-purge-') as tmp:
        body, header = qqnt.strip(a.db, tmp)
        db = qqnt.connect(body, qqnt.key_from_env(a.key_env))
        qqnt.check_integrity(db)
        tables = qqnt.user_tables(db)
        recent_keys = set()
        for t, p in targets:
            if p:
                recent_keys |= {str(u) for (u,) in db.execute(f'SELECT DISTINCT "40021" FROM {t} WHERE "40030"=?', (p,))}
        peers = {t: {p for tt, p in targets if tt == t} for t in qqnt.MSG_TABLES}
        cols = {t: ['rowid'] + [r[1] for r in db.execute(f'PRAGMA table_info("{t}")')] for t in tables}

        def keep(t, row):
            r = dict(zip(cols[t], row))
            if t in peers and int(r['40030'] or 0) in peers[t]:
                return False
            return not (t == 'recent_contact_v3_table' and str(r['40021']) in recent_keys)

        expected = qqnt.snapshot(db, tables, keep)
        deleted = {}
        db.execute('BEGIN IMMEDIATE')
        for t, p in targets:
            deleted[f'{t}|{p}'] = db.execute(f'DELETE FROM {t} WHERE "40030"=?', (p,)).rowcount
        deleted['recent_contact_v3_table'] = sum(
            db.execute('DELETE FROM recent_contact_v3_table WHERE "40021"=?', (u,)).rowcount for u in recent_keys)
        db.execute('COMMIT')
        db.execute('VACUUM')
        qqnt.check_integrity(db)
        if qqnt.snapshot(db, tables) != expected:
            raise SystemExit('a row outside the listed conversations changed')
        counts = {t: db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in qqnt.MSG_TABLES + ('recent_contact_v3_table',)}
        db.close()
        qqnt.package(body, header, a.out)
    report = {'deleted': deleted, 'message_rows_deleted': sum(v for k, v in deleted.items() if '|' in k),
              'counts_after': counts, 'size': a.out.stat().st_size}
    a.out.with_suffix('.report.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    print(json.dumps(report, indent=1))


if __name__ == '__main__':
    main()
