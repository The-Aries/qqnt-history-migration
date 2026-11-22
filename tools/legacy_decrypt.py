"""Decrypt a legacy PC QQ Msg3.0.db with its key, using the old client's own codec (Windows only).

Usage: python legacy_decrypt.py --source <copy of Msg3.0.db> --out <plain.db> --key-env PCQQ_KEY
                                --kernelutil <old QQ>\\Bin\\KernelUtil.dll

How it works: legacy PC QQ encrypts every 8192-byte page of Msg3.0.db with its own codec inside KernelUtil.dll. The
tool starts a hidden 32-bit PowerShell as a host process, loads KernelUtil.dll into it with Frida, opens a small seed
copy of the source (header and first page) through the DLL and sets the key on it, which makes the DLL create its
codec. Then it reads the source page
by page, lets the codec decode each page in memory and writes a plain SQLite file (page size 8192) that any SQLite
tool can open. The source is opened read-only and is never changed.

Requirements:
  * Windows with the 32-bit PowerShell (C:\\Windows\\SysWOW64\\WindowsPowerShell\\v1.0\\powershell.exe);
  * pip install frida (16 or 17);
  * KernelUtil.dll of legacy PC QQ 9.7.25.29417 (1,873,456 bytes): the function offsets below belong to exactly this
    build, so any other file is refused;
  * the 16-byte database key as 32 hex characters in the environment variable named by --key-env. How to obtain
    the key is not covered here.

The output contains every message in plain text: keep it on your own computer and delete it when done.
"""
import argparse
import os
import tempfile
from pathlib import Path

PAGE_SIZE = 8192
HEADER_SIZE = 1024
SQLITE_MAGIC = b'SQLite format 3\x00'
KERNELUTIL_SIZE = 1873456          # KernelUtil.dll of PC QQ 9.7.25.29417
HOST = Path(r'C:\Windows\SysWOW64\WindowsPowerShell\v1.0\powershell.exe')

