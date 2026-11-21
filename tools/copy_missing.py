"""Insert the messages that one device has and another lacks into a copy of the other device's nt_msg.db.

Usage:
  python copy_missing.py --src <nt_msg.db> --src-key-env A_KEY --dst <nt_msg.db> --dst-key-env B_KEY --out <new nt_msg.db>
                         [--src-data <src nt_data folder> --dst-data <dst nt_data folder>]

Both files belong to the same account. The destination copy is opened with the destination key and written in place
(no re-encryption); its original vendor header is put back. Rules:
  * rows are matched on (peer 40030, time 40050, random 40002); a missing row keeps its source msgId, unless that msgId
    is already used on the destination (then it is skipped and counted);
  * private chats: slot 40027 = the destination's slot for that peer (from its messages, else from its
    nt_uid_mapping_table row), or a new slot above every slot in use; the unique key (40027, 40002, 40005) is kept
    by bumping 40005 on a clash. New slots also need nt_uid_mapping_table rows: run fix_uid_mapping.py on the result;
  * groups: a row is skipped when its sequence 40003 is already used in that group on the destination;
  * element field 45812 (local file path, written by each client for itself): a path under --src-data is rewritten to
    the same relative path under --dst-data; any other 45812 value is dropped.
Every original destination row must stay identical, integrity_check must pass and no group sequence may collide.
The media files themselves are not copied; see list_media.py and compare_media.py.
"""
import argparse
import collections
import json
from pathlib import Path

import qqnt


class PathMap:
    def __init__(self, src, dst):
        self.src = self.dst = None
        if src and dst:
            self.sep = '/' if '/' in dst and '\\' not in dst else '\\'
            self.src = src.rstrip('/\\').encode('utf-8')
            self.dst = dst.rstrip('/\\').encode('utf-8')

    def __call__(self, value, stats):
        if self.src and value[:len(self.src)] == self.src and value[len(self.src):len(self.src) + 1] in (b'/', b'\\'):
            rest = value[len(self.src):].replace(b'\\', b'/').replace(b'/', self.sep.encode())
            stats['45812_rewritten'] += 1
            return self.dst + rest
        stats['45812_dropped'] += 1
        return None


