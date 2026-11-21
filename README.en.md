[中文](README.md) | English

# Bringing legacy PC QQ chat history into QQNT, then to Mac and phone: a full record of reconciliation, direct write and migration

This document records a real QQ chat history migration in three parts:

1. **Complete**: the importer built into QQNT brought over only part of the history stored by legacy PC QQ
   (`Msg3.0.db`). We reconciled every message on database copies, wrote the missing ones, swapped the database and checked
   the result in the client.
2. **Move to Mac**: move the completed Windows history to QQ on a Mac, compare both sides, and fill in missing messages and
   media.
3. **Move to phone**: use QQ's built-in "export to phone" to send the full history from the computer to an iPhone.

It covers why we did it, the problems we met, how we solved them, what worked and the pitfalls.

## 1. Summary

- **The QQNT importer does not import everything.** The main account had 524,471 private and group records in the legacy
  database; the importer brought over about 106,000. It skipped quoted replies, classic faces, groups the user had left,
  group messages without a sequence number, and local notices such as recalls and call status.
- **Missing messages can be written into a copy of the database, which then replaces the live file.** Every message type
  needs a sample written by QQNT itself first, and the generated rows are compared with that sample byte by byte. Every
  swap is rehearsed first and keeps a rollback copy.
- **The official backup and migration tools carry the written rows.** Gray tips, market faces, replies and messages of left
  groups all arrived intact through a computer-to-computer backup import and through export from computer to phone.
- **The official backup and export carry only the 200 most recent private chats.** They are picked by the time of the last
  message, newest first. Groups have no such limit. Accounts with more private chats must be exported in batches.
- **If export to phone hangs, the phone's QQ probably lacks the "Local Network" permission.** We checked Tailscale, the
  hotspot and IPv6 before finding that only this switch was off.
- **Pictures and files need their own work.** Once the messages are written, pictures must be placed where QQNT looks
  for them and old file paths must be changed. A file name or size does not prove an original; check the md5 recorded
  in the message.
- **Importing from the phone back to a computer brings deleted chats back and creates duplicates.** Clean up again by
  conversation and by message afterwards.
- The hardest part is not writing but **deciding what is still missing**. Reconcile one to one; do not trust file sizes or
  numbers shown in the UI.

## 2. Goal

Two QQ accounts on one Windows PC, both upgraded from legacy PC QQ to QQNT, plus a MacBook Air and an iPhone. The
requirements:

1. **Old history visible in QQNT**: text, pictures, faces and replies as close to the original as possible.
2. **Do not damage existing data**: no existing QQNT row may change.
3. **Delete what is not wanted**: groups and chats the owner no longer cares about are removed from QQNT and the legacy data.
4. **Complete history on the PC, the Mac and the phone**.
5. **Clean up at the end**: once the records are in QQNT, remove the legacy data and intermediate files, and keep one
   original backup on the external drive.

## 3. Problems

| Problem | Why it matters |
| --- | --- |
| Both databases are encrypted | `Msg3.0.db` and QQNT's `nt_msg.db` use different schemes and both need a key; the same account has different keys on Windows and Mac |
| QQNT adds a vendor header | `nt_msg.db` starts with 1024 extra bytes; without them it is a standard SQLCipher file |
| Fields are numbers | Tables and columns are numeric (`40001`, `40800`); message content is nested protobuf |
| The importer skips many types | Replies, classic and market faces, cards and local notices are skipped |
| Group order depends on sequence numbers | Group messages are shown by sequence `40003`, one row per number; many legacy notices have none |
| The client writes while in the tray | Closing the window is not enough; quit QQ from the tray |
| Official migration shows no real progress | During export to phone, the merge stage on the phone only shows "importing x/y"; a hang and a slow chat look the same |

## 4. Part 1: complete QQNT on Windows

### 4.1 Principles

- **Only change copies.** Confirm that QQ has quit from the tray and that the main database has no `-wal`/`-journal`, copy
  the live file, strip the vendor header and work on the copy.
