import re
import yaml
import requests
import argparse
from pathlib import Path
from typing import List, Dict, Set

ANKI_CONNECT_URL = "http://localhost:8765"

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
