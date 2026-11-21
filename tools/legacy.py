"""Legacy PC QQ (Msg3.0.db) records -> QQNT message rows.

Input is a DECRYPTED legacy Msg3.0.db (a plain SQLite file). How to decrypt it is not covered here.
Legacy tables: buddy_<QQ number> (private chats), group_<group uin> (groups), group$<group uin>$Seq$ (sequence index).
Each row has Time, Rand, SenderUin, MsgContent and Info.

Every rule below was verified against rows the QQNT importer (or the client) wrote itself, byte by byte:
  text (1), emoji text (13)  -> element 1, text from sub-field 1 only
  classic face (2)           -> element 6; id < 255 keeps its id, id 255 takes the id in sub-field 2 (protobuf field 1)
  new face (meta 25, 53/33)  -> element 6
  picture (3, 6)             -> element 2 (md5, size, size in pixels, resource id, server path)
  forward (30)               -> element 16 with the XML summary rebuilt as the importer does
  private file (17)          -> element 3
  reply (meta 25 'ea02')     -> element 7 followed by the other elements
  market face (12)           -> element 11 (package id, md5, key)
  local notices              -> local gray tip (element 8, sub-type 17), the text old PC QQ displayed
  card (20), app card (27)   -> one text element with what old PC QQ displayed (27 with JSON -> ark element 10)
Not converted (raise Unsupported): voice (7), video (26), unknown element types, group rows without a real sequence.
"""
import bisect
import datetime as dt
import html
import json
import random
import re
import struct
import zlib
from xml.sax.saxutils import escape

import qqnt

C2C, GROUP = qqnt.MSG_TABLES
META = {0, 14, 18, 25}
REPLY_MARK = b'\xea\x02'
EXPIRED = (45507, 0, (1 << 64) - 6101)     # QQNT's own mark after a failed download, together with 45509 = 1


class Unsupported(Exception):
    pass


# --- legacy containers ----------------------------------------------------------------------------------------

def tlv(b, start=None):
    """MsgContent: 'MSG' header (font name length at offset 24), then elements (type u8, length u16, payload).
    With start, parse a payload's own sub-fields from that offset. Returns [(type, bytes)] or None if malformed."""
    out = []
    if start is None:
        if not b or len(b) < 28 or b[:3] != b'MSG':
            return None
        p = 26 + struct.unpack_from('<H', b, 24)[0] + 2
    else:
        p = start
    while p < len(b):
        if p + 3 > len(b):
            return None
        t, n = b[p], struct.unpack_from('<H', b, p + 1)[0]
        if p + 3 + n > len(b):
            return None
        out.append((t, b[p + 3:p + 3 + n]))
        p += 3 + n
    return out


def _xor(data, n):
    k = (~(n ^ (n >> 8) ^ (n >> 16) ^ (n >> 24))) & 0xFF
    return bytes(x ^ k for x in data)


def td(b):
    """'TD' key-value container: {name: (type, raw, decoded)}. Names are UTF-16LE; both parts XOR a length byte."""
    if not b or len(b) < 6 or b[:2] != b'TD':
        return {}
    p, out = 6, {}
    for _ in range(struct.unpack_from('<H', b, 4)[0]):
        if p + 3 > len(b):
            break
        ty, nl = b[p], struct.unpack_from('<H', b, p + 1)[0]
        p += 3
        name = _xor(b[p:p + nl], nl).decode('utf-16-le', 'replace')
        p += nl
        if p + 4 > len(b):
            break
        vl = struct.unpack_from('<I', b, p)[0]
        p += 4
        raw = b[p:p + vl]
        p += vl
        out[name] = (ty, raw, _xor(raw, vl))
    return out


def td_int(d, k):
    return int.from_bytes(d[k][1], 'little') if k in d else None


def td_str(d, k):
    return d[k][2].decode('utf-16-le', 'replace') if k in d else None


def td_buf(d, k):
    return d[k][2] if k in d else None