- **Rehearse, then swap.** Rehearse on a file identical to the live one. For the real change, copy the live database again
  after QQ has quit, so messages received in between are kept.
- **Check every row.** Apart from the intended rows, every row of every table must be identical to the state before the
  change; `PRAGMA integrity_check` must pass and no new duplicate group sequence may appear.
- **Keep permissions and a rollback copy.** Stage the new file in the same folder so it inherits the ACL, compare it, then
  `os.replace` it over the live file. Keep the pre-swap live file as the rollback source.
- **Move the search indexes aside.** Move `group_msg_fts.db`, `buddy_msg_fts.db` and `msg_fts.db` away before the swap;
  QQNT rebuilds them on the next start.
- **Check in the client after each step.** After a swap the account owner opens QQ and looks at the listed samples at the
  given times.

### 4.2 Steps

1. **Baseline**: before migrating, copy the whole `nt_qq` folder and verify it byte by byte.
2. **Reconcile**: pair every legacy message with existing QQNT rows by conversation, time and random value; classify as
   present, writable or unsupported, with the reason.
3. **Learn the formats**: for each type find rows written by QQNT itself (importer output or messages sent and received by
   the client) and learn the field and element structure from them. For types the importer handles (replies, classic
   faces), regenerate them with the rule and compare with the importer's rows byte by byte.
4. **First write**: text and pictures whose format is verified.
5. **Delete as decided by the owner**: the owner first confirms which groups and chats are not wanted; their messages are
   deleted per conversation, then `VACUUM`. For groups the owner had left, a local HTML page lets the owner review them and
   choose which to keep.
6. **Write the remaining types**: local notices, market faces, cards, and the messages of the kept left groups.
7. **Small fixes**: restore pictures marked expired although the original exists; rewrite replies and classic faces with the
   verified rules.

### 4.3 Result (main account)

| Item | Result |
| --- | --- |
| Legacy private and group records | 524,471 |
| Imported by the QQNT importer | about 106,000 |
| Deleted at the owner's request | one large group (400,000 records), another group (about 9,000), 6 left groups and 2 private chats |
| Written into QQNT | text, pictures, replies, faces, local notices; all checked in the client |
| Deliberately not written | voice messages without audio, videos without the file, likely duplicates, group notices without a real sequence |
| Database size | about 160 MB before, about 35 MB after removing the large group and `VACUUM` |

### 4.4 Pictures and files

After the message rows are written, the client must also find the files.

**Pictures**
- QQNT looks for originals at `nt_data/Pic/<month>/Ori/<md5>.<extension>`; the month is that of the message time in
  the local time zone. Legacy QQ kept pictures under their old names in its `Image` folder.
- We placed each legacy picture at that location by the md5 and size recorded in the message, as a hard link where
  possible (same drive, no extra space). The legacy files stay untouched.
- A few messages still carry an old path in the original-picture fields (`45403`, `45812`); change it to the new path.
  The thumbnail field `45814` is separate; do not count it with the original fields.
- Pictures without an original are marked expired by the client (see 7.2); after placing the original, make them
  displayable again.

**Files**
- The path field `45403` of old file messages may still point to a drive letter that no longer exists (e.g. `D:`).
  We found the originals by size and md5 and changed the path to `::NTOSFull::<current path>`; the client then opened
  them.
- File state `45550`: 4 opens, 5 shows "file expired". Clicking an expired file makes the client change 4 to 5 itself.
  One message already had the right path and only needed `45550` set from 5 back to 4. Do not set every 5 to 4; first
  confirm that the original exists and the path is right.
- Same name and size do not prove an original: we found files with the same name and size but a different md5, and
  these must not be used.
- On Mac, private-chat files placed at `nt_data/File/Ori/<value of 45503>/<file name>` are shown as downloaded.
- Group files use a different structure and had almost no local originals; we left them alone.

