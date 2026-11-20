import re
import sys
import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set
import requests

ANKI_CONNECT_URL = "http://localhost:8765"
CLOZE_PATTERN = re.compile(r"\{\{\s*(.+?)\s*\}\}")
DECK_TAG_PATTERN = re.compile(r"#(?:ankid(?:::|/))?([\w\-/\\]+)")


@dataclass
class Card:
    ankid: str
    card_type: str  # "basic" | "cloze"
    deck: str
    tags: List[str] = field(default_factory=list)
    suspend: bool = False
    front: str = ""
    back: str = ""


def anki_invoke(action: str, params: Optional[dict] = None):
    payload = {"action": action, "version": 6}
    if params is not None:
        payload["params"] = params
    resp = requests.post(ANKI_CONNECT_URL, json=payload, timeout=15)
    resp.raise_for_status()
    ret = resp.json()
    if ret.get("error"):
        raise RuntimeError(f"AnkiConnect [{action}] error: {ret['error']}")
    return ret.get("result")


def escape_anki(s: str) -> str:
    return s.replace('"', '\\"')


def normalize_deck(deck_raw: str) -> str:
    s = deck_raw.strip()
    for prefix in ("#ankid::", "#ankid/"):
        if s.startswith(prefix):
            s = s.removeprefix(prefix)
            break
    else:
        s = s.lstrip("#")
    s = s.replace("/", "::").replace("\\", "::")
    parts = [p.strip() for p in s.split("::") if p.strip()]
    return "::".join(parts)


def parse_tags(raw: Optional[str]) -> List[str]:
    if not raw or not raw.strip():
        return []
    parts = re.split(r"[,，\s]+", raw.strip())
    # Anki 标签不支持空格，替换为下划线并去重保序
    cleaned = [re.sub(r"\s+", "_", p.strip()) for p in parts if p.strip()]
    return list(dict.fromkeys(cleaned))


def to_cloze_text(text: str) -> Optional[str]:
    if not CLOZE_PATTERN.search(text):
        return None
    counter = 0

    def repl(match: re.Match) -> str:
        nonlocal counter
        counter += 1
        content = match.group(1).strip()
        return f"{{{{c{counter}::{content}}}}}"

    return CLOZE_PATTERN.sub(repl, text)


def get_parent_deck_chain(deck_name: str) -> List[str]:
    parts = deck_name.split("::")
    return ["::".join(parts[:i]) for i in range(len(parts) - 1, 0, -1)]


def is_deck_empty(deck_name: str) -> bool:
    cids = anki_invoke("findCards", {"query": f'deck:"{escape_anki(deck_name)}"'})
    return len(cids) == 0


def parse_anki_block(block_content: str) -> Optional[Card]:
    lines = block_content.splitlines()
    in_header = True
    suspend = False
    deck_raw: Optional[str] = None
    tags_raw: Optional[str] = None
    ankid: Optional[str] = None
    body_lines: List[str] = []

    for line in lines:
        stripped = line.strip()
        if in_header:
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
            in_header = False
        body_lines.append(line)

    if not ankid or not deck_raw:
        return None

    deck_name = normalize_deck(deck_raw)
    tag_list = parse_tags(tags_raw)

    body_text = "\n".join(body_lines)
    if "---" in body_text:
        front_raw, extra_raw = body_text.split("---", 1)
        front_text = front_raw.strip()
        extra_text = extra_raw.strip()
    else:
        front_text = body_text.strip()
        extra_text = ""

    if not front_text:
        return None

    cloze_text = to_cloze_text(front_text)
    if cloze_text is not None:
        return Card(
            ankid=ankid,
            card_type="cloze",
            deck=deck_name,
            tags=tag_list,
            suspend=suspend,
            front=cloze_text,
            back=extra_text,
        )

    if not extra_text:
        return None

    return Card(
        ankid=ankid,
        card_type="basic",
        deck=deck_name,
        tags=tag_list,
        suspend=suspend,
        front=front_text,
        back=extra_text,
    )


