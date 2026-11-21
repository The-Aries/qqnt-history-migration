"""List the files of an nt_data folder as "relative path <TAB> size" (forward slashes), for compare_media.py.

Usage: python list_media.py <nt_data folder> --out <listing.tsv>
Run it on each device; only names and sizes are read.
"""
import argparse
import os
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('root', type=Path)
    ap.add_argument('--out', required=True, type=Path)
    a = ap.parse_args()
    n = 0
    with a.out.open('w', encoding='utf-8', newline='\n') as f:
        for d, _dirs, files in os.walk(a.root):
            for name in files:
                p = Path(d) / name
                f.write(f'{p.relative_to(a.root).as_posix()}\t{p.stat().st_size}\n')
                n += 1
    print(a.out, n, 'files')


if __name__ == '__main__':
    main()