def fix_paths(blob, pmap, stats):
    out = []
    for f, w, pl in qqnt.parse(blob):
        if f == 40800:
            fields = []
            for a, ww, x in qqnt.parse(pl):
                if a == 45812 and isinstance(x, bytes) and x:
                    x = pmap(x, stats)
                    if x is None:
                        continue
                fields.append((a, ww, x))
            pl = qqnt.enc(fields)
        out.append((f, w, pl))
    return qqnt.enc(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--src', required=True, type=Path)
    ap.add_argument('--src-key-env', required=True)
    ap.add_argument('--dst', required=True, type=Path)
    ap.add_argument('--dst-key-env', required=True)
    ap.add_argument('--out', required=True, type=qqnt.new_path)
    ap.add_argument('--src-data', help='nt_data folder of the source account, as written in 45812')
    ap.add_argument('--dst-data', help='nt_data folder of the destination account')
    a = ap.parse_args()
    pmap = PathMap(a.src_data, a.dst_data)
    stats, per, inserts = collections.Counter(), collections.Counter(), collections.defaultdict(list)
    with qqnt.workdir('qqnt-copy-') as tmp:
        sbody, _ = qqnt.strip(a.src, tmp, name='src.sqlite')
        dbody, header = qqnt.strip(a.dst, tmp, name='dst.sqlite')
        s = qqnt.connect(sbody, qqnt.key_from_env(a.src_key_env), readonly=True)
        d = qqnt.connect(dbody, qqnt.key_from_env(a.dst_key_env))
        qqnt.check_integrity(d)
        before = qqnt.snapshot(d, qqnt.user_tables(d))
        dups_before = d.execute(qqnt.GROUP_SEQ_DUPLICATES).fetchall()
        for t in qqnt.MSG_TABLES:
            scols = [r[1] for r in s.execute(f'PRAGMA table_info({t})')]
            dcols = [r[1] for r in d.execute(f'PRAGMA table_info({t})')]
            if set(scols) != set(dcols):
                raise SystemExit(f'{t}: the two databases have different columns')
            have = {(int(p or 0), int(ts or 0), int(r or 0) & 0xffffffff)
                    for p, ts, r in d.execute(f'SELECT "40030","40050","40002" FROM {t}')}
            mids = {r[0] for r in d.execute(f'SELECT "40001" FROM {t}')}
            if t == 'c2c_msg_table':
                slot = {}
                for p, sl, _n in d.execute('SELECT "40030","40027",count(*) FROM c2c_msg_table GROUP BY 1,2 ORDER BY 3'):
                    slot[int(p or 0)] = sl
                # a peer without messages may still have a mapping row (uid -> slot); reuse that slot
                mapped = {str(u): sl for sl, u in d.execute('SELECT "48901","48902" FROM nt_uid_mapping_table')}
                next_slot = 1 + max(d.execute('SELECT max("40027") FROM c2c_msg_table').fetchone()[0] or 0,
                                    d.execute('SELECT max("48901") FROM nt_uid_mapping_table').fetchone()[0] or 0,
                                    d.execute("SELECT max(seq) FROM sqlite_sequence WHERE name='nt_uid_mapping_table'").fetchone()[0] or 0)
                used = set(d.execute('SELECT "40027","40002","40005" FROM c2c_msg_table'))
            else:
                seqs = set(d.execute('SELECT "40027","40003" FROM group_msg_table WHERE "40003">0'))
            for row in s.execute(f'SELECT * FROM {t}'):
                r = dict(zip(scols, row))
                k = (int(r['40030'] or 0), int(r['40050'] or 0), int(r['40002'] or 0) & 0xffffffff)
                if k in have:
                    continue
                if r['40001'] in mids:
                    stats[f'{t}_skipped_msgid_used'] += 1
                    continue
                if t == 'c2c_msg_table':
                    if k[0] not in slot:
                        if str(r['40021']) in mapped:
                            slot[k[0]] = mapped[str(r['40021'])]
                            stats['private_slots_from_mapping'] += 1
                        else:
                            slot[k[0]] = next_slot
                            next_slot += 1
                            stats['new_private_slots'] += 1
                    r['40027'] = slot[k[0]]
                    while (r['40027'], r['40002'], r['40005']) in used:
                        r['40005'] = 1 + (r['40005'] or 0) % 65535
                    used.add((r['40027'], r['40002'], r['40005']))
                elif r['40003'] and r['40003'] > 0:
                    if (r['40027'], r['40003']) in seqs:
                        stats['group_skipped_seq_taken'] += 1
                        continue
                    seqs.add((r['40027'], r['40003']))
                r['40800'] = fix_paths(r['40800'], pmap, stats)
                mids.add(r['40001'])
                inserts[t].append(tuple([r['40001']] + [r[c] for c in dcols]))
                per[(t, k[0])] += 1
            if inserts[t]:
                cols = ','.join(f'"{c}"' for c in dcols)
                d.execute('BEGIN IMMEDIATE')
                d.executemany(f'INSERT INTO {t}(rowid,{cols}) VALUES ({",".join("?" * (len(dcols) + 1))})', inserts[t])
                d.execute('COMMIT')
        qqnt.check_integrity(d)
        qqnt.check_only_added(before, d, {t: len(v) for t, v in inserts.items()})
        if d.execute(qqnt.GROUP_SEQ_DUPLICATES).fetchall() != dups_before:
            raise SystemExit('a group sequence now collides')
        counts = {t: d.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in qqnt.MSG_TABLES}
        s.close()
        d.close()
        qqnt.package(dbody, header, a.out)
    report = {'inserted': {t: len(v) for t, v in inserts.items()}, 'stats': dict(stats), 'counts_after': counts,
              'conversations': len(per), 'size': a.out.stat().st_size}
    a.out.with_suffix('.report.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    print(json.dumps(report, indent=1))


if __name__ == '__main__':
    main()