def parse_anki_markdown(
    md_text: str,
    source_file: str = "",
    quiet: bool = False,
    append_tags: Optional[List[str]] = None,
) -> List[Card]:
    extra_default_tags = parse_tags(",".join(append_tags)) if append_tags else ["bear-app"]
    cards: List[Card] = []

    # 1. 解析 ```anki 代码块
    block_pattern = re.compile(r"```anki[ \t]*\r?\n(?P<content>.*?)\r?\n```[ \t]*$", re.DOTALL | re.MULTILINE)
    blocks = list(block_pattern.finditer(md_text))
    if not quiet:
        print(f"\n[DEBUG {source_file}] 检测到 ```anki 块数量={len(blocks)}")

    for idx, m in enumerate(blocks):
        card = parse_anki_block(m.group("content"))
        if card is None:
            print(f"⚠️ [{source_file}] 区块 {idx}: ```anki 解析失败，跳过")
            continue
        card.tags = list(dict.fromkeys(card.tags + extra_default_tags))
        cards.append(card)
        if not quiet:
            print(f"✅ [{source_file}] anki块解析成功 ankid={card.ankid}, type={card.card_type}, suspend={card.suspend}")

    # 2. 解析简易短横线行
    lines = md_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.startswith("- "):
            i += 1
            continue

        attr_match = re.search(r"<!--\s*(ankid\s*:.+?)\s*-->", line)
        if not attr_match:
            i += 1
            continue

        prop_dict: Dict[str, object] = {}
        for part in attr_match.group(1).split(";"):
            part = part.strip()
            if not part:
                continue
            if ":" in part:
                k, v = part.split(":", 1)
                prop_dict[k.strip()] = v.strip()
            else:
                prop_dict[part] = True

        ankid_val = str(prop_dict.get("ankid", "")).strip()
        if not ankid_val:
            print(f"⚠️ [{source_file}] 简易行缺少 ankid：{line}")
            i += 1
            continue

        need_suspend = "suspend" in prop_dict
        tag_list = parse_tags(str(prop_dict["tags"])) if "tags" in prop_dict else []

        deck_match = DECK_TAG_PATTERN.search(line)
        if not deck_match:
            print(f"⚠️ [{source_file}] 简易行缺少卡组标记：{line}")
            i += 1
            continue

        deck_name = normalize_deck(deck_match.group(0))

        main_text = re.sub(r"<!--\s*ankid\s*:.+?\s*-->", "", line)
        main_text = DECK_TAG_PATTERN.sub("", main_text)
        main_text = main_text.removeprefix("- ").strip()

        extra_lines: List[str] = []
        j = i + 1
        while j < len(lines):
            subline = lines[j]
            if re.match(r"^\s+- ", subline):
                extra_lines.append(re.sub(r"^\s*-\s*", "", subline).strip())
                j += 1
            else:
                break
        extra = "\n".join(extra_lines)

        merged_tags = list(dict.fromkeys(tag_list + extra_default_tags))

        if "::" in main_text:
            front, back = main_text.split("::", 1)
            front, back = front.strip(), back.strip()
            if not front or not back:
                print(f"⚠️ [{source_file}] 简易 Basic 正反面为空：{line}")
                i = j
                continue
            cards.append(
                Card(
                    ankid=ankid_val,
                    card_type="basic",
                    deck=deck_name,
                    tags=merged_tags,
                    suspend=need_suspend,
                    front=front,
                    back=back,
                )
            )
            if not quiet:
                print(f"✅ [{source_file}] 简易Basic解析成功 ankid={ankid_val}, suspend={need_suspend}")
        else:
            cloze_text = to_cloze_text(main_text)
            if cloze_text is None:
                print(f"⚠️ [{source_file}] 简易 Cloze 无 {{{{}}}} 挖空标记：{line}")
                i = j
                continue
            cards.append(
                Card(
                    ankid=ankid_val,
                    card_type="cloze",
                    deck=deck_name,
                    tags=merged_tags,
                    suspend=need_suspend,
                    front=cloze_text,
                    back=extra,
                )
            )
            if not quiet:
                print(f"✅ [{source_file}] 简易Cloze解析成功 ankid={ankid_val}, suspend={need_suspend}")

        i = j

    return cards


def scan_md_files(folder: Path, quiet: bool = False, append_tags: Optional[List[str]] = None) -> List[Card]:
    all_cards: List[Card] = []
    for f in sorted(folder.glob("*.md")):
        try:
            text = f.read_text(encoding="utf-8")
            cards = parse_anki_markdown(text, source_file=f.name, quiet=quiet, append_tags=append_tags)
            all_cards.extend(cards)
        except Exception as e:
            print(f"❌ 读取文件失败 {f}: {e}")
    return all_cards


def export_tsv(cards: List[Card], out_path: Path):
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        for c in cards:
            tag_str = ",".join(c.tags)
            # 替换换行与制表符，避免破坏 TSV 行列结构
            front_clean = c.front.replace("\t", " ").replace("\r\n", "<br>").replace("\n", "<br>")
            back_clean = c.back.replace("\t", " ").replace("\r\n", "<br>").replace("\n", "<br>")
            f.write(f"{c.ankid}\t{front_clean}\t{back_clean}\t{c.deck}\t{tag_str}\t{c.suspend}\n")


def fetch_existing_ankid_notes() -> Dict[str, dict]:
    nids = anki_invoke("findNotes", {"query": "ankid:*"})
    if not nids:
        return {}
    infos = anki_invoke("notesInfo", {"notes": nids})
    mapping: Dict[str, dict] = {}
    for info in infos:
        aid = info.get("fields", {}).get("ankid", {}).get("value", "").strip()
        if aid:
            mapping[aid] = info
    return mapping