AGENT = r"""
const OFF = {open: 0x365e1, key: 0x86ec3, close: 0x639b7, pagerSetCodec: 0x57645, xCodec: 0x64107};
const PAGE = 8192, HEADER = 1024;
let myThread = -1, pCodec = null;

function exp(mod, name) {
  if (typeof Module.getExportByName === 'function') return Module.getExportByName(mod, name);   // Frida 16
  return Process.getModuleByName(mod).getExportByName(name);                                 // Frida 17
}
function invalid(h) { return h.isNull() || h.equals(ptr('0xffffffff')); }

function run(msg) {
  if (!Process.findModuleByName('KernelUtil.dll')) {
    const load = new NativeFunction(exp('kernel32.dll', 'LoadLibraryExW'), 'pointer', ['pointer', 'pointer', 'uint']);
    if (load(Memory.allocUtf16String(msg.kernel), ptr(0), 0x8).isNull()) throw new Error('KERNELUTIL_LOAD_FAILED');
  }
  const m = Process.getModuleByName('KernelUtil.dll');
  const fopen = new NativeFunction(m.base.add(OFF.open), 'int', ['pointer', 'pointer']);
  const fkey = new NativeFunction(m.base.add(OFF.key), 'int', ['pointer', 'pointer', 'int']);
  const fclose = new NativeFunction(m.base.add(OFF.close), 'int', ['pointer']);
  const xcodec = new NativeFunction(m.base.add(OFF.xCodec), 'pointer', ['pointer', 'pointer', 'uint', 'int']);
  const k32 = 'kernel32.dll';
  const CreateFileW = new NativeFunction(exp(k32, 'CreateFileW'), 'pointer', ['pointer', 'uint', 'uint', 'pointer', 'uint', 'uint', 'pointer']);
  const ReadFile = new NativeFunction(exp(k32, 'ReadFile'), 'int', ['pointer', 'pointer', 'uint', 'pointer', 'pointer']);
  const WriteFile = new NativeFunction(exp(k32, 'WriteFile'), 'int', ['pointer', 'pointer', 'uint', 'pointer', 'pointer']);
  const SetFilePointerEx = new NativeFunction(exp(k32, 'SetFilePointerEx'), 'int', ['pointer', 'uint', 'int', 'pointer', 'uint']);
  const FlushFileBuffers = new NativeFunction(exp(k32, 'FlushFileBuffers'), 'int', ['pointer']);
  const CloseHandle = new NativeFunction(exp(k32, 'CloseHandle'), 'int', ['pointer']);

  // the codec object is handed to the pager when the key is set; catch it on our own thread
  Interceptor.attach(m.base.add(OFF.pagerSetCodec), {
    onEnter(a) { if (this.threadId === myThread && a[1].equals(m.base.add(OFF.xCodec))) pCodec = a[4]; }
  });
  myThread = Process.getCurrentThreadId();
  const ppDb = Memory.alloc(Process.pointerSize);
  ppDb.writePointer(ptr(0));
  const openRc = fopen(Memory.allocUtf8String(msg.seed), ppDb);
  const db = ppDb.readPointer();
  if (openRc !== 0 || db.isNull()) throw new Error('SEED_OPEN_FAILED');
  const key = Memory.alloc(msg.key.length);
  key.writeByteArray(msg.key);
  let keyRc = -1;
  try { keyRc = fkey(db, key, msg.key.length); } finally { key.writeByteArray(new Array(msg.key.length).fill(0)); }
  if (keyRc !== 0 || pCodec === null) { fclose(db); throw new Error('CODEC_NOT_CREATED'); }

  const inH = CreateFileW(Memory.allocUtf16String(msg.src), 0x80000000, 7, ptr(0), 3, 0, ptr(0));   // read, share all
  if (invalid(inH)) { fclose(db); throw new Error('SOURCE_OPEN_FAILED'); }
  const outH = CreateFileW(Memory.allocUtf16String(msg.out), 0x40000000, 0, ptr(0), 1, 0, ptr(0));  // CREATE_NEW
  if (invalid(outH)) { CloseHandle(inH); fclose(db); throw new Error('OUTPUT_CREATE_FAILED'); }
  const buf = Memory.alloc(PAGE), nread = Memory.alloc(4), nwritten = Memory.alloc(4);
  const result = {pages: 0, page1_magic: false};
  try {
    if (!SetFilePointerEx(inH, HEADER, 0, ptr(0), 0)) throw new Error('SOURCE_SEEK_FAILED');
    for (let pg = 1; pg <= msg.npages; pg++) {
      nread.writeU32(0);
      if (!ReadFile(inH, buf, PAGE, nread, ptr(0)) || nread.readU32() !== PAGE) throw new Error('SOURCE_SHORT_READ');
      const decoded = xcodec(pCodec, buf, pg, 3);
      const page = decoded.isNull() ? buf : decoded;
      if (pg === 1) {
        const h = new Uint8Array(page.readByteArray(16));
        result.page1_magic = String.fromCharCode.apply(null, h) === 'SQLite format 3\u0000';
      }
      nwritten.writeU32(0);
      if (!WriteFile(outH, page, PAGE, nwritten, ptr(0)) || nwritten.readU32() !== PAGE) throw new Error('OUTPUT_SHORT_WRITE');
      result.pages = pg;
      if (pg % 5000 === 0) send({kind: 'progress', page: pg, pages: msg.npages});
    }
    if (!FlushFileBuffers(outH)) throw new Error('OUTPUT_FLUSH_FAILED');
  } catch (e) {
    result.error = String(e.message || e).slice(0, 80);
  } finally {
    CloseHandle(outH); CloseHandle(inH); fclose(db);
  }
  return result;
}
rpc.exports = { decrypt(msg) { try { return run(msg); } catch (e) { return {error: String(e.message || e).slice(0, 80)}; } } };
"""


