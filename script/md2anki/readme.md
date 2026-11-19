下面是**全部修复完成**的完整脚本：

- ✅ 前缀 `#ankid/`
- ✅ 无论有无tags自动追加 `bear-app`、自动去重
- ✅ 修复标签不更新bug：新增用addNote，更新笔记单独调用`updateNoteTags`覆盖标签
- ✅ TSV导出同步标签

```
import re
import yaml
import requests
import argparse
from pathlib import Path
from typing import List, Dict, Set

ANKI_CONNECT_URL = "[http://localhost:8765](http://localhost:8765)"

def anki_invoke(action: str, params: dict = None):
    payload = {"action": action, "version": 6, "params": params or {}}
    resp = requests.post(ANKI_CONNECT_URL, json=payload, timeout=10)
    resp.raise_for_status()
    ret = resp.json()
    if ret.get("error"):
        raise RuntimeError(f"AnkiConnect error: {ret['error']}")
    return ret.get("result")

def escape_anki(s: str) -> str:
    return s.replace('"', '\\"')

def get_parent_deck_chain(deck_name: str) -> List[str]:
    parts = deck_name.split("::")
    parents = []
    for i in range(len(parts)-1, 0, -1):
        parents.append("::".join(parts[:i]))
    return parents

def is_deck_empty(dk: str) -> bool:
    cids = anki_invoke("findCards", {"query": f'deck:"{escape_anki(dk)}"'})
    return len(cids) == 0

def parse_anki_markdown(md_text: str, source_file: str = "", quiet: bool = False) -> List[Dict]:
    cards = []
    all_block_pat = re.compile(r"<anki\s*(?P<attrs>[^>]*)>(?P<content>.*?)</anki>", re.DOTALL)
    all_matches = list(all_block_pat.finditer(md_text))
    if not quiet:
        print(f"\n[DEBUG {source_file}] 检测到<anki>标签总数量={len(all_matches)}")
    attr_pat = re.compile(
        r"""id=(['\"])(?P<id>.+?)\1\s*"""
        r"""type=(['\"])(?P<type>.+?)\3\s*"""
        r"""(?:tags=(['\"])(?P<tags>.*?)\5)?\s*"""
        r"""(?P<suspend_flag>suspend\b)?"""
    )
    for idx, m in enumerate(all_matches):
        attrs_raw = m.group("attrs")
        raw_block = m.group("content").strip()
        ok = False
        attr_match = attr_pat.search(attrs_raw)
        if not attr_match:
            print(f"⚠️ [{source_file}] 区块{idx}: <anki>标签缺少 id 或 type 属性，忽略该区块")
            continue
        gd_attr = attr_match.groupdict()
        uuid_val = gd_attr["id"].strip()
        card_type = gd_attr["type"].strip()
        tag_str = gd_attr.get("tags")
        need_suspend = bool(gd_attr.get("suspend_flag"))

        if not raw_block:
            print(f"⚠️ [{source_file}] 区块{idx}: <anki></anki> 内为空，识别不到卡片，跳过")
            continue
        raw_for_yaml = re.sub(
            r"^deck:\s*(.+)$",
            r'deck: "\1"',
            raw_block,
            flags=re.MULTILINE
        )
        try:
            meta = yaml.safe_load(raw_for_yaml)
            if not quiet:
                print(f"[DEBUG {source_file}] 区块{idx} yaml解析meta = {meta}")
            if meta is None or not isinstance(meta, dict):
                print(f"⚠️ [{source_file}] 区块{idx}: <anki> 内容不是有效的键值对，识别不到卡片，跳过")
            else:
                deck = meta.get("deck")
                if deck is None:
                    if not quiet:
                        print(f"[DEBUG {source_file}] 区块{idx} deck值={repr(deck)}")
                    print(f"⚠️ [{source_file}] 区块{idx}: 缺少 deck 字段，识别不到卡片，跳过")
                else:
                    deck = deck.strip()
                    if deck.startswith("#ankid/"):
                        deck = deck[len("#ankid/"):]
                    deck_name = deck.replace("/", "::")
                    card_type = card_type.lower()
                    if card_type not in ("basic", "cloze"):
                        print(f"⚠️ [{source_file}] 区块{idx}: type 必须是 basic / cloze，识别不到卡片，跳过")
                    else:
                        tags = []
                        if tag_str:
                            tags = [t.strip() for t in tag_str.split(",") if t.strip()]
                        # 自动追加 bear-app，去重
                        tags = list(set(tags + ["bear-app"]))
                        card = {
                            "uuid": uuid_val,
                            "type": card_type,
                            "deck": deck_name,
                            "tags": tags,
                            "suspend": need_suspend
                        }
                        if card_type == "basic":
                            front = meta.get("front")
                            back = meta.get("back")
                            if front is None or back is None:
                                print(f"⚠️ [{source_file}] 区块{idx}: basic 类型缺少 front / back，识别不到卡片，跳过")
                            else:
                                card["front"] = front
                                card["back"] = back
                                cards.append(card)
                                ok = True
                        else:
                            text = meta.get("text")
                            extra = meta.get("extra", "")
                            if text is None:
                                print(f"⚠️ [{source_file}] 区块{idx}: cloze 类型缺少 text，识别不到卡片，跳过")
                            elif not re.search(r"{{c\d+::", text):
                                print(f"⚠️ [{source_file}] 区块{idx}: cloze text必须包含挖空 {{c1::}}，识别不到卡片，跳过")
                            else:
                                card["text"] = text
                                card["extra"] = extra
                                cards.append(card)
                                ok = True
        except yaml.YAMLError as e:
            print(f"⚠️ [{source_file}] 区块{idx}: YAML解析失败 {e}，识别不到卡片，跳过")
        if not ok:
            print(f"⚠️ [{source_file}] 区块{idx}: <anki>标签解析完成，但未生成有效卡片，请检查内容")
    return cards

def push_to_anki(cards: List[Dict], toanki: bool = False, allow_delete: bool = False, quiet: bool = False):
    if not toanki:
        print("\n===== 仅预览模式，不写入Anki =====")
        for c in cards:
            print(c)
        return
    print("===== 🚀 正式推送模式 =====")
    local_uuid_set = {c["uuid"] for c in cards}
    deck_list = anki_invoke("deckNames")
    for card in cards:
        try:
            uuid_val = card["uuid"]
            deck_name = card["deck"]
            tags = card["tags"]
            suspend_flag = card["suspend"]
            if not quiet:
                print(f"🔄 uuid={uuid_val}, deck={deck_name}, suspend={suspend_flag}, tags={tags}")
            if deck_name not in deck_list:
                if not quiet:
                    print(f"📂 卡组不存在，自动创建: {deck_name}")
                anki_invoke("createDeck", {"deck": deck_name})
                deck_list.append(deck_name)
            note_id_list = anki_invoke("findNotes", {"query": f'uuid:{escape_anki(uuid_val)}'})
            note_id = note_id_list[0] if note_id_list else None
            note_fields = {}
            if card["type"] == "basic":
                note_fields["front"] = card["front"].strip()
                note_fields["back"] = card["back"].strip()
                note_fields["uuid"] = uuid_val
                model_name = "Basic-md2anki"
            else:
                note_fields["text"] = card["text"].strip()
                note_fields["extra"] = card["extra"].strip()
                note_fields["uuid"] = uuid_val
                model_name = "Cloze-md2anki"
            if note_id is not None:
                # 更新字段
                anki_invoke("updateNoteFields", {
                    "note": {
                        "id": note_id,
                        "fields": note_fields
                    }
                })
                # 单独更新标签（覆盖式）
                anki_invoke("updateNoteTags", {
                    "note": note_id,
                    "tags": tags
                })
                card_ids = anki_invoke("findCards", {"query": f"nid:{note_id}"})
                if not quiet:
                    print(f"♻️ 更新笔记 note_id={note_id}, card_ids={card_ids}, new_tags={tags}")
                deck_map = anki_invoke("getDecks", {"cards": card_ids})
                cid_to_deck = {}
                for dname, cids in deck_map.items():
                    for cid in cids:
                        cid_to_deck[cid] = dname
                need_move = any(cid_to_deck[cid] != deck_name for cid in card_ids)
                if need_move:
                    anki_invoke("changeDeck", {"cards": card_ids, "deck": deck_name})
                    print(f"📦 移动卡片 {card_ids} 到卡组 {deck_name}")
                else:
                    print(f"✅ 该笔记下全部卡片已在卡组 {deck_name}，无需移动")
            else:
                note_param = {
                    "deckName": deck_name,
                    "modelName": model_name,
                    "fields": note_fields,
                    "tags": tags
                }
                note_id = anki_invoke("addNote", {"note": note_param})
                card_ids = anki_invoke("findCards", {"query": f"nid:{note_id}"})
                print(f"➕ 新增笔记 uuid={uuid_val}, note_id={note_id}, card_ids={card_ids}")

            if note_id is not None:
                card_ids = anki_invoke("findCards", {"query": f"nid:{note_id}"})
                if suspend_flag:
                    anki_invoke("suspend", {"cards": card_ids})
                    print(f"⏸️ 暂停卡片 {card_ids}")
                else:
                    anki_invoke("unsuspend", {"cards": card_ids})
                    print(f"▶️ 取消暂停卡片 {card_ids}")

        except Exception as e:
            print(f"❌ 处理卡片异常 {card}: {e}")

    if allow_delete:
        try:
            all_notes = anki_invoke("findNotes", {"query": "uuid:*"})
            to_del_nids = []
            deleted_note_decks: Set[str] = set()

            for nid in all_notes:
                info = anki_invoke("notesInfo", {"notes": [nid]})[0]
                note_uuid = info["fields"]["uuid"]["value"]
                if note_uuid not in local_uuid_set:
                    to_del_nids.append(nid)
                    card_ids = anki_invoke("findCards", {"query": f"nid:{nid}"})
                    deck_map = anki_invoke("getDecks", {"cards": card_ids})
                    for dname in deck_map:
                        deleted_note_decks.add(dname)

            if to_del_nids:
                anki_invoke("deleteNotes", {"notes": to_del_nids})
                print(f"🗑️ 删除 {len(to_del_nids)} 条笔记（不在本次md中）")
            else:
                print("✅ 没有需要删除的笔记")

            if not deleted_note_decks:
                print("ℹ️ 本次没有笔记被删除，跳过卡组检查")
                return

            deck_to_delete = []
            parent_empty_warn = set()

            for dk in deleted_note_decks:
                if is_deck_empty(dk):
                    deck_to_delete.append(dk)
                parents = get_parent_deck_chain(dk)
                for p in parents:
                    if is_deck_empty(p):
                        parent_empty_warn.add(p)

            if deck_to_delete:
                del_list = sorted(deck_to_delete, key=lambda x: x.count("::"), reverse=True)
                anki_invoke("deleteDecks", {"decks": del_list, "cardsToo": True})
                print(f"🧹 清理本次删卡后变空的子卡组: {del_list}")
            else:
                print("✅ 本次删卡对应的子卡组均还有卡片，无需删除")

            if parent_empty_warn:
                print(f"⚠️ 检测到以下父卡组为空（仅提示，不会自动删除）: {sorted(parent_empty_warn)}")

        except Exception as e:
            print(f"⚠️ 删除/清理空卡组阶段出错: {e}")

def export_tsv(cards: List[Dict], out_path: str):
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        for c in cards:
            tag_str = ",".join(c["tags"])
            if c["type"] == "basic":
                line = f"{c['uuid']}\t{c['front']}\t{c['back']}\t{c['deck']}\t{tag_str}\t{c['suspend']}\n"
            else:
                line = f"{c['uuid']}\t{c['text']}\t{c['extra']}\t{c['deck']}\t{tag_str}\t{c['suspend']}\n"
            f.write(line)

def scan_md_files(folder: Path, quiet: bool = False) -> List[Dict]:
    all_cards = []
    for f in folder.glob("*.md"):
        try:
            text = f.read_text(encoding="utf-8")
            cards = parse_anki_markdown(text, source_file=f.name, quiet=quiet)
            all_cards.extend(cards)
        except Exception as e:
            print(f"❌ 读取文件失败 {f}: {e}")
    return all_cards

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Markdown同步Anki脚本")
    parser.add_argument("dir", nargs="?", default="./notes", help="笔记目录")
    parser.add_argument("--toanki", action="store_true", help="写入Anki；不加仅预览")
    parser.add_argument("--allow-delete", action="store_true", help="删除md不存在笔记；空子卡组自动删，空父卡组仅打印提示不删除；无关卡组不动")
    parser.add_argument("--quiet", action="store_true", help="安静模式，关闭DEBUG打印")
    args = parser.parse_args()
    note_folder = Path(args.dir)
    if not note_folder.exists():
        print(f"目录不存在：{note_folder.resolve()}")
        exit(1)
    result_cards = scan_md_files(note_folder, quiet=args.quiet)

    # 重复UUID校验
    uuid_list = [c["uuid"] for c in result_cards]
    seen = set()
    dup_set = set()
    for uid in uuid_list:
        if uid in seen:
            dup_set.add(uid)
        seen.add(uid)
    if dup_set:
        print(f"\n❌ 错误：检测到重复id(uuid)！重复id列表：{sorted(dup_set)}")
        print("❌ 终止执行，不会推送Anki、不会删除卡片、不会生成tsv！")
        exit(1)

    push_to_anki(result_cards, toanki=args.toanki, allow_delete=args.allow_delete, quiet=args.quiet)
    export_tsv(result_cards, "anki_export.tsv")
```

