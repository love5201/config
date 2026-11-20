import re
import yaml
import requests
import argparse
from pathlib import Path
from typing import List, Dict, Set

ANKI_CONNECT_URL = "http://localhost:8765"

def anki_invoke(action: str, params: dict = None):
    payload = {"action": action, "version": 6}
    if params is not None:
        payload["params"] = params
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

def normalize_deck(deck_raw: str) -> str:
    r"""
    去掉前缀: #ankid::  / #ankid/ / #
    把 / 、\ 全部替换成 ::
    """
    s = deck_raw.strip()
    if s.startswith("#ankid::"):
        s = s.removeprefix("#ankid::")
    elif s.startswith("#ankid/"):
        s = s.removeprefix("#ankid/")
    elif s.startswith("#"):
        s = s.lstrip("#")
    s = s.replace("/", "::")
    s = s.replace("\\", "::")
    return s

def parse_anki_block(block_content: str) -> Dict | None:
    """解析单个 ```anki 块，返回卡片dict，失败返回None"""
    lines = block_content.splitlines()
    state = "header"
    suspend = False
    deck_raw = None
    tags_raw = None
    ankid = None
    body_lines = []

    for line in lines:
        stripped = line.strip()
        if state == "header":
            low = stripped.lower()
            if low == "@suspend":
                suspend = True
                continue
            if stripped.startswith("Deck:"):
                deck_raw = stripped.removeprefix("Deck:").strip()
                continue
            if stripped.startswith("Tags:"):
                tags_raw = stripped.removeprefix("Tags:").strip()
                continue
            if stripped.startswith("ankid:"):
                ankid = stripped.removeprefix("ankid:").strip()
                continue
            # 遇到非头部标记：切换状态
            state = "body"
        if state == "body":
            body_lines.append(line)

    if not ankid or not deck_raw:
        return None

    deck_name = normalize_deck(deck_raw)

    tag_list = []
    if tags_raw is not None and tags_raw.strip():
        raw_parts = re.split(r"[,，\s]+", tags_raw.strip())
        tag_list = [t.strip() for t in raw_parts if t.strip()]

    body_text = "\n".join(body_lines)
    if "---" in body_text:
        front_text, extra_text = body_text.split("---", 1)
        front_text = front_text.rstrip("\n")
        extra_text = extra_text.lstrip("\n")
    else:
        front_text = body_text
        extra_text = ""

    front_text = front_text.strip()
    if not front_text:
        return None

    cloze_pattern = re.compile(r"{{\s*(.+?)\s*}}")
    cloze_matches = cloze_pattern.findall(front_text)
    if cloze_matches:
        counter = 1
        def repl(m):
            nonlocal counter
            out = f"{{{{c{counter}::{m.group(1)}}}}}"
            counter += 1
            return out
        cloze_text = cloze_pattern.sub(repl, front_text)
        return {
            "ankid": ankid,
            "type": "cloze",
            "deck": deck_name,
            "tags": tag_list,
            "suspend": suspend,
            "text": cloze_text,
            "extra": extra_text
        }
    else:
        back_text = extra_text.strip()
        if not back_text:
            return None
        return {
            "ankid": ankid,
            "type": "basic",
            "deck": deck_name,
            "tags": tag_list,
            "suspend": suspend,
            "front": front_text,
            "back": back_text
        }