**Scale**
The main account's legacy picture folder held about 57 GB in over 270,000 files. Media were checked by scripted full
rule checks plus stratified samples by conversation, year and type, with only a few samples opened in the client.

## 5. Part 2: move to Mac

### 5.1 Method

1. **Start with the official backup import**: export a backup on Windows and import it on the Mac.
2. **Compare both sides one to one**: take database snapshots on Windows and Mac and pair messages by "conversation + time +
   random value". Also compare the media files in both `nt_data` folders (relative path + size).
3. **Fill in the differences**:
   - Missing messages: insert them directly into a copy of the Mac database with the Mac key; no re-encryption is needed.
   - Missing media: copy them to the Mac `nt_data` under the same relative path, without overwriting existing files.
4. **Owner review of all chats**: a local HTML page shows the chat list on the left and the full conversation on the right,
   with a delete checkbox at the top right, and exports a deletion list at the end. The list is applied on both the Mac and
   Windows.

### 5.2 Findings

- **The official backup carries the written rows.** After moving one account from Windows to Mac by backup, both sides
  matched message for message, including the written gray tips, market faces, replies and messages of left groups.
- **The official backup does not carry original pictures.** The messages arrive but the originals stay on Windows and must
  be copied separately. Voice and video do arrive.
- **Only about the 200 most recent private chats are carried.** The other account had almost 300 private chats. After the
  backup import, every private chat whose last message was older than a certain date was missing, while all groups arrived.
  We filled them in by writing the database directly.
- **When writing a private chat on another device, also add a row to `nt_uid_mapping_table`.** The private-chat slot
  `40027` is allocated per device. If a newly allocated slot is not registered in the mapping table (slot, uid, QQ number),
  the chat opens empty in the client. With the mapping row added and the table's `sqlite_sequence` raised above the largest
  ID, it displays normally.
- **For a left group, the member panel shows the previous group's members.** This is not a data problem: these groups have
  no member data in `group_info.db`, so the client reuses the last member list it loaded.

### 5.3 Importing from the phone back to the Mac

After importing the phone's history back to the Mac, the Mac database grew from about 200 MB to about 640 MB, about
360,000 more rows. Compared one to one with the snapshot taken before, the new rows fell into four groups:

| Group | Rows | Action |
| --- | ---: | --- |
| Conversations deleted earlier that came back (60) | about 236,000 | deleted |
| Confirmed duplicates: same conversation and random value, at most 5 s apart, same sender and content | about 16,000 | the imported copy deleted |
| Possible duplicates: same random value and close in time, but sender or content differ | about 1,300 | left alone |
| Messages really new in kept conversations (only on the phone, never on a computer) | about 110,000 | kept |

**Findings**
- The deleted conversations came back because the phone still had them. Delete them on the phone too, or do not
  import the whole phone back again.
- **Group rows with conversation 0 are not a conversation of their own.** Many belong to kept groups: their `40021` is
  the group number while `40030` is 0. Never delete "group 0" as a whole; delete only the rows whose sequence repeats
  one already used in the kept group. Private chats also have notices with conversation 0.
- **Duplicate pictures:** in the same month folder, one md5 was stored as `.jpg`, `.png` and `.gif` with identical
  bytes. Most came from the earlier media copy from Windows, where the same picture had another extension. After a
  byte-by-byte comparison we deleted the copies the database does not refer to: 12,770 files, 4.9 GB.
- After deleting conversations, move the search databases (`buddy_msg_fts.db`, `group_msg_fts.db`, `msg_fts.db`) away so
  the client rebuilds them; otherwise search still finds the deleted chats.
- Without Python `sqlcipher3` on the Mac, Homebrew's `sqlcipher` command line can do the deletion; check the result on
  a copy.

Afterwards the Mac database was about 280 MB and the account folder went from about 18 GB to 13 GB. The client opened
it normally without rebuilding it.

## 6. Part 3: move to phone

### 6.1 Findings

