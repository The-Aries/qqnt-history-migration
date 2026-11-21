"""Show pictures again that QQNT marked as expired although the original is on disk (copy of nt_msg.db).

Usage: python unexpire_pictures.py <nt_msg.db> --key-env QQ_KEY --nt-data <account nt_data> --tz Asia/Shanghai
                                   --out <new nt_msg.db>

After a failed download QQNT adds field 45507 to the picture element and sets 45509 to 1. When the original exists
in <nt_data>/Pic/<month of the message>/Ori/<md5>.* with the recorded size, the element gets 45507 removed and
45509 set back to 0. Nothing else changes; every other row stays identical.
"""
import argparse
import collections
import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import qqnt


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('db', type=Path)
    ap.add_argument('--key-env', required=True)
    ap.add_argument('--nt-data', required=True, type=Path)
    ap.add_argument('--tz', required=True)
    ap.add_argument('--out', required=True, type=qqnt.new_path)
    a = ap.parse_args()
    tz, index = ZoneInfo(a.tz), qqnt.picture_index(a.nt_data)
    status = collections.Counter()
    with qqnt.workdir('qqnt-unexpire-') as tmp:
        body, header = qqnt.strip(a.db, tmp)
        db = qqnt.connect(body, qqnt.key_from_env(a.key_env))
        qqnt.check_integrity(db)
        tables = qqnt.user_tables(db)
        before = qqnt.snapshot(db, tables)
        changed = {}
        for t in qqnt.MSG_TABLES:
            for mid, ts, blob in db.execute(f'SELECT "40001","40050","40800" FROM {t}').fetchall():
                outer, hit = [], False
                for f, w, pl in qqnt.parse(blob):
                    if f == 40800:
                        fields = qqnt.parse(pl)
                        v = {x: y for x, _w, y in fields}
                        if v.get(45002) == 2 and v.get(45509) == 1:
                            month = dt.datetime.fromtimestamp(ts, tz).strftime('%Y-%m')
                            if (month, v.get(45405)) in index.get((v.get(45406) or b'').hex(), ()):
                                pl = qqnt.enc([(x, ww, 0 if x == 45509 else y) for x, ww, y in fields if x != 45507])
                                hit = True
                                status['picture_shown_again'] += 1
                            else:
                                status['picture_still_expired_no_original'] += 1
                    outer.append((f, w, pl))
                if hit:
                    changed.setdefault(t, []).append((qqnt.enc(outer), mid))
        db.execute('BEGIN IMMEDIATE')
        for t, ups in changed.items():
            db.executemany(f'UPDATE {t} SET "40800"=? WHERE "40001"=?', ups)
        db.execute('COMMIT')
        qqnt.check_integrity(db)
        for t in tables:
            now = qqnt.snapshot(db, [t])[t]
            n = len(changed.get(t, ()))
            if sum((before[t] - now).values()) != n or sum((now - before[t]).values()) != n:
                raise SystemExit(f'{t}: unexpected changes')
        db.close()
        qqnt.package(body, header, a.out)
    print(json.dumps({'rows_changed': {t: len(v) for t, v in changed.items()}, **status}, indent=1))


if __name__ == '__main__':
    main()