def parse_anki_markdown(md_text: str, source_file: str = "", quiet: bool = False, append_tags: List[str] = None) -> List[Dict]:
    if append_tags is None:
        append_tags = ["bear-app"]
    cards = []

    anki_block_pat = re.compile(r"```anki\s*\n(?P<content>.*?)\n```", re.DOTALL)
    anki_blocks = list(anki_block_pat.finditer(md_text))
    if not quiet:
        print(f"\n[DEBUG {source_file}] 检测到```anki块总数量={len(anki_blocks)}")
    for idx, m in enumerate(anki_blocks):
        raw_block = m.group("content")
        card = parse_anki_block(raw_block)
        if card is None:
            print(f"⚠️ [{source_file}] 区块{idx}: ```anki解析失败，跳过")
            continue
        card["tags"] = list(set(card["tags"] + append_tags))
        cards.append(card)
        if not quiet:
            print(f"✅ [{source_file}] anki块解析成功 ankid={card['ankid']}, type={card['type']}, suspend={card['suspend']}")

    lines = md_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if re.match(r"^- ", line):
            attr_match = re.search(r"<!--\s*(ankid\s*:.+?)\s*-->", line)
            if not attr_match:
                i += 1
                continue
            attr_raw = attr_match.group(1).strip()
            attr_parts = [p.strip() for p in attr_raw.split(";") if p.strip()]
            prop_dict = {}
            for part in attr_parts:
                if ":" in part:
                    k, v = part.split(":", 1)
                    k = k.strip()
                    v = v.strip()
                    prop_dict[k] = v
                else:
                    prop_dict[part.strip()] = True
            ankid_val = prop_dict.get("ankid")
            if not ankid_val:
                print(f"⚠️ [{source_file}] 简易行：ankid id缺失：{line}")
                i += 1
                continue
            need_suspend = "suspend" in prop_dict
            tag_list = []
            raw_tags = prop_dict.get("tags")
            if raw_tags and isinstance(raw_tags, str) and raw_tags.strip():
                tag_list = re.split(r"[,，\s]+", raw_tags.strip())
                tag_list = [t.strip() for t in tag_list if t.strip()]

            # ==========修复此处正则，消除SyntaxWarning==========
            deck_match = re.search(r"#(ankid(::|/))?([\w\-/\\\\]+)", line)
            if not deck_match:
                print(f"⚠️ [{source_file}] 简易行：缺少卡组标记 #xxx / #ankid/xxx / #ankid::xxx：{line}")
                i += 1
                continue
            deck_raw = deck_match.group(3)
            deck_name = normalize_deck(deck_raw)

            main_text = re.sub(r"<!--\s*ankid\s*:.+?\s*-->", "", line)
            main_text = re.sub(r"#(ankid(::|/))?([\w\-/\\\\]+)", "", main_text).strip("- ").strip()

            extra_lines = []
            j = i + 1
            while j < len(lines):
                subline = lines[j]
                if re.match(r"^\s+- ", subline):
                    extra_lines.append(subline.strip("- ").strip())
                    j += 1
                else:
                    break
            extra = "\n".join(extra_lines)

            if "::" in main_text:
                front, back = main_text.split("::", 1)
                front, back = front.strip(), back.strip()
                if not front or not back:
                    print(f"⚠️ [{source_file}] 简易Basic正反面为空：{line}")
                    i = j
                    continue
                card = {
                    "ankid": ankid_val,
                    "type": "basic",
                    "deck": deck_name,
                    "tags": list(set(tag_list + append_tags)),
                    "suspend": need_suspend,
                    "front": front,
                    "back": back
                }
                cards.append(card)
                if not quiet:
                    print(f"✅ [{source_file}] 简易Basic解析成功 ankid={ankid_val}, suspend={need_suspend}")
            else:
                cloze_pattern = re.compile(r"{{\s*(.+?)\s*}}")
                cloze_matches = cloze_pattern.findall(main_text)
                if not cloze_matches:
                    print(f"⚠️ [{source_file}] 简易cloze：无{{}}挖空标记：{line}")
                    i = j
                    continue
                counter = 1
                def repl(m):
                    nonlocal counter
                    out = f"{{{{c{counter}::{m.group(1)}}}}}"
                    counter += 1
                    return out
                cloze_text = cloze_pattern.sub(repl, main_text)
                card = {
                    "ankid": ankid_val,
                    "type": "cloze",
                    "deck": deck_name,
                    "tags": list(set(tag_list + append_tags)),
                    "suspend": need_suspend,
                    "text": cloze_text,
                    "extra": extra
                }
                cards.append(card)
                if not quiet:
                    print(f"✅ [{source_file}] 简易Cloze解析成功 ankid={ankid_val}, suspend={need_suspend}")
            i = j
        else:
            i += 1
    return cards

