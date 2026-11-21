[中文](README.md) | English

# Tools

Scripts for the three parts of the [main document](../README.en.md): completing legacy history, moving to Mac and
moving to phone. They contain no account numbers, paths or keys; every input is a command-line argument.

## Requirements

- Python 3.10 or later with [sqlcipher3](https://pypi.org/project/sqlcipher3/): `pip install sqlcipher3`. Tools with
  `--tz` also need `pip install tzdata` on Windows.
- The database key of your own account. How to obtain it is not covered here; on Mac see
  [protocol_dbkey](https://github.com/QQBackup/protocol_dbkey).
- For legacy history: a **decrypted** legacy `Msg3.0.db` (a plain SQLite file). Decryption is not covered here.
- Keys are passed only through environment variables. `--key-env` takes the **name** of the variable, not the key.
- On Windows, `qq_key.ps1` stores a key in a DPAPI-protected file and loads it into an environment variable.

## Safety rules

- **Quit QQ from the tray first.** The tools refuse to run when the main database has a non-empty `-wal` or `-journal`.
- **The live file is never changed.** The tools copy the database into a temporary folder, work on the copy, and
  write the result to a new file with the original 1024-byte vendor header.
- **Output files must be new.** If the output exists, the tool stops; nothing is overwritten.
- **Every change is checked.** Tools that change data confirm that, apart from the intended rows, every row of every
  table is unchanged, and that `PRAGMA integrity_check` passes.
- **You replace the live file yourself.** Keep the original as a rollback source, move the search indexes
  (`*_msg_fts.db`) away, put the new file in place, and check the result in the client.
- The review page and `new_rows.json` contain message content. Use them on your own computer only and delete them
  when done.

## Overview

| Script | Purpose | Changes data |
| --- | --- | --- |
| `legacy_reconcile.py` | Pair legacy records with existing QQNT messages one to one and find the missing ones | read-only |
| `legacy_check.py` | Check the conversion rules against rows the importer wrote itself | read-only |
| `legacy_write.py` | Write the missing legacy records into a copy of the QQNT database | new file |
| `unexpire_pictures.py` | Show pictures again that are marked expired although the original exists | new file |
| `db_stats.py` | Integrity check, row and conversation counts | read-only |
| `compare_devices.py` | Compare one account's messages between two devices one to one; differences per conversation | read-only |
| `copy_missing.py` | Write the messages A has and B lacks into a copy of B, converting the local path field `45812` | new file |
| `fix_uid_mapping.py` | Add the missing `nt_uid_mapping_table` rows of private chats | new file |
| `purge_conversations.py` | Delete whole conversations listed in a file, then `VACUUM` | new file |
| `review_conversations.py` | Local HTML page to tick conversations for deletion and export the list | read-only |
| `phone_batches.py` | Export to phone in batches: split, build a temporary database, collect messages received meanwhile, merge them back | new file |
| `list_media.py`, `compare_media.py` | List and compare the media files of two `nt_data` folders and write a copy list | read-only |
| `qq_key.ps1` | Store and load keys with DPAPI on Windows | — |

Run `python <script> --help` for details.

## Examples

### Write legacy history into QQNT

```sh
# 1. Find the missing records. Several legacy files (e.g. the live one and an older backup) can be passed; identical
#    records count once
python tools/legacy_reconcile.py --legacy Msg3.0.plain.db --qqnt nt_msg.db --key-env QQ_KEY \
       --exclude not_wanted.tsv --out reconcile.sqlite
# 2. Check the rules against the importer's own rows; do not write a class that does not match
python tools/legacy_check.py --reconcile reconcile.sqlite --qqnt nt_msg.db --key-env QQ_KEY \
       --self <own QQ number> --tz Asia/Shanghai
# 3. Rehearse with one or two conversations, check them in the client, then write everything
python tools/legacy_write.py --reconcile reconcile.sqlite --qqnt nt_msg.db --key-env QQ_KEY \
       --self <own QQ number> --tz Asia/Shanghai --nt-data <account nt_data> --only pilot.tsv --out nt_msg.pilot.db
# 4. Show pictures again whose original exists
python tools/unexpire_pictures.py nt_msg.db --key-env QQ_KEY --nt-data <account nt_data> --tz Asia/Shanghai --out nt_msg.shown.db
```

`--tz` is the time zone of the computer that runs QQ, e.g. `Asia/Shanghai`.

Rules of `legacy_write.py`:
- Only conversations QQNT already has are written.
- **Always pass deleted conversations with `--exclude`.** While you are still in a group, new messages keep arriving,
  so QQNT keeps the conversation and the deleted history would be written back. `legacy_reconcile.py` lists the
  conversations with the most missing records; look at it before writing.
- A record whose Rand already exists in the same QQNT conversation at a clearly different time is most likely the
  same message and is skipped; `--write-possible-duplicates` writes it anyway.
- Group rows only with their real sequence, and only when it is not taken in that group; never borrowed from a
  neighbouring message.
- Recall notices from the system account are skipped by default, as the importer does; `--recall-notices write`
  stores them as gray tips.
- `--kinds` limits what is written: `content`, `gray_tip`, `market_face`, `ark`, `text`.

Afterwards check a few rows in the client using the msgIds and times in `<out>.samples.tsv`. `reconcile.sqlite`
contains message content; delete it when done.

### Fill in differences after moving to Mac

```sh
# 1. Quit QQ on both devices, copy both nt_msg.db files to one computer, set WIN_KEY and MAC_KEY
python tools/compare_devices.py --a win/nt_msg.db --a-key-env WIN_KEY --a-name win \
                                --b mac/nt_msg.db --b-key-env MAC_KEY --b-name mac --out cmp
# 2. Write the messages Windows has and the Mac lacks into a copy of the Mac database
python tools/copy_missing.py --src win/nt_msg.db --src-key-env WIN_KEY --dst mac/nt_msg.db --dst-key-env MAC_KEY \
       --out mac/nt_msg.new.db --src-data 'C:\...\nt_qq\nt_data' --dst-data '/Users/.../nt_qq_xxx/nt_data'
# 3. Add mapping rows for new private chats
python tools/fix_uid_mapping.py mac/nt_msg.new.db --key-env MAC_KEY --out mac/nt_msg.final.db
# 4. Compare again: Windows should have no messages left that the Mac lacks
# 5. Media: list the files on each device, then copy the list to the Mac without overwriting existing files
python tools/list_media.py <nt_data> --out win.tsv          # and mac.tsv on the Mac
python tools/compare_media.py win.tsv --a-name win mac.tsv --b-name mac --out media
```

### Review and delete conversations

```sh
python tools/review_conversations.py nt_msg.db --key-env QQ_KEY --self <own QQ number> --out review.html
# open review.html in a browser, tick conversations, export delete_list.tsv
python tools/purge_conversations.py nt_msg.db --key-env QQ_KEY --list delete_list.tsv --out nt_msg.purged.db
```

The same list can be applied to the database of each device.

Do not list conversation 0. Rows with conversation 0 are not one conversation; many belong to kept groups or chats
(see their `40021`).

### Export to phone in batches

```sh
python tools/phone_batches.py split nt_msg.db --key-env QQ_KEY --out-dir batches --batches 3
# for each batch:
python tools/phone_batches.py build nt_msg.db --key-env QQ_KEY --batch batches/batch1.tsv --out temp1.db
#   put temp1.db in place -> open QQ -> export all to phone -> wait for the phone -> quit QQ from the tray
python tools/phone_batches.py collect nt_msg.db --temp <temp1.db as QQ left it> --key-env QQ_KEY --out new_rows1.json
#   put the original back; if collect found rows, merge them:
python tools/phone_batches.py merge nt_msg.db --key-env QQ_KEY --rows new_rows1.json --out nt_msg.merged.db
```

Keep each batch at or below 200 private chats. When a batch hangs, split it in half.

## Known limits

- `copy_missing.py` writes message rows only, not chat-list entries (`recent_contact_v3_table`). A private chat without
  an entry may be left out of an export to phone.
- `compare_media.py` compares relative paths. If the destination already has a picture with the same md5 under another
  extension, it is still on the copy list and the copy creates a duplicate; remove such files by md5 before copying.
- `copy_missing.py` skips rows whose message ID is already used on the destination and group rows whose sequence is
  taken; the report counts them.
- `legacy_write.py` writes no voice or video and no conversation QQNT lacks.
- The rules in `legacy.py` were verified byte by byte against the importer's output during a real migration, and
  checked again on a real account after the cleanup:
  - In 106,560 importer rows, message type and `40058` all match.
  - Of the 1,034 rows written back then, 1,019 are byte-identical to what the tool generates now. The other 15 differ
    because the client renamed pictures later and a sender uid became known later.
- Remaining differences from the importer that do not affect display:
  - Picture file name `45402`: the tool keeps the legacy name; after showing a picture the client renames it to
    "md5 + `.png`".
  - A reply followed by several text segments: the importer sometimes drops the leading "@name" and space (about
    10 %); the tool keeps everything old PC QQ displayed.
  - App cards (legacy type 27): the importer writes empty messages; the tool writes a card element.
- Run `legacy_check.py` on your own data first.
- Based on QQNT 9.9.36 for Windows and QQ 7.0.2 for Mac; other versions may use different tables.