- **Hanging at "loading" or "preparing chat history" is caused by the missing "Local Network" permission for QQ on the
  iPhone.** As soon as "Settings → Privacy & Security → Local Network → QQ" was turned on, it worked. Before finding this,
  we checked:
  - Tailscale on the computer: QQ on the computer actually listens on the Wi-Fi address, so it is not affected.
  - Wi-Fi with client isolation: possible, but not the cause this time.
  - Phone hotspot: on an IPv6-only carrier network, the `192.0.0.2` address the Mac gets is a translation address, and QQ
    does not open its transfer port on it. With a dual-stack APN the hotspot gives IPv4, but transfer still failed because
    the permission was still off.
- **Export to phone carries media files too.** The estimated size shown in the export dialog is inaccurate: it is based on
  the file index of the whole account (`files_in_chat.db`).
- **Export to phone has two stages.** First the data is sent to the phone; the phone's storage grows and the computer shows
  the export as successful. Then the phone merges each chat into its own database, showing only "importing x/y".
- **Only the 200 most recent private chats are carried, by time of the last message.** We watched the temporary files the
  computer wrote during export and confirmed that one export packed exactly 200 private chats and all groups; everything
  left out was older private chats. The time on the chat-list entry does not affect the choice.
- **A very large chat looks like a hang.** A private chat with about 100,000 messages and 2 GB of media kept the phone on the
  same number for a long time, but it finished after enough waiting.
- **Import on the phone merges.** A chat imported in several parts still ends up as one chat on the phone.

### 6.2 Exporting in batches

Since one export carries at most 200 private chats, let QQ "see" only one batch at a time:

1. Quit QQ on the computer from the tray and make a full backup of the live database.
2. Build a temporary database: keep only the messages of the chats in this batch and remove the messages of all other
   chats; leave the mapping table, contacts and chat-list entries unchanged.
3. Put the temporary database in place, open QQ, "export all" to the phone and wait for the phone to finish.
4. Quit QQ, find the new messages received while the temporary database was in place, restore the original database, merge
   them back, and verify the original database byte by byte.

The same method finds a hanging chat: when a batch hangs, split it in half. We exported all 331 chats of one account in 3
batches and found the "hanging" large chat by splitting.

## 7. Pitfalls

### 7.1 Client

- **Closing the window is not quitting.** QQ keeps writing from the tray. Before replacing the database, quit from the tray
  and confirm that no `QQ.exe` runs and the main database has no `-wal`.
- **The client changes the database too.** Browsing old chats made QQ mark failed downloads as expired: 33 pictures during
  the rehearsal, 53 at the real swap. Always base the real change on the live file taken after QQ quit.
- **Left groups are invisible.** Written group messages do not appear in the chat list or search, even with a chat-list
  entry added. The rows stay in the database, and the official backup and export carry them.
- **Leave chat-list entries to the client where possible.** When deleting chat history, the owner can remove the entry in the
  client with "remove from message list".
- **A copy taken while QQ runs can look corrupt.** Early copies taken with QQ running repeatedly reported
  `SQLITE_CORRUPT` in the group table; a copy taken after QQ had fully quit did not. Before calling a database damaged,
  make sure the copy was taken after QQ quit.
- **A legacy `Msg3.0.db` that will not open is not damaged.** It has an SQLite header but encrypted pages, so plain
  SQLite cannot read it.
- **A re-signed QQ copy is a dead end.** On Mac a copy of QQ can be re-signed and attached with a debugger, but the copy
  has none of the real account's permissions and cannot read its data, so it does not help to obtain keys.

### 7.2 Database

- **SQLCipher parameters**: page size 4096, `kdf_iter=4000`, `HMAC_SHA1`, `PBKDF2_HMAC_SHA512`. Strip the first 1024
  bytes to open it; put the original header back unchanged when packaging.
- **Group sequence**: group messages are ordered and shown by `(conversation, 40003)`, one row per number. The legacy
  sequence is in the message `Info`; when missing, the legacy table `group$<group>$Seq$` can be searched by time and
  random value. Use it only for a unique hit that is not taken yet in the group. **Do not borrow a neighbour's sequence**:
  it hides other messages.
