中文 | [English](README.en.md)

# 工具

这些脚本对应[主文档](../README.md)的三段：补全旧记录、迁到 Mac、导到手机。它们不包含任何账号、路径或密钥，所有输入都通过命令行参数传入。

## 前提

- Python 3.10 或更高版本，并安装 [sqlcipher3](https://pypi.org/project/sqlcipher3/)：`pip install sqlcipher3`。用到 `--tz` 的工具在 Windows 上还需要 `pip install tzdata`。
- 已经取得自己账号的数据库密钥。本仓库不提供取得密钥的方法。在 Mac 上可以参考 [protocol_dbkey](https://github.com/QQBackup/protocol_dbkey)。
- 补全旧记录时，需要一份**已解密**的旧版 `Msg3.0.db`，也就是一个普通的 SQLite 文件。本仓库不提供解密方法。
- 密钥只通过环境变量传入。参数 `--key-env` 填的是环境变量的**名称**，不是密钥本身。
- 在 Windows 上，可以用 `qq_key.ps1` 把密钥保存为 DPAPI 加密文件，再从文件加载到环境变量里。

## 安全规则

- **先从托盘退出 QQ。** 主库旁边如果有不为空的 `-wal` 或 `-journal` 文件，工具会拒绝运行。
- **不直接修改实库。** 工具先把数据库复制到临时目录，在副本上操作，再把结果写成一个新文件，并原样接上原来的 1024 字节厂商头。
- **输出文件必须是新文件。** 如果输出文件已存在，工具会直接退出，不会覆盖。
- **自动核对。** 会修改数据的工具，都会确认除了要改的行以外，每张表的每一行都没有变化，并检查 `PRAGMA integrity_check`。
- **替换实库由你自己完成。** 替换前先保留原库作为回滚源，把搜索索引（`*_msg_fts.db`）移到别处，然后再换上新库，最后在客户端里验收。
- 审核网页和 `new_rows.json` 里有聊天内容。只在自己的电脑上使用，用完就删除。

## 工具一览

| 脚本 | 用途 | 是否修改数据 |
| --- | --- | --- |
| `legacy_reconcile.py` | 把旧版记录和 QQNT 已有的消息逐条配对，找出缺少的记录 | 只读 |
| `legacy_check.py` | 用导入器自己写的行，逐条核对转换规则 | 只读 |
| `legacy_write.py` | 把缺少的旧版记录写进 QQNT 的库副本 | 生成新库 |
| `unexpire_pictures.py` | 有原图却被标成"已过期"的图片，恢复显示 | 生成新库 |
| `db_stats.py` | 完整性检查、行数、会话数 | 只读 |
| `compare_devices.py` | 逐条对比两台设备上同一个账号的消息，按会话列出差异 | 只读 |
| `copy_missing.py` | 把 A 有、B 没有的消息写进 B 的库副本，并转换本机路径字段 `45812` | 生成新库 |
| `fix_uid_mapping.py` | 给缺少 `nt_uid_mapping_table` 映射行的私聊补上映射行 | 生成新库 |
| `purge_conversations.py` | 按清单删除整个会话，然后执行 `VACUUM` | 生成新库 |
| `review_conversations.py` | 生成本地审核网页，勾选要删除的会话，并导出删除清单 | 只读 |
| `phone_batches.py` | 分批导出到手机：分批、生成临时库、收集临时库期间收到的新消息、合并回原库 | 生成新库 |
| `list_media.py`、`compare_media.py` | 列出并对比两边 `nt_data` 里的媒体文件，生成复制清单 | 只读 |
| `qq_key.ps1` | Windows 上用 DPAPI 保存和加载密钥 | — |

每个脚本的详细说明，用 `python <脚本> --help` 查看。

## 例子

### 把旧版记录补进 QQNT

```sh
# 1. 找出缺少的记录。可以同时传入多份旧库（例如实库和更早的备份），相同的记录只算一次
python tools/legacy_reconcile.py --legacy Msg3.0.plain.db --qqnt nt_msg.db --key-env QQ_KEY \
       --exclude not_wanted.tsv --out reconcile.sqlite
# 2. 用导入器自己写的行核对转换规则：哪一类不一致，就先不要写这一类
python tools/legacy_check.py --reconcile reconcile.sqlite --qqnt nt_msg.db --key-env QQ_KEY \
       --self <自己的 QQ 号> --tz Asia/Shanghai
# 3. 先挑一两个会话演练，在客户端里验收后，再写全部
python tools/legacy_write.py --reconcile reconcile.sqlite --qqnt nt_msg.db --key-env QQ_KEY \
       --self <自己的 QQ 号> --tz Asia/Shanghai --nt-data <账号的 nt_data> --only pilot.tsv --out nt_msg.pilot.db
# 4. 有原图却显示"已过期"的图片，恢复显示
python tools/unexpire_pictures.py nt_msg.db --key-env QQ_KEY --nt-data <账号的 nt_data> --tz Asia/Shanghai --out nt_msg.shown.db
```

`--tz` 填运行 QQ 的那台电脑的时区，例如 `Asia/Shanghai`。

`legacy_write.py` 的规则：
- 只写 QQNT 里已经有的会话。
- **删掉的会话一定要用 `--exclude` 排除。** 如果你还在那个群里，群里会继续收到新消息，QQNT 里就一直有这个会话，删掉的旧记录会被重新写回去。`legacy_reconcile.py` 会列出缺得最多的会话，写入前先看一遍。
- 在同一个会话里，QQNT 已经有相同随机数的消息、只是时间相差较大的记录，很可能是同一条消息，默认不写。加 `--write-possible-duplicates` 时才写。
- 群消息只用真实序号，并且这个序号在群里还没有被占用；不借用相邻消息的序号。
- 系统账号发出的撤回提示默认不写，与导入器一致。加 `--recall-notices write` 时，写成灰条。
- 用 `--kinds` 可以限定写哪几类：`content`、`gray_tip`、`market_face`、`ark`、`text`。

写完后，按 `<out>.samples.tsv` 里的消息 ID 和时间，在客户端里抽查。`reconcile.sqlite` 里有聊天内容，用完就删除。

### 迁到 Mac 后补齐差异

```sh
# 1. 退出两边的 QQ，把两边的 nt_msg.db 复制到同一台电脑上，设置环境变量 WIN_KEY、MAC_KEY
python tools/compare_devices.py --a win/nt_msg.db --a-key-env WIN_KEY --a-name win \
                                --b mac/nt_msg.db --b-key-env MAC_KEY --b-name mac --out cmp
# 2. 把 Windows 有、Mac 没有的消息写进 Mac 库的副本
python tools/copy_missing.py --src win/nt_msg.db --src-key-env WIN_KEY --dst mac/nt_msg.db --dst-key-env MAC_KEY \
       --out mac/nt_msg.new.db --src-data 'C:\...\nt_qq\nt_data' --dst-data '/Users/.../nt_qq_xxx/nt_data'
# 3. 给新私聊补映射行
python tools/fix_uid_mapping.py mac/nt_msg.new.db --key-env MAC_KEY --out mac/nt_msg.final.db
# 4. 再对比一次：Windows 那边应该不再有 Mac 缺少的消息
# 5. 媒体文件：两边各自列出文件，再按复制清单复制到 Mac，不覆盖已有文件
python tools/list_media.py <nt_data> --out win.tsv          # Mac 上同样生成 mac.tsv
python tools/compare_media.py win.tsv --a-name win mac.tsv --b-name mac --out media
```

### 审核并删除会话

```sh
python tools/review_conversations.py nt_msg.db --key-env QQ_KEY --self <自己的 QQ 号> --out review.html
# 在浏览器里打开 review.html，勾选要删除的会话，导出为 delete_list.tsv
python tools/purge_conversations.py nt_msg.db --key-env QQ_KEY --list delete_list.tsv --out nt_msg.purged.db
```

同一份清单可以在每台设备的库上各执行一次。

不要把会话号 0 放进清单。会话号为 0 的行不是一个会话，其中很多属于保留的群或私聊（看它们的 `40021`）。

### 分批导出到手机

```sh
python tools/phone_batches.py split nt_msg.db --key-env QQ_KEY --out-dir batches --batches 3
# 对每一批：
python tools/phone_batches.py build nt_msg.db --key-env QQ_KEY --batch batches/batch1.tsv --out temp1.db
#   换上 temp1.db -> 打开 QQ -> 导出全部到手机 -> 等手机导完 -> 从托盘退出 QQ
python tools/phone_batches.py collect nt_msg.db --temp <QQ 用过的 temp1.db> --key-env QQ_KEY --out new_rows1.json
#   换回原库；collect 找到了新消息时再合并：
python tools/phone_batches.py merge nt_msg.db --key-env QQ_KEY --rows new_rows1.json --out nt_msg.merged.db
```

一批里的私聊不要超过 200 个。哪一批卡住了，就把那一批对半拆开，再分别导出。

## 已知限制

- `copy_missing.py` 只写消息行，不写会话列表项（`recent_contact_v3_table`）。没有会话列表项的私聊，导出到手机时可能不会被打包。
- `compare_media.py` 按相对路径比较。如果目标设备上已经有同一 md5、但扩展名不同的图片，复制清单里仍会列出它，复制后就多出一份重复文件。复制前，先按 md5 去掉这类文件。
- `copy_missing.py` 会跳过两种消息：消息 ID 在目标库里已被占用的，以及群序号已被占用的群消息。跳过的数量会写在报告里。
- `legacy_write.py` 不写语音、视频，也不写 QQNT 里没有的会话。
- `legacy.py` 的转换规则，是在一次真实迁移中按导入器的输出逐字节核对得到的。整理后，又在一个真实账号上重新核对了一遍：
  - 106,560 条导入器写的行里，消息类型和 `40058` 全部一致。
  - 当时写进去的 1,034 条行里，1,019 条和现在生成的逐字节一致。其余 15 条，是客户端后来改了图片文件名，以及发送人 uid 后来才能查到。
- 和导入器仍然不同、但不影响显示的地方：
  - 图片文件名 `45402`：工具保留旧版文件名。客户端显示图片后，会自己改成"md5 + `.png`"。
  - 回复后面带多段文字时，导入器有时会去掉开头的 "@名字" 和空格（约 10%），工具保留旧版 PC QQ 显示的全部内容。
  - 应用卡片（旧版类型 27）：导入器写成空消息，工具写成卡片元素。
- 用在自己的数据上时，先运行 `legacy_check.py`。
- 这些工具基于 Windows 版 QQNT 9.9.36 和 Mac 版 QQ 7.0.2，在其他版本上，表结构可能不同。