def push_to_anki(cards: List[Card], toanki: bool = False, allow_delete: bool = False, quiet: bool = False):
    if not toanki:
        print("\n===== 仅预览模式，不写入 Anki =====")
        for c in cards:
            print(c)
        return

    print("===== 🚀 正式推送模式 =====")
    existing_map = fetch_existing_ankid_notes()
    local_ankid_set = {c.ankid for c in cards}
    deck_list: List[str] = anki_invoke("deckNames")
    empty_deck_candidates: Set[str] = set()
    to_suspend_cids: List[int] = []
    to_unsuspend_cids: List[int] = []

    for card in cards:
        try:
            if not quiet:
                print(f"🔄 ankid={card.ankid}, deck={card.deck}, suspend={card.suspend}, tags={card.tags}")

            if card.deck not in deck_list:
                if not quiet:
                    print(f"📂 卡组不存在，自动创建: {card.deck}")
                anki_invoke("createDeck", {"deck": card.deck})
                deck_list.append(card.deck)

            if card.card_type == "basic":
                fields = {"front": card.front, "back": card.back, "ankid": card.ankid}
                model_name = "Basic-md2anki"
            else:
                fields = {"text": card.front, "extra": card.back, "ankid": card.ankid}
                model_name = "Cloze-md2anki"

            existing = existing_map.get(card.ankid)
            if existing is not None:
                note_id = existing["noteId"]
                anki_invoke("updateNoteFields", {"note": {"id": note_id, "fields": fields}})
                anki_invoke("updateNoteTags", {"note": note_id, "tags": card.tags})
                card_ids: List[int] = existing.get("cards") or anki_invoke("findCards", {"query": f"nid:{note_id}"})
                if not quiet:
                    print(f"♻️ 更新笔记 note_id={note_id}, card_ids={card_ids}")

                if card_ids:
                    deck_map = anki_invoke("getDecks", {"cards": card_ids})
                    old_decks = [d for d in deck_map.keys() if d != card.deck]
                    if old_decks:
                        anki_invoke("changeDeck", {"cards": card_ids, "deck": card.deck})
                        print(f"📦 移动卡片 {card_ids} 到卡组 {card.deck}")
                        for old_dk in old_decks:
                            if is_deck_empty(old_dk):
                                empty_deck_candidates.add(old_dk)
            else:
                note_id = anki_invoke(
                    "addNote",
                    {
                        "note": {
                            "deckName": card.deck,
                            "modelName": model_name,
                            "fields": fields,
                            "tags": card.tags,
                        }
                    },
                )
                card_ids = anki_invoke("findCards", {"query": f"nid:{note_id}"})
                print(f"➕ 新增笔记 ankid={card.ankid}, note_id={note_id}, card_ids={card_ids}")

            if card_ids:
                if card.suspend:
                    to_suspend_cids.extend(card_ids)
                else:
                    to_unsuspend_cids.extend(card_ids)

        except Exception as e:
            print(f"❌ 处理卡片异常 [ankid={card.ankid}]: {e}")

    if to_suspend_cids:
        anki_invoke("suspend", {"cards": list(dict.fromkeys(to_suspend_cids))})
        if not quiet:
            print(f"⏸️ 批量暂停卡片数量={len(set(to_suspend_cids))}")
    if to_unsuspend_cids:
        anki_invoke("unsuspend", {"cards": list(dict.fromkeys(to_unsuspend_cids))})
        if not quiet:
            print(f"▶️ 批量恢复卡片数量={len(set(to_unsuspend_cids))}")

    if allow_delete:
        try:
            to_del_nids: List[int] = []
            cards_of_deleted: List[int] = []
            for aid, info in existing_map.items():
                if aid not in local_ankid_set:
                    to_del_nids.append(info["noteId"])
                    cards_of_deleted.extend(info.get("cards", []))

            deleted_note_decks: Set[str] = set()
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


def main():
    parser = argparse.ArgumentParser(description="Markdown同步Anki脚本，```anki + 简易短横线格式")
    parser.add_argument("dir", nargs="?", default="./notes", help="笔记目录")
    parser.add_argument("--toanki", action="store_true", help="写入Anki；不加则预览")
    parser.add_argument("--allow-delete", action="store_true", help="删除md不存在笔记，清理空卡组")
    parser.add_argument("--quiet", action="store_true", help="安静模式")
    parser.add_argument("--append-tags", default="bear-app", help="追加标签，逗号分隔")
    parser.add_argument("--tsv", default="anki_export.tsv", help="导出 TSV 路径")
    args = parser.parse_args()

    note_folder = Path(args.dir)
    if not note_folder.exists():
        print(f"❌ 目录不存在：{note_folder.resolve()}")
        sys.exit(1)

    append_tags = parse_tags(args.append_tags)
    result_cards = scan_md_files(note_folder, quiet=args.quiet, append_tags=append_tags)

    seen: Set[str] = set()
    dup_set: Set[str] = set()
    for c in result_cards:
        if c.ankid in seen:
            dup_set.add(c.ankid)
        seen.add(c.ankid)

    if dup_set:
        print(f"\n❌ 错误：检测到重复ankid！{sorted(dup_set)}")
        print("❌ 终止运行，不会推送、删除、导出")
        sys.exit(1)

    push_to_anki(result_cards, toanki=args.toanki, allow_delete=args.allow_delete, quiet=args.quiet)
    export_tsv(result_cards, Path(args.tsv))


if __name__ == "__main__":
    main()