def u16(b):
    return b.decode('utf-16-le', 'replace')


# group table uin -> group code (the number users see), by the uin's leading digits
_GROUP_RANGES = [((202, 212), 202), ((480, 488), 469), ((2100, 2146), 2080), ((2010, 2099), 1943), ((2147, 2199), 1990),
                 ((4100, 4199), 3890), ((3800, 3825), 3490), ((2601, 2651), 2265), ((3877, 3989), 3490)]


def group_code(uin):
    left = uin // 1000000
    for (lo, hi), off in _GROUP_RANGES:
        if lo <= left <= hi:
            return (left - off) * 1000000 + uin % 1000000
    return uin


def legacy_tables(db):
    """Yield (legacy table, QQNT table, peer as str) for every buddy_/group_ message table."""
    for (name,) in db.execute(r"SELECT name FROM sqlite_master WHERE type='table' AND "
                              r"(name LIKE 'buddy\_%' ESCAPE '\' OR name LIKE 'group\_%' ESCAPE '\')"):
        kind, _, num = name.partition('_')
        if num.isdigit():
            yield name, (C2C if kind == 'buddy' else GROUP), str(int(num) if kind == 'buddy' else group_code(int(num)))


def real_group_seq(db, table, info, ts, rnd):
    """The group sequence of a legacy row: Info.dwMsgSeq, else a unique (Time, Rand) hit in group$<uin>$Seq$."""
    seq = td_int(td(info or b''), 'dwMsgSeq')
    if seq:
        return seq
    seq_table = table.replace('group_', 'group$', 1) + '$Seq$'
    try:
        hits = db.execute(f'SELECT Seq FROM "{seq_table}" WHERE Time=? AND Rand=?', (ts, rnd)).fetchall()
    except Exception:
        return None
    return hits[0][0] if len(hits) == 1 and hits[0][0] else None


# --- elements -------------------------------------------------------------------------------------------------

def _split_name(name):
    base = name.replace('/', '\\').rsplit('\\', 1)[-1]
    if ':' in base:
        base = base.rsplit(':', 1)[-1]
    stem, dot, ext = base.rpartition('.')
    return (base, stem, '.' + ext) if dot else (base, base, '')


def _text(pl, _):
    sub = tlv(pl, 0)
    if sub is None:
        raise Unsupported('text_unparsed')
    keys = {i for i, _ in sub}
    if 0 in keys:
        raise Unsupported('local_notice')          # sub-field 0 = PC-only formatting: a local notice
    if keys - {1, 2, 3, 6, 7, 8}:
        raise Unsupported('text_extra_subfields')
    s = ''.join(u16(v) for i, v in sub if i == 1)
    return [(1, [(45002, 0, 1), (45101, 2, s.encode('utf-8'))])] if s else []


def _emoji13(pl, _):
    s = ''.join(u16(v) for i, v in tlv(pl, 0) or [] if i == 1)
    return [(1, [(45002, 0, 1), (45101, 2, s.encode('utf-8'))])] if s else []


def _face2(pl, _):
    d = dict(tlv(pl, 0) or [])
    if 1 not in d:
        raise Unsupported('face_no_id')
    fid = int.from_bytes(d[1], 'little')
    if fid == 255:
        ext = {f: x for f, _w, x in qqnt.parse(d.get(2, b''))}
        if not isinstance(ext.get(1), int):
            raise Unsupported('face255_no_id')
        fid = ext[1]
    return [(6, [(45002, 0, 6), (47601, 0, fid)])]