- **Private-chat unique key** is `(slot 40027, random 40002, 40005)`; on a collision, increment `40005`. The slot must have
  a row in `nt_uid_mapping_table`.
- **Message IDs** carry the write time in the high 32 bits; element IDs follow the message ID in order. The same message
  usually has the same message ID on different devices, so it can be used for cross-device comparison.
- **`40058`** is local midnight of the message day, with the UTC offset in force at that time; get daylight saving time right.
- **Native replies and classic faces** can be learned from the importer's rows: a reply is element type 7; a classic face
  keeps its ID below 255, and for 255 the real ID is in a sub-field.
- **Local notices** ("you recalled a message", call failed, offline file received) can be stored as QQNT local gray tips:
  element type 8, sub-type 17, content `{"align":"center","items":[{"txt":"…","type":"nor"}]}`. Note that the importer
  itself writes none of these.
- **Market faces**: legacy type 12 holds the package ID, the face md5 and a key, which map to QQNT's market-face element
  (type 11). The client fetches the image from the server by ID.
- **Expired pictures**: after a failed download QQ adds field `45507` to the picture element and sets `45509` to 1. After
  placing the original locally, remove `45507` and set `45509` back to 0 so it displays.
- **Local path field `45812`**: each device writes its own path. When writing one device's messages to another, convert it
  to the target device's path or leave it out.

### 7.3 Reconciliation

- **Loose matching gives false positives.** Counting a legacy row as present when any row shares conversation, random and
  time can merge different messages. Reconcile one to one and check the sender.
- **"Writable" is not "should be written".** The importer never writes local notices such as recalls; whether to write them
  is the owner's decision. We wrote them and they display correctly.
- **Do not trust numbers in the UI.** Export estimates and folder sizes can mislead. Check which chats were actually packed
  and which rows the database actually holds.
- **Read earlier findings first.** When work passes between people or rounds, read the verified rules and conclusions
  before changing anything.
- **Accept in layers.** Message in the database → original matches the size and md5 in the message → the path the
  client really uses is right → it opens in the client. Passing one layer does not mean passing the next.
- **Leave full checks to scripts, not to people or an AI reading row by row.** For hundreds of thousands of messages,
  check every row by rule with a script, sample by strata, and look at only a few samples in the client.

## 8. Tools

### 8.1 Used

| Tool | Purpose |
| --- | --- |
| Official QQNT client (Windows, Mac, iPhone) | View, check, backup import, export to phone |
| [sqlcipher3](https://pypi.org/project/sqlcipher3/) | Open SQLCipher databases from Python |
| [protocol_dbkey](https://github.com/QQBackup/protocol_dbkey) | Get the key of the local QQ database on Mac |
| Python 3.14, Windows 10, macOS | Runtime |

### 8.2 Scripts in this repository

[`tools/`](tools/README.en.md) holds the scripts of all three parts:
- **Part 1**: one-to-one reconciliation of legacy records, checking the conversion rules against the importer's rows,
  writing into a database copy, and showing expired pictures again.
- **Part 2**: one-to-one comparison of two devices, writing missing messages, adding mapping rows, deleting
  conversations from a list, the review page and media comparison.
- **Part 3**: export to phone in batches.

They contain no account numbers, paths or keys. Every input is a command-line argument and keys are passed only
through environment variables. How to obtain keys or decrypt a legacy `Msg3.0.db` is not covered.

## 9. Notes

- Use these methods only on your own account and devices.
- Keys and decryption are for handling your own data only. Never upload or share keys, databases or chat content with
  anyone.
- Editing QQ's database directly is risky: the client may fail, data may be damaged, and it may disagree with the server.
  Keep a complete original backup before you start.
- QQ versions and data formats change. This document is based on QQNT 9.9.36.53644 for Windows and QQ 7.0.2 for Mac,
  November 2026.