def export_tsv(cards: List[Dict], out_path: str):
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        for c in cards:
            tag_str = ",".join(c["tags"])
            if c["type"] == "basic":
                line = f"{c['ankid']}\t{c['front']}\t{c['back']}\t{c['deck']}\t{tag_str}\t{c['suspend']}\n"
            else:
                line = f"{c['ankid']}\t{c['text']}\t{c['extra']}\t{c['deck']}\t{tag_str}\t{c['suspend']}\n"
            f.write(line)

def scan_md_files(folder: Path, quiet: bool = False, append_tags: List[str] = None) -> List[Dict]:
    all_cards = []
    for f in folder.glob("*.md"):
        try:
            text = f.read_text(encoding="utf-8")
            cards = parse_anki_markdown(text, source_file=f.name, quiet=quiet, append_tags=append_tags)
            all_cards.extend(cards)
        except Exception as e:
            print(f"❌ 读取文件失败 {f}: {e}")
    return all_cards

def push_to_anki(cards: List[Dict], toanki: bool = False, allow_delete: bool = False, quiet: bool = False):
    if not toanki:
        print("\n===== 仅预览模式，不写入 Anki =====")
        for c in cards:
            print(c)
        return
    print("===== 🚀 正式推送模式 =====")
    local_ankid_set = {c["ankid"] for c in cards}
    deck_list = anki_invoke("deckNames")
    empty_deck_candidates = set()

    for card in cards:
        try:
            ankid_val = card["ankid"]
            deck_name = card["deck"]
            tags = card["tags"]
            suspend_flag = card["suspend"]
            if not quiet:
                print(f"🔄 ankid={ankid_val}, deck={deck_name}, suspend={suspend_flag}, tags={tags}")
            if deck_name not in deck_list:
                if not quiet:
                    print(f"📂 卡组不存在，自动创建: {deck_name}")
                anki_invoke("createDeck", {"deck": deck_name})
                deck_list.append(deck_name)

            note_ids = anki_invoke("findNotes", {"query": f'ankid:"{escape_anki(ankid_val)}"'})
            note_id = note_ids[0] if note_ids else None

            note_fields = {}
            if card["type"] == "basic":
                note_fields["front"] = card["front"]
                note_fields["back"] = card["back"]
                note_fields["ankid"] = ankid_val
                model_name = "Basic-md2anki"
            else:
                note_fields["text"] = card["text"]
                note_fields["extra"] = card["extra"]
                note_fields["ankid"] = ankid_val
                model_name = "Cloze-md2anki"

            if note_id is not None:
                anki_invoke("updateNoteFields", {"note": {"id": note_id, "fields": note_fields}})
                anki_invoke("updateNoteTags", {"note": note_id, "tags": tags})
                card_ids = anki_invoke("findCards", {"query": f'nid:{note_id}'})
                if not quiet:
                    print(f"♻️ 更新笔记 note_id={note_id}, card_ids={card_ids}, tags={tags}")
                deck_map = anki_invoke("getDecks", {"cards": card_ids})
                old_decks = [d for d in deck_map.keys() if d != deck_name]
                if old_decks:
                    anki_invoke("changeDeck", {"cards": card_ids, "deck": deck_name})
                    print(f"📦 移动卡片 {card_ids} 到卡组 {deck_name}")
                    for old_dk in old_decks:
                        if is_deck_empty(old_dk):
                            empty_deck_candidates.add(old_dk)
                elif not quiet:
                    print("✅ 该笔记卡片已在目标卡组，无需移动")
            else:
                note_id = anki_invoke("addNote", {
                    "note": {
                        "deckName": deck_name,
                        "modelName": model_name,
                        "fields": note_fields,
                        "tags": tags
                    }
                })
                card_ids = anki_invoke("findCards", {"query": f'nid:{note_id}'})
                print(f"➕ 新增笔记 ankid={ankid_val}, note_id={note_id}, card_ids={card_ids}")

            card_ids = anki_invoke("findCards", {"query": f'nid:{note_id}'})
            if suspend_flag:
                anki_invoke("suspend", {"cards": card_ids})
                if not quiet:
                    print(f"⏸️ 暂停卡片 {card_ids}")
            else:
                anki_invoke("unsuspend", {"cards": card_ids})
                if not quiet:
                    print(f"▶️ 恢复卡片 {card_ids}")
        except Exception as e:
            print(f"❌ 处理卡片异常 [ankid={card.get('ankid')}]: {e}")

    if allow_delete:
        try:
            all_notes = anki_invoke("findNotes", {"query": "ankid:*"})
            to_del_nids = []
            deleted_note_decks: Set[str] = set()
            if all_notes:
                notes_info = anki_invoke("notesInfo", {"notes": all_notes})
                cards_of_deleted = []
                for info in notes_info:
                    note_ankid = info.get("fields", {}).get("ankid", {}).get("value", "").strip()
                    if note_ankid and note_ankid not in local_ankid_set:
                        to_del_nids.append(info["noteId"])
                        cards_of_deleted.extend(info.get("cards", []))
                if cards_of_deleted:
                    deck_map = anki_invoke("getDecks", {"cards": cards_of_deleted})
                    deleted_note_decks.update(deck_map.keys())
            if to_del_nids:
                anki_invoke("deleteNotes", {"notes": to_del_nids})
                print(f"🗑️ 删除 {len(to_del_nids)} 条笔记（Markdown 中已不存在）")
            else:
                print("✅ 没有需要删除的笔记")

            all_candidate_decks = (deleted_note_decks | empty_deck_candidates) - {"Default"}
            if not all_candidate_decks:
                print("ℹ️ 本次无空卡组待清理")
                return
            deck_to_delete: List[str] = []
            parent_empty_warn: Set[str] = set()
            for dk in all_candidate_decks:
                if is_deck_empty(dk):
                    deck_to_delete.append(dk)
                for p in get_parent_deck_chain(dk):
                    if p != "Default" and is_deck_empty(p):
                        parent_empty_warn.add(p)
            if deck_to_delete:
                deck_to_delete_sorted = sorted(deck_to_delete, key=lambda x: x.count("::"), reverse=True)
                anki_invoke("deleteDecks", {"decks": deck_to_delete_sorted, "cardsToo": True})
                print(f"🧹 清理空卡组: {deck_to_delete_sorted}")
            else:
                print("✅ 没有空卡组需要删除")
            if parent_empty_warn:
                print(f"⚠️ 检测到父卡组为空（不会自动删除）: {sorted(parent_empty_warn)}")
        except Exception as e:
            print(f"⚠️ 笔记删除流程异常: {e}")