def _picture(pl, _):
    sd = dict(tlv(pl, 0) or [])
    path = u16(sd[2]) if 2 in sd else ''
    if path.startswith('sysres:'):
        raise Unsupported('local_notice')          # blue system bar
    if path.startswith('FaceStore:'):
        raise Unsupported('market_face_picture')
    t = td(sd.get(0, b''))
    src = t
    if 'pPicRecvInfo' in t:
        src = td(t['pPicRecvInfo'][1])
        md5 = td_buf(src, 'bufPicMd5') or b''
        if not md5:
            guid = ''.join(ch for ch in (td_str(t, 'strSrcPicName') or '') if ch in '0123456789abcdefABCDEF')
            if len(guid) == 32 and (td_str(t, 'strSrcPicName') or '').startswith('{'):
                md5 = bytes.fromhex(guid)
        size, resid, server = td_int(src, 'dwFileSize'), str(td_int(src, 'dwFileID')).encode(), None
        _base, name, ext = _split_name(path or td_str(src, 'strFileName') or '')
    else:
        md5, size = td_buf(t, 'bufPicMd5'), td_int(t, 'dwPicInfoFileSize')
        resid, server = td_buf(t, 'bufPicResId'), td_buf(t, 'bufPicInfoServerPath')
        fname = td_str(t, 'strPicInfoFileName')
        _pb, pstem, pext = _split_name(path)
        if fname:
            _b, _s, ext = _split_name(fname)
            name, ext = fname, ext or pext
        else:
            name, ext = pstem, pext
    w, h = td_int(src, 'dwPicWidth'), td_int(src, 'dwPicHeight')
    if md5 is None or size is None or w is None or h is None or not resid:
        raise Unsupported('picture_missing_meta')
    f = [(45002, 0, 2), (45003, 0, 0), (45402, 2, name.encode('utf-8')), (45405, 0, size), (45406, 2, md5),
         (45411, 0, w), (45412, 0, h), (45419, 2, ext.encode('utf-8')), (45503, 2, resid)]
    if server is not None:
        f.append((45504, 2, server))
    f.append((45509, 0, 0))
    return [(2, f)]


def _title_list(buf):
    if len(buf) < 8:
        return []
    k = buf[0] ^ 0x54
    b = bytes(c ^ k for c in buf)
    if b[:2] != b'TA':
        return []
    p, out = 8, []
    for _ in range(struct.unpack_from('<I', b, 4)[0]):
        n = struct.unpack_from('<I', b, p + 1)[0]
        out.append(b[p + 5:p + 5 + n].decode('utf-16-le', 'replace'))
        p += 5 + n
    return out


def _forward(pl, _):
    t = td(dict(tlv(pl, 0) or []).get(1, b''))
    resid, fname = td_str(t, 'bsResId'), td_str(t, 'bsFileName')
    if not resid or fname is None:
        raise Unsupported('forward_no_resid')
    titles = _title_list(t['titleList'][2]) if 'titleList' in t else []
    a = lambda s: escape(s, {'"': '&quot;'})
    xml = ('<?xml version="1.0" encoding="utf-8"?><msg brief="%s" m_fileName="%s" m_fileSize="0" action="viewMultiMsg" '
           'tSum="2" flag="3" serviceID="35" m_resid="%s" > <item layout="1"> <title color="#000000" size="34">%s</title>'
           % (a(td_str(t, 'brief') or '[聊天记录]'), a(fname), a(resid), escape(td_str(t, 'title') or '聊天记录')))
    xml += ''.join('<title color="#777777" size="26">%s</title>' % escape(x) for x in titles)
    xml += ' <summary color="#808080" size="26"> 查看%d条转发消息  </summary> </item><source name="聊天记录"></source> </msg>' % len(titles)
    return [(16, [(45002, 0, 16), (48601, 2, resid.encode('utf-8')), (48602, 2, xml.encode('utf-8')),
                  (48603, 2, fname.encode('utf-8'))])]