### 配套更新后的 README

# md2anki 脚本 README

## 功能简介

把 Markdown 文件里自定义的 `<anki>` 卡片同步到 Anki，基于 Anki-Connect。

- ✅ 支持 basic / cloze 两种卡片类型
- ✅ 可选 `suspend` 布尔标记：带则暂停卡片，不带则取消暂停
- ✅ 自动转换路径：`#ankid/地理/中国地理` → Anki卡组 `地理::中国地理`
- ✅ 标签自动追加 `bear-app`，不管你有没有填写 tags，都会带上该标签；自动去重
- ✅ 全局 UUID（id）查重，重复直接终止，防止冲突
- ✅ 更新已有笔记、新建笔记、自动移动卡片到目标卡组；**更新笔记时标签正常覆盖**
- ✅ `--allow-delete` 模式：删掉md里不存在的笔记；删除后自动清理空的**子卡组**，父卡组只告警不删除
- ✅ 导出 `anki_export.tsv` 备份（包含标签、suspend状态）
- ✅ 调试日志：新增/更新打印 note_id、card_ids；quiet 参数关闭 debug 输出

## 前置依赖

1. Anki 安装插件：Anki-Connect，地址：[https://foosoft.net/projects/anki-connect/](https://foosoft.net/projects/anki-connect/)
2. 开启 Anki，保证 Anki-Connect 监听 `[http://localhost:8765](http://localhost:8765)`
3. Python 依赖

```
pip install pyyaml requests
```

4. 在Anki中提前建好两个笔记类型（模型名称严格一致）

- `Basic-md2anki`：字段 `front`、`back`、`uuid`
- `Cloze-md2anki`：字段 `text`、`extra`、`uuid`> 
> ⚠️ uuid 字段必须存在，脚本依靠此字段定位笔记

## 语法示例

### 1. basic（问答卡片，不暂停）

```
<anki id="b001" type="basic" tags="计算机网络,TCP">
deck: #ankid/计算机网络
front: TCP和UDP区别？
back: 1.TCP面向连接；UDP无连接
2.TCP可靠；UDP不可靠
</anki>
```

### 2. cloze（挖空卡片，suspend暂停）

```
<anki id="abc002" type="cloze" tags="地理,首都" suspend>
deck: #ankid/地理/中国地理
text: 中国的首都是{{c1::北京}}
extra: 拓展：北京是直辖市
</anki>
```

- 属性说明
  - `id`：全局唯一标识（uuid），**不可重复**
  - `type`：`basic` / `cloze`
  - `tags`：逗号分隔标签，可选
  - `suspend`：布尔标记，只写关键字、不用赋值；有则暂停，无则取消暂停
  - `deck`：`#ankid/` 开头，`/` 自动转为Anki卡组分隔符 `::`
- 标签必须成对闭合：`<anki> ... </anki>`

## 命令使用

```
# 预览模式：只解析打印，不写入Anki
python md2anki.py ./notes

# 写入Anki（不自动删除，只新增/更新）
python md2anki.py ./notes --toanki

# 写入Anki + 开启自动删除（md中消失的笔记会被Anki删除，同步清理空子卡组）
python md2anki.py ./notes --toanki --allow-delete

# 安静模式，关闭debug日志
python md2anki.py ./notes --toanki --quiet
```

## 执行流程

1. 扫描目标目录全部 `.md` 文件，提取 `<anki>` 块
2. 校验所有 id，存在重复 → 直接退出，不做任何改动
3. 预览模式：输出解析结果，结束
4. toanki 模式：
   - 根据 uuid 查询Anki笔记
   - 找到：更新字段、**覆盖标签**、移动卡组，同步 suspend 状态
   - 没找到：新建笔记，带上标签与 suspend 状态
5. 开启 `--allow-delete`：
   - 找出Anki内uuid在本次md中不存在的笔记，批量删除
   - 遍历被删除笔记所属卡组，空子卡组自动删除
   - 父卡组为空只打印警告，不会删除
6. 导出 `anki_export.tsv`

## 日志标识

- `🔄` 处理卡片
- `➕` 新增笔记
- `♻️` 更新笔记
- `📂` 自动创建卡组
- `📦` 卡片移动卡组
- `⏸️` 暂停卡片
- `▶️` 取消暂停卡片
- `🗑️` 删除笔记
- `🧹` 删除空的子卡组
- `⚠️` 警告，不会中断流程
- `❌` 错误，单条卡片失败不影响其他卡片继续执行

## 注意事项 & 避坑

1. Anki必须保持打开状态，Anki-Connect正常运行，否则报错
2. 模型名称、字段名称不能写错（Basic-md2anki / Cloze-md2anki）
3. id一旦分配尽量不要修改；修改id脚本会当成一条全新笔记新增
4. suspend 是卡片状态，每次同步都会按md标记强制覆盖Anki里原有暂停状态
5. 标签是**全量覆盖**：脚本输出什么标签列表，Anki笔记就变成这些标签
6. `--allow-delete` 是高危参数：md删掉anki块，anki笔记就会被删除，重要笔记谨慎使用
7. cloze 的 text 里必须包含 `{{c1::内容}}` 挖空语法，否则会跳过该卡片
8. 导出文件 `anki_export.tsv` 在脚本同目录，每次运行覆盖

## 文件结构示例

```
md2anki.py
notes/
├─ input.md
└─ 地理.md
anki_export.tsv
```

直接复制保存为 `md2anki.py` 即可运行。

> 
> 小提示：测试的时候先不加 `--allow-delete`，用预览模式看打印出来的tags是否符合预期。

有问题随时跟我说！
