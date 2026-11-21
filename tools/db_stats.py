"""Integrity check, row counts and conversation counts of a QQNT nt_msg.db (read-only, on a temporary copy).

Usage: python db_stats.py <nt_msg.db> --key-env QQ_KEY
"""
import argparse
import contextlib

import qqnt


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('db')
    ap.add_argument('--key-env', required=True, help='name of the environment variable that holds the key')
    a = ap.parse_args()
    with qqnt.workdir('qqnt-stats-') as tmp:
        body, _ = qqnt.strip(a.db, tmp)
        with contextlib.closing(qqnt.connect(body, qqnt.key_from_env(a.key_env), readonly=True)) as db:
            print('integrity_check', db.execute('PRAGMA integrity_check').fetchone()[0])
            for t in qqnt.MSG_TABLES:
                n, c = db.execute(f'SELECT count(*), count(DISTINCT "40030") FROM {t}').fetchone()
                print(f'{t}: rows {n}, conversations {c}')
            print('recent_contact_v3_table rows', db.execute('SELECT count(*) FROM recent_contact_v3_table').fetchone()[0])


if __name__ == '__main__':
    main()