def _file17(pl, _):
    t3 = td(dict(tlv(pl, 0) or []).get(3, b''))
    if 'pFileData' not in t3:
        raise Unsupported('file_no_data')
    inner = td(t3['pFileData'][1])
    if td_int(inner, 'bFolder'):
        raise Unsupported('file_folder')
    name, size = td_str(inner, 'strFinalFile'), td_int(inner, 'u64FileSize')
    md5, uid = td_buf(inner, 'buf10mMd5'), td_buf(inner, 'bufUUID')
    if not name or size is None or md5 is None or not uid:
        raise Unsupported('file_missing_meta')
    name = name.replace('/', '\\').rsplit('\\', 1)[-1]
    ext = _split_name(name)[2]
    return [(3, [(45002, 0, 3), (45402, 2, name.encode('utf-8')), (45403, 2, name.encode('utf-8')), (45405, 0, size),
                 (45406, 2, md5), (45419, 2, ext.encode('utf-8')), (45503, 2, uid), (45504, 2, b''), (45550, 0, 1)])]


BUILDERS = {1: _text, 13: _emoji13, 2: _face2, 3: _picture, 6: _picture, 30: _forward, 17: _file17}


def _meta25(pl):
    out = []
    for i, v in tlv(pl, 0) or []:
        if i != 1 or not v:
            continue
        try:
            fields = qqnt.parse(v)
        except (ValueError, IndexError):
            continue
        for f, w, inner in fields:
            if f != 53 or w != 2:
                continue
            d = {ff: vv for ff, _w, vv in qqnt.parse(inner)}
            if d.get(1) != 33:
                raise Unsupported(f'meta25_type{d.get(1)}')
            face = {ff: vv for ff, _w, vv in qqnt.parse(d.get(2, b''))}
            if 1 not in face:
                raise Unsupported('meta25_face_no_id')
            out.append((6, [(45002, 0, 6), (47601, 0, face[1])]))
    return out


def _plain_elements(tl, table):
    els = []
    for ty, pl in tl:
        if ty == 25:
            els += _meta25(pl)
        elif ty in META:
            continue
        elif ty == 12:
            raise Unsupported('market_face')
        elif ty in BUILDERS:
            els += BUILDERS[ty](pl, table)
        else:
            raise Unsupported(f'type{ty}')
    return els


def _reply(pl, grp):
    msg = None
    for i, v in tlv(pl, 0) or []:
        if i == 1:
            msg = next((x for f, w, x in qqnt.parse(v) if f == 45 and w == 2), None)
    if msg is None:
        raise Unsupported('reply_unparsed')
    o = {f: x for f, _w, x in qqnt.parse(msg)}
    rep = [(5, 2, o.get(5, b'')), (40010, 0, 2 if grp else 1)]
    if grp:
        rep.append((40020, 2, b''))
    rep += [(45002, 0, 7), (47402, 0, o.get(1, 0)), (47403, 0, o.get(2, 0)), (47404, 0, o.get(3, 0)),
            (47405, 0, o.get(4, 0)), (47407, 0, o.get(6, 0)), (47408, 2, b''), (47410, 2, o.get(9, b'')),
            (47411, 0, o.get(10, 0)), (47412, 2, b'')]
    if not grp:
        rep.append((47419, 0, 0))
    return (7, rep)


def content_elements(content, table):
    tl = tlv(content)
    if tl is None:
        raise Unsupported('content_unparsed')
    reps = [pl for ty, pl in tl if ty == 25 and pl[3:5] == REPLY_MARK]
    if len(reps) > 1:
        raise Unsupported('reply_multiple')
    rest = [(ty, pl) for ty, pl in tl if not (ty == 25 and pl[3:5] == REPLY_MARK)]
    els = _plain_elements(rest, table)
    if reps:
        els = [_reply(reps[0], table == GROUP)] + els
    if not els:
        raise Unsupported('no_elements')
    return els


