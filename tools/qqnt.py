"""Shared helpers for QQNT nt_msg.db files: protobuf codec, vendor header, SQLCipher connection and row checks.

A QQNT nt_msg.db starts with a 1024-byte vendor header. Without it the file is a standard SQLCipher 4 database
(page 4096, kdf_iter 4000, HMAC_SHA1, PBKDF2_HMAC_SHA512). The tools never open the live file: they copy the body
into a temporary folder, work there, and write a new vendor file with the original header put back.

Keys are read from an environment variable whose name the caller passes (never from the command line).
"""
import collections
import contextlib
import os
import shutil
import tempfile
from pathlib import Path

HEADER_BYTES = 1024
MSG_TABLES = ('c2c_msg_table', 'group_msg_table')
PRAGMAS = ('PRAGMA cipher_page_size=4096', 'PRAGMA kdf_iter=4000',
           'PRAGMA cipher_hmac_algorithm=HMAC_SHA1', 'PRAGMA cipher_kdf_algorithm=PBKDF2_HMAC_SHA512')


# --- protobuf -------------------------------------------------------------------------------------------------

def _rv(data, i):
    value = shift = 0
    while True:
        b = data[i]
        i += 1
        value |= (b & 0x7F) << shift
        shift += 7
        if b < 0x80:
            return value, i


def _ev(value):
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def parse(data):
    """Decode one protobuf level into [(field, wire, value)]; value is int (wire 0) or bytes."""
    i, out = 0, []
    data = data or b''
    while i < len(data):
        tag, i = _rv(data, i)
        field, wire = tag >> 3, tag & 7
        if wire == 0:
            value, i = _rv(data, i)
        elif wire == 2:
            n, i = _rv(data, i)
            value = bytes(data[i:i + n])
            i += n
        elif wire == 1:
            value = bytes(data[i:i + 8])
            i += 8
        elif wire == 5:
            value = bytes(data[i:i + 4])
            i += 4
        else:
            raise ValueError(f'unsupported protobuf wire type {wire}')
        out.append((field, wire, value))
    return out


def enc(fields):
    """Encode [(field, wire, value)] back to bytes (inverse of parse)."""
    out = bytearray()
    for field, wire, value in fields:
        out += _ev((field << 3) | wire)
        if wire == 0:
            out += _ev(int(value))
        elif wire == 2:
            raw = bytes(value)
            out += _ev(len(raw)) + raw
        elif wire in (1, 5):
            out += bytes(value)
        else:
            raise ValueError(f'unsupported protobuf wire type {wire}')
    return bytes(out)


def elements(blob):
    """The message elements of a 40800 blob, each as a dict {field: value} (last value wins)."""
    return [{a: x for a, _w, x in parse(pl)} for f, _w, pl in parse(blob) if f == 40800]


# --- files ----------------------------------------------------------------------------------------------------

def key_from_env(name):
    try:
        return os.environ[name]
    except KeyError:
        raise SystemExit(f'environment variable {name} is not set (it must hold the database key)')


def strip(src, tmp, name='body.sqlite', allow_wal=False):
    """Copy src without its vendor header into tmp; return (body path, header bytes).

    A non-empty -wal/-journal means QQ is still running or did not close cleanly: refuse, unless allow_wal, in which
    case the -wal is copied next to the body so SQLite replays it on open.
    """
    src = Path(src)
    for suffix in ('-wal', '-journal'):
        side = Path(str(src) + suffix)
        if side.exists() and side.stat().st_size:
            if allow_wal and suffix == '-wal':
                continue
            raise SystemExit(f'{src}: {suffix} is not empty; quit QQ from the tray first')
    body = Path(tmp) / name
    with open(src, 'rb') as f, open(body, 'wb') as o:
        header = f.read(HEADER_BYTES)
        if not header.startswith(b'SQLite header'):
            raise SystemExit(f'{src}: no QQNT vendor header')
        shutil.copyfileobj(f, o, 1 << 22)
    wal = Path(str(src) + '-wal')
    if allow_wal and wal.exists() and wal.stat().st_size:
        shutil.copy(wal, str(body) + '-wal')
    return body, header


_open = []


@contextlib.contextmanager
def workdir(prefix):
    """A temporary folder for the stripped copies. On exit (also after an error) every connection opened by connect()
    is closed first, so the folder can be removed on Windows."""
    with tempfile.TemporaryDirectory(prefix=prefix) as tmp:
        try:
            yield tmp
        finally:
            while _open:
                _open.pop().close()


def connect(body, key, readonly=False):
    from sqlcipher3 import dbapi2 as sqlcipher
    if readonly:
        c = sqlcipher.connect(f'file:{Path(body).as_posix()}?mode=ro', uri=True)
    else:
        c = sqlcipher.connect(str(body), isolation_level=None)
    _open.append(c)
    c.execute("PRAGMA key='" + key.replace("'", "''") + "'")
    for p in PRAGMAS:
        c.execute(p)
    c.execute('PRAGMA query_only=ON' if readonly else 'PRAGMA journal_mode=DELETE')
    c.execute('SELECT count(*) FROM sqlite_master').fetchone()
    return c


def new_path(value):
    """argparse type for output files: the file must not exist yet (nothing is ever overwritten)."""
    p = Path(value)
    if p.exists():
        raise SystemExit(f'{p} already exists; choose a new output file')
    return p


def package(body, header, out):
    """Write header + body to out. out must not exist yet; the body must have no pending -wal/-journal."""
    for suffix in ('-wal', '-journal'):
        side = Path(str(body) + suffix)
        if side.exists() and side.stat().st_size:
            raise SystemExit(f'{body}{suffix} is not empty; close the connection first')
    with open(out, 'xb') as o, open(body, 'rb') as b:
        o.write(header)
        shutil.copyfileobj(b, o, 1 << 22)


def picture_index(nt_data):
    """Original pictures in <nt_data>/Pic/<YYYY-MM>/Ori: {md5 hex: {(month, size), ...}}; files are named by md5."""
    import re
    index = {}
    for f in Path(nt_data).glob('Pic/*/Ori/*'):
        m = re.match(r'(?i)^([0-9a-f]{32})(?:\.|$)', f.name)
        if m and f.is_file():
            index.setdefault(m.group(1).lower(), set()).add((f.parent.parent.name, f.stat().st_size))
    return index


# --- checks ---------------------------------------------------------------------------------------------------

def user_tables(db):
    return [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]


def snapshot(db, tables, keep=None):
    """Per table, a multiset of repr(rowid, *row). keep(table, row) -> bool can leave rows out."""
    snap = {}
    for t in tables:
        rows = db.execute(f'SELECT rowid,* FROM "{t}"')
        snap[t] = collections.Counter(repr(r) for r in rows if keep is None or keep(t, r))
    return snap


def check_integrity(db):
    result = db.execute('PRAGMA integrity_check').fetchone()[0]
    if result != 'ok':
        raise SystemExit(f'integrity_check failed: {result}')


def check_only_added(before, db, added):
    """Every row in before is unchanged; each table gained exactly added.get(table, 0) rows."""
    for t, old in before.items():
        now = collections.Counter(repr(r) for r in db.execute(f'SELECT rowid,* FROM "{t}"'))
        if old - now:
            raise SystemExit(f'{t}: an original row changed')
        if sum((now - old).values()) != added.get(t, 0):
            raise SystemExit(f'{t}: unexpected number of new rows')


GROUP_SEQ_DUPLICATES = ('SELECT "40027","40003",count(*) FROM group_msg_table WHERE "40003">0 '
                        'GROUP BY 1,2 HAVING count(*)>1 ORDER BY 1,2')