def read_key(name):
    value = os.environ.get(name, '').strip()
    try:
        key = bytes.fromhex(value)
    except ValueError:
        key = b''
    if len(key) != 16:
        raise SystemExit(f'environment variable {name} must hold the 16-byte key as 32 hex characters')
    return key


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--source', required=True, type=Path, help='a COPY of Msg3.0.db (legacy QQ closed)')
    ap.add_argument('--out', required=True, type=Path, help='new plain SQLite file (must not exist)')
    ap.add_argument('--key-env', required=True)
    ap.add_argument('--kernelutil', required=True, type=Path)
    a = ap.parse_args()
    import frida

    key = read_key(a.key_env)
    if not a.kernelutil.is_file() or a.kernelutil.stat().st_size != KERNELUTIL_SIZE:
        raise SystemExit('KernelUtil.dll must be the one of PC QQ 9.7.25.29417 (1,873,456 bytes); '
                         'the function offsets do not fit other builds')
    if not HOST.is_file():
        raise SystemExit(f'32-bit PowerShell not found: {HOST}')
    size = a.source.stat().st_size
    if size < HEADER_SIZE + PAGE_SIZE or (size - HEADER_SIZE) % PAGE_SIZE:
        raise SystemExit('source does not look like a legacy Msg3.0.db (1024-byte header + 8192-byte pages)')
    out = a.out.resolve()
    partial = out.with_name(out.name + '.partial')
    if out.exists() or partial.exists():
        raise SystemExit(f'{out} (or its .partial) already exists')
    npages = (size - HEADER_SIZE) // PAGE_SIZE
    before = (size, a.source.stat().st_mtime_ns)
    print(f'decrypting {npages} pages', flush=True)

    device = frida.get_local_device()
    pid = session = script = None
    errors = []

    def on_message(message, _data):
        if message.get('type') == 'error':
            errors.append(message.get('description', 'script error'))
        elif message.get('payload', {}).get('kind') == 'progress':
            p = message['payload']
            print(f'  page {p["page"]}/{p["pages"]}', flush=True)

    try:
        with tempfile.TemporaryDirectory(prefix='pcqq-seed-') as tmp:
            # seed = the source's header and first page; the DLL opens it and sets the key, only its codec is used.
            # (An empty new file does not work: the DLL then creates a codec that does not decode legacy pages.)
            seed = Path(tmp) / 'seed.db'
            with a.source.open('rb') as f:
                seed.write_bytes(f.read(HEADER_SIZE + PAGE_SIZE))
            pid = device.spawn(str(HOST), argv=['-NoLogo', '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden',
                                                '-Command', 'Start-Sleep -Seconds 3600'], cwd=str(a.kernelutil.parent))
            session = device.attach(pid)
            script = session.create_script(AGENT)
            script.on('message', on_message)
            script.load()
            device.resume(pid)
            result = script.exports_sync.decrypt({'src': str(a.source.resolve()), 'out': str(partial), 'seed': str(seed),
                                                  'kernel': str(a.kernelutil.resolve()), 'npages': npages, 'key': list(key)})
            script.unload()
            script = None
    finally:
        for step in (lambda: script and script.unload(), lambda: session and session.detach(),
                     lambda: pid and device.kill(pid)):
            try:
                step()
            except Exception:
                pass
    def fail(msg):
        partial.unlink(missing_ok=True)
        raise SystemExit(msg)

    if errors or result.get('error') or result.get('pages') != npages:
        fail(f'decryption failed: {result.get("error") or errors}')
    if (a.source.stat().st_size, a.source.stat().st_mtime_ns) != before:
        fail('the source changed during decryption; use a copy taken with legacy QQ closed')
    with partial.open('rb') as f:
        page1 = f.read(18)
    if partial.stat().st_size != npages * PAGE_SIZE or page1[:16] != SQLITE_MAGIC or not result.get('page1_magic'):
        fail('the output is not a valid SQLite file: wrong key or wrong KernelUtil.dll')
    os.replace(partial, out)
    print(f'done: {out} ({npages} pages, page size {int.from_bytes(page1[16:18], "big")})')


if __name__ == '__main__':
    main()