def market_face(content):
    tl = [(ty, pl) for ty, pl in (tlv(content) or []) if ty not in META and ty != 18]
    t12 = [pl for ty, pl in tl if ty == 12]
    if len(t12) != 1 or [ty for ty, _ in tl if ty not in (12, 3, 25)]:
        raise Unsupported('market_face_shape')
    sd = dict(tlv(t12[0], 0) or [])
    b, name = sd.get(2, b''), u16(sd.get(1, b''))
    if len(b) != 44 or b[0] != 6 or not re.fullmatch(r'\[.+\]', name):
        raise Unsupported('market_face_unparsed')
    md5, tab, key = b[7:23], int.from_bytes(b[23:27], 'big'), b[28:44]
    if not re.fullmatch(rb'[0-9a-f]{16}', key) or not 100000 <= tab <= 999999:
        raise Unsupported('market_face_fields')
    dim = sd.get(4, b'')
    w, h = (int.from_bytes(dim[0:2], 'big'), int.from_bytes(dim[2:4], 'big')) if len(dim) == 4 and any(dim) else (200, 200)
    size_blob = qqnt.enc([(80924, 0, w), (80925, 0, h)])    # as the client writes it: e0c127 <w> e8c127 <h>
    return [(11, [(45002, 0, 11), (80810, 0, tab), (80824, 2, key), (80900, 2, name.encode('utf-8')), (80901, 0, 0),
                  (80902, 0, 0), (80903, 2, md5), (80905, 0, 0), (80908, 0, 0), (80909, 0, w), (80910, 0, h),
                  (80935, 0, 1), (80970, 2, size_blob), (80975, 0, 0), (80980, 0, 0), (80981, 0, 0), (80995, 0, 0)])]


def _xml_brief(pl):
    sub = dict(tlv(pl, 0) or [])
    z, xml = sub.get(1, b''), ''
    for cand in (z[1:], z):
        try:
            xml = zlib.decompress(cand).decode('utf-8', 'replace')
            break
        except zlib.error:
            continue
    if not xml:
        xml = (td_buf(td(sub.get(0, b'')), 'bufXml') or b'').decode('utf-8', 'replace')
    m = re.search(r'brief="([^"]*)"', xml)
    return html.unescape(m.group(1)) if m else ''


def display_text(content):
    """What old PC QQ displayed for a record, as one plain string."""
    tl = tlv(content)
    if tl is None:
        raise Unsupported('content_unparsed')
    parts = []
    for ty, pl in tl:
        if ty in META:
            continue
        sub = tlv(pl, 0) or []
        sd = dict(sub)
        if ty in (1, 13):
            parts.append(''.join(u16(v) for i, v in sub if i == 1))
        elif ty == 12:
            if 1 in sd:
                parts.append(u16(sd[1]))
        elif ty in (3, 6):
            path = u16(sd.get(2, b''))
            if path.startswith('FaceStore:'):
                tip = td_str(td(sd.get(0, b'')), 'strTip')
                if tip and not any(tip in p for p in parts):
                    parts.append(tip)
            elif not path.startswith('sysres:'):
                parts.append('[图片]')
        elif ty == 20:
            b = _xml_brief(pl)
            if b and not any(b in p for p in parts):
                parts.append(b)
        elif ty == 27:
            d = td(sd.get(1, b''))
            parts.append(td_str(d, 'prompt') or td_str(d, 'desc') or '')
        else:
            raise Unsupported(f'display_type{ty}')
    s = ''.join(parts).strip()
    if not s:
        raise Unsupported('display_empty')
    return s


def ark(content):
    tl = [(ty, pl) for ty, pl in (tlv(content) or []) if ty not in META]
    if len(tl) != 1 or tl[0][0] != 27:
        return None
    d = td(dict(tlv(tl[0][1], 0) or []).get(1, b''))
    try:
        meta, cfg = json.loads(td_str(d, 'meta')), json.loads(td_str(d, 'config-json') or '{}')
    except (TypeError, ValueError):
        return None
    j = {'app': td_str(d, 'app') or '', 'config': cfg, 'desc': td_str(d, 'desc') or '', 'meta': meta,
         'prompt': td_str(d, 'prompt') or '', 'ver': td_str(d, 'ver') or '', 'view': td_str(d, 'view') or ''}
    return [(10, [(45002, 0, 10), (47901, 2, json.dumps(j, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))])]