if __name__ == "__main__":
    import sys
    parser = argparse.ArgumentParser(description="Markdown同步Anki脚本，```anki + 简易短横线格式")
    parser.add_argument("dir", nargs="?", default="./notes", help="笔记目录")
    parser.add_argument("--toanki", action="store_true", help="写入Anki；不加则预览")
    parser.add_argument("--allow-delete", action="store_true", help="删除md不存在笔记，清理空卡组")
    parser.add_argument("--quiet", action="store_true", help="安静模式")
    parser.add_argument("--append-tags", default="bear-app", help="追加标签，逗号分隔")
    args = parser.parse_args()
    note_folder = Path(args.dir)
    if not note_folder.exists():
        print(f"❌ 目录不存在：{note_folder.resolve()}")
        sys.exit(1)
    append_tags = [t.strip() for t in args.append_tags.split(",") if t.strip()]
    result_cards = scan_md_files(note_folder, quiet=args.quiet, append_tags=append_tags)

    seen = set()
    dup_set = set()
    for c in result_cards:
        aid = c["ankid"]
        if aid in seen:
            dup_set.add(aid)
        seen.add(aid)
    if dup_set:
        print(f"\n❌ 错误：检测到重复ankid！{sorted(dup_set)}")
        print("❌ 终止运行，不会推送、删除、导出")
        sys.exit(1)

    push_to_anki(result_cards, toanki=args.toanki, allow_delete=args.allow_delete, quiet=args.quiet)
    export_tsv(result_cards, "anki_export.tsv")
