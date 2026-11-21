"""Compare two nt_data listings from list_media.py (e.g. Windows and Mac) and write the copy list.

Usage: python compare_media.py <a.tsv> --a-name windows <b.tsv> --b-name mac --out <dir>

Prints counts and sizes per media folder (Pic/Ori, Pic/Thumb, Video/Ori, ...). Writes to <dir>:
  media_report.json      the same numbers
  <a>_only.tsv / <b>_only.tsv / size_differs.tsv
  copy_<a>_to_<b>.txt    relative paths of message media (Pic, Video, Ptt originals and thumbnails, File) that only
                         <a> has; copy them under the same relative path without overwriting existing files,
                         e.g. tar -k, robocopy /XC /XN /XO, or rsync --ignore-existing.
Temporary folders (OriTemp, ThumbTemp, Temp, ...) are counted but never put on the copy list.
"""
import argparse
import collections
import json
import re
from pathlib import Path

MEDIA = re.compile(r'^(Pic/[^/]+/(Ori|Thumb)/|Video/[^/]+/(Ori|Thumb)/|Ptt/[^/]+/Ori/|File/)')


def read(path):
    out = {}
    for line in open(path, encoding='utf-8'):
        rel, _, size = line.rstrip('\n').rpartition('\t')
        if rel and size.isdigit():
            out[rel.replace('\\', '/')] = int(size)
    return out


def group(rel):
    parts = rel.split('/')
    if parts[0] in ('Pic', 'Video', 'Ptt') and len(parts) > 3:     # Pic/<month>/Ori/<file>
        return parts[0] + '/' + parts[2]
    return parts[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('a', type=Path)
    ap.add_argument('--a-name', required=True)
    ap.add_argument('b', type=Path)
    ap.add_argument('--b-name', required=True)
    ap.add_argument('--out', required=True, type=Path)
    x = ap.parse_args()
    a, b, na, nb = read(x.a), read(x.b), x.a_name, x.b_name
    x.out.mkdir(parents=True, exist_ok=True)
    res = collections.defaultdict(collections.Counter)
    lists = collections.defaultdict(list)
    copy = []
    for rel in sorted(a.keys() | b.keys()):
        g = group(rel)
        if rel in a and rel in b:
            k = 'same' if a[rel] == b[rel] else 'size_differs'
        else:
            k = f'{na}_only' if rel in a else f'{nb}_only'
            res[g][k + '_bytes'] += a.get(rel, b.get(rel, 0))
        res[g][k] += 1
        if k != 'same':
            lists[k].append(f'{rel}\t{a.get(rel, "")}\t{b.get(rel, "")}')
        if k == f'{na}_only' and MEDIA.match(rel):
            copy.append(rel)
    for k, v in lists.items():
        (x.out / f'{k}.tsv').write_text('\n'.join(v) + '\n', encoding='utf-8')
    (x.out / f'copy_{na}_to_{nb}.txt').write_text('\n'.join(copy) + ('\n' if copy else ''), encoding='utf-8')
    (x.out / 'media_report.json').write_text(json.dumps(res, indent=1), encoding='utf-8')
    mb = lambda n: f'{n / 2**20:9.1f} MB'
    for g in sorted(res):
        c = res[g]
        print(f'{g:16} same {c["same"]:7}  size differs {c["size_differs"]:5}  '
              f'{na} only {c[na + "_only"]:7} ({mb(c[na + "_only_bytes"])})  {nb} only {c[nb + "_only"]:7} ({mb(c[nb + "_only_bytes"])})')
    print(f'copy list: {len(copy)} files, {sum(a[r] for r in copy) / 2**30:.2f} GB')


if __name__ == '__main__':
    main()