def gray_tip(text):
    j = json.dumps({'align': 'center', 'items': [{'txt': text, 'type': 'nor'}]}, ensure_ascii=False, separators=(',', ':')) + '\n'
    return [(8, [(45002, 0, 8), (45003, 0, 17), (48271, 2, j.encode('utf-8')), (48272, 0, 0), (48273, 0, 52), (48275, 0, 0)])]


# message type 40011/40012 by the set of element types, as the importer writes them. For mixed messages 40012 is a bit
# set (text 1, picture 2, face 16, reply 32; a reply alone is 33). Only combinations seen in importer rows are listed.
TYPESET = {(1,): (2, 1), (2,): (2, 2), (1, 2): (2, 3), (6,): (2, 16), (1, 6): (2, 17), (2, 6): (2, 18), (1, 2, 6): (2, 19),
           (1, 7): (2, 33), (7,): (2, 33), (2, 7): (2, 34), (1, 2, 7): (2, 35), (6, 7): (2, 48), (1, 6, 7): (2, 49),
           (4,): (6, 0), (5,): (7, 0), (16,): (8, 0), (10,): (11, 0)}


def is_recall_notice(sender, content):
    """Recall notices from system account 10000: text whose sub-field 0 holds only PC formatting."""
    return int(sender or 0) == 10000 and any(ty == 1 and any(i == 0 for i, _ in tlv(pl, 0) or [])
                                             for ty, pl in tlv(content) or [])


def convert(content, table, sender):
    """Element list and kind for one legacy record. kind: content, gray_tip, market_face, ark or text."""
    try:
        return content_elements(content, table), 'content'
    except Unsupported as e:
        reason = str(e)
    if reason == 'local_notice':
        return gray_tip(display_text(content)), 'gray_tip'
    if reason == 'market_face':
        return market_face(content), 'market_face'
    if reason in ('type20', 'type27', 'meta25_type3'):
        a = ark(content)
        if a:
            return a, 'ark'
        return [(1, [(45002, 0, 1), (45101, 2, display_text(content).encode('utf-8'))])], 'text'
    raise Unsupported(reason)


# --- rows -----------------------------------------------------------------------------------------------------

