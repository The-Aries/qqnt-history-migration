"""Add the nt_uid_mapping_table rows that private chats need, in a copy of a QQNT nt_msg.db.

Usage: python fix_uid_mapping.py <nt_msg.db> --key-env QQ_KEY --out <new nt_msg.db>

Every private-chat slot 40027 must have a mapping row, or the client opens that chat empty. A missing row is added
as QQ writes it: 48901 = slot, 48902 = the conversation's uid 40021, 48912 = NULL, 1002 = peer 40030. The table's
sqlite_sequence is raised to the highest slot. No other row changes.
"""
import argparse
import json
from pathlib import Path

import qqnt


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('db', type=Path)
    ap.add_argument('--key-env', required=True)
    ap.add_argument('--out', required=True, type=qqnt.new_path)
    a = ap.parse_args()
    with qqnt.workdir('qqnt-uidmap-') as tmp:
        body, header = qqnt.strip(a.db, tmp)
        db = qqnt.connect(body, qqnt.key_from_env(a.key_env))
        qqnt.check_integrity(db)
        before = qqnt.snapshot(db, qqnt.user_tables(db))
        seq_before = db.execute('SELECT name, seq FROM sqlite_sequence ORDER BY name').fetchall()
        mapped = {sl: str(u) for sl, u in db.execute('SELECT "48901","48902" FROM nt_uid_mapping_table')}
        mapped_uids = set(mapped.values())
        need = []
        for slot, uid, peer, n in db.execute('SELECT "40027","40021","40030",count(*) FROM c2c_msg_table GROUP BY 1,2,3'):
            if slot in mapped and mapped[slot] != str(uid):
                raise SystemExit(f'slot {slot}: mapped to another uid than its messages carry')
            if slot not in mapped:
                if str(uid) in mapped_uids:
                    raise SystemExit(f'slot {slot}: its uid is already mapped to another slot')
                need.append((slot, str(uid), int(peer), n))
        if len({x[0] for x in need}) != len(need):
            raise SystemExit('a slot has messages with two different uids')
        db.execute('BEGIN IMMEDIATE')
        db.executemany('INSERT INTO nt_uid_mapping_table("48901","48902","48912","1002") VALUES (?,?,NULL,?)',
                       [(s, u, p) for s, u, p, _ in need])
        top = db.execute('SELECT max("48901") FROM nt_uid_mapping_table').fetchone()[0]
        db.execute("UPDATE sqlite_sequence SET seq=? WHERE name='nt_uid_mapping_table' AND seq<?", (top, top))
        db.execute('COMMIT')
        qqnt.check_integrity(db)
        qqnt.check_only_added(before, db, {'nt_uid_mapping_table': len(need)})
        seq_after = db.execute('SELECT name, seq FROM sqlite_sequence ORDER BY name').fetchall()
        if [x for x in seq_after if x[0] != 'nt_uid_mapping_table'] != [x for x in seq_before if x[0] != 'nt_uid_mapping_table']:
            raise SystemExit('another sqlite_sequence entry changed')
        unmapped = db.execute('SELECT count(DISTINCT "40027") FROM c2c_msg_table '
                              'WHERE "40027" NOT IN (SELECT "48901" FROM nt_uid_mapping_table)').fetchone()[0]
        db.close()
        qqnt.package(body, header, a.out)
    print(json.dumps({'mapping_rows_added': len(need), 'message_rows_covered': sum(x[3] for x in need),
                      'sequence': dict(seq_after).get('nt_uid_mapping_table'), 'slots_still_unmapped': unmapped}, indent=1))


if __name__ == '__main__':
    main()