class Writer:
    """Turns legacy records into QQNT rows for one database, using lookups from its existing rows."""

    def __init__(self, db, self_uin, tz):
        from zoneinfo import ZoneInfo
        self.db, self.me, self.tz = db, int(self_uin), ZoneInfo(tz)
        self.uid, self.conv = {}, {}
        for t in qqnt.MSG_TABLES:
            for snd, uid, peer, puid, slot in db.execute(f'SELECT "40033","40020","40030","40021","40027" FROM {t}'):
                if uid and '*' not in str(uid):
                    self.uid.setdefault(int(snd or 0), uid)
                self.conv.setdefault((t, str(peer)), (puid, slot))
        flags = db.execute('SELECT "40013",count(*) FROM group_msg_table WHERE "40013">3 GROUP BY 1 ORDER BY 2 DESC LIMIT 1').fetchone()
        self.group_flag = flags[0] if flags else 0
        self.taken = {}
        for t in qqnt.MSG_TABLES:
            for slot, seq, rnd, f5 in db.execute(f'SELECT "40027","40003","40002","40005" FROM {t}'):
                self.taken.setdefault((t, slot), set()).add(seq if t == GROUP else (rnd, f5))
        ids = [db.execute(f'SELECT max("40001") FROM {t}').fetchone()[0] or 0 for t in qqnt.MSG_TABLES]
        import time
        self.next_id = max(int(time.time()) << 32, max(ids) + 65536) + 2

    def sender_uid(self, uin):
        uin = int(uin)
        return self.uid.get(uin) or 'u_' + '*' * (21 - len(str(uin))) + '_' + str(uin)

    def local_midnight(self, ts):
        off = int(dt.datetime.fromtimestamp(ts, self.tz).utcoffset().total_seconds())
        return ts - (ts + off) % 86400

    def local_month(self, ts):
        return dt.datetime.fromtimestamp(ts, self.tz).strftime('%Y-%m')

    def row(self, table, peer, rec, seq=None, picture_exists=None):
        """rec: dict(time, rand, sender, content, info). Returns (column dict, kind). Raises Unsupported.
        seq: the real group sequence (required for groups). picture_exists(md5_hex, size, month) -> bool marks the
        pictures without a local original as expired, as QQNT does after a failed download."""
        if (table, peer) not in self.conv:
            raise Unsupported('conversation_not_in_qqnt')
        grp = table == GROUP
        if grp and not seq:
            raise Unsupported('group_no_real_seq')
        puid, slot = self.conv[(table, peer)]
        if grp and seq in self.taken.get((table, slot), ()):
            raise Unsupported('group_seq_taken')
        sender = int(rec['sender'] or 0)
        els, kind = convert(rec['content'], table, sender)
        if picture_exists:
            month = self.local_month(rec['time'])
            for q, f in els:
                if q == 2:
                    v = dict((a, x) for a, _w, x in f)
                    if not picture_exists(v[45406].hex(), v[45405], month):
                        f[:] = sorted([x for x in f if x[0] != 45509] + [EXPIRED, (45509, 0, 1)], key=lambda x: x[0])
        msgid = self.next_id
        blob = b''.join(qqnt.enc([(40800, 2, qqnt.enc([(45001, 0, msgid + k)] + f))]) for k, (q, f) in enumerate(els, 1))
        self.next_id += len(els) + 2 + len(els) % 2
        qtypes = [q for q, _ in els]
        if kind == 'gray_tip':
            t11, t12 = 5, 17
        elif kind == 'market_face':
            t11, t12 = 17, 8
        elif qtypes == [3]:
            t11, t12 = 3, (1 if sender == self.me else 2)
        else:
            key = tuple(sorted(set(qtypes)))
            if key not in TYPESET:
                raise Unsupported('typeset_' + ','.join(map(str, key)))
            t11, t12 = TYPESET[key]
        info = td(rec['info'] or b'')
        show = td_str(info, 'strSenderShowName') or ''
        r = {'40001': msgid, '40002': rec['rand'], '40003': seq if grp else 0, '40010': 2 if grp else 1,
             '40011': t11, '40012': t12, '40013': (1 if 'bSelfMsg' in info else self.group_flag) if grp else int(sender == self.me),
             '40020': self.sender_uid(sender), '40026': 0, '40021': puid, '40027': slot, '40040': 0, '40041': 2,
             '40050': rec['time'], '40052': 0, '40090': show if grp else '', '40093': '' if grp else show,
             '40800': blob, '40900': None, '40105': 0, '40005': 0 if grp else random.randint(1, 65535),
             '40058': self.local_midnight(rec['time']), '40006': 0, '40100': 0, '40600': None, '40060': 1, '40850': 0,
             '40851': 0, '40601': None, '40801': None, '40605': None, '40030': int(peer), '40033': sender, '40062': None,
             '40083': 0, '40084': 0, '40008': 0, '40009': 0, '40722': None}
        if kind == 'gray_tip' and not grp:
            r.update({'40013': 3, '40020': None, '40033': 0, '40093': ''})
        taken = self.taken.setdefault((table, slot), set())
        if grp:
            taken.add(seq)
        else:
            while (r['40002'], r['40005']) in taken:
                r['40005'] = 1 + r['40005'] % 65535
            taken.add((r['40002'], r['40005']))
        return r, kind


def is_imported_row(blob, msgid, ts):
    """True for a row the QQNT history importer wrote: msgId carries the import time (long after the message) and no
    element was refreshed from the server later (45802 URL, 45816 host, 45408 sha1)."""
    if (int(msgid) >> 32) - int(ts or 0) <= 86400:
        return False
    return not any(x in (45802, 45816, 45408) for v in qqnt.elements(blob) for x in v)
