import re
import sys
import json
import base64
import hashlib
import argparse
from datetime import datetime
from urllib.parse import unquote
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
import requests

ANKI_CONNECT_URL = "http://localhost:8765"
CLOZE_PATTERN = re.compile(r"\{\{\s*(.+?)\s*\}\}")
DECK_TAG_PATTERN = re.compile(r"#(?:ankid(?:::|/))?([\w\-/\\]+)")
MD_IMAGE_PATTERN = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)\s]+)(?:\s+\"[^\"]*\")?\)")


@dataclass
class Card:
    ankid: str
    card_type: str  # "basic" | "cloze"
    deck: str
    tags: List[str] = field(default_factory=list)
    suspend: bool = False
    front: str = ""
    back: str = ""
    source_file: str = ""


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


def store_local_image_to_anki(img_path: Path, media_cache: Dict[Path, str]) -> str:
    """将本地图片存入 Anki 媒体库，返回 Anki 媒体文件名"""
    resolved = img_path.resolve()
    if resolved in media_cache:
        return media_cache[resolved]

    raw_bytes = resolved.read_bytes()
    short_hash = hashlib.md5(raw_bytes).hexdigest()[:8]
    safe_stem = re.sub(r"[^\w\-]", "_", resolved.stem) or "img"
    suffix = resolved.suffix.lower() or ".png"
    anki_filename = f"bear_{safe_stem}_{short_hash}{suffix}"

    b64_data = base64.b64encode(raw_bytes).decode("ascii")
    anki_invoke("storeMediaFile", {"filename": anki_filename, "data": b64_data})
    media_cache[resolved] = anki_filename
    return anki_filename


def process_images_in_text(
    text: str,
    md_file_path: Optional[Path],
    media_cache: Dict[Path, str],
    toanki: bool,
    quiet: bool,
    ankid: str,
) -> str:
    """处理文本中的 Markdown 图片引用，上传本地图片并转换为 <img src="...">"""
    if not text or "![" not in text:
        return text

    base_dir = md_file_path.parent if md_file_path else Path(".")

    def repl(match: re.Match) -> str:
        alt = match.group("alt").strip()
        raw_src = unquote(match.group("src").strip())

        # 网络链接直接保留
        if raw_src.startswith(("http://", "https://", "data:")):
            return match.group(0)

        candidate = Path(raw_src)
        if not candidate.is_absolute():
            candidate = base_dir / candidate

        if not candidate.exists() or not candidate.is_file():
            print(f"⚠️ [ankid={ankid}] 图片文件不存在，跳过上传: {raw_src} (解析路径: {candidate})")
            return match.group(0)

        if not toanki:
            # 预览模式下不写 Anki 媒体库，仅提示将处理
            if not quiet:
                print(f"🖼️ [预览][ankid={ankid}] 检测到本地图片: {candidate}")
            return match.group(0)

        try:
            media_name = store_local_image_to_anki(candidate, media_cache)
            alt_attr = f' alt="{escape_anki(alt)}"' if alt else ""
            return f'<img src="{escape_anki(media_name)}"{alt_attr}>'
        except Exception as e:
            print(f"❌ [ankid={ankid}] 上传图片失败 {candidate}: {e}")
            return match.group(0)

    return MD_IMAGE_PATTERN.sub(repl, text)


def get_parent_deck_chain(deck_name: str) -> List[str]:
    parts = deck_name.split("::")
    return ["::".join(parts[:i]) for i in range(len(parts) - 1, 0, -1)]


def is_deck_empty(deck_name: str) -> bool:
    cids = anki_invoke("findCards", {"query": f'deck:"{escape_anki(deck_name)}"'})
    return len(cids) == 0


def parse_anki_block(block_content: str, source_file: str = "") -> Optional[Card]:
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
            source_file=source_file,
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
        source_file=source_file,
    )


def parse_anki_markdown(
    md_text: str,
    source_file: str = "",
    quiet: bool = False,
    append_tags: Optional[List[str]] = None,
) -> List[Card]:
    extra_default_tags = parse_tags(",".join(append_tags)) if append_tags else ["bear-app"]
    cards: List[Card] = []

    block_pattern = re.compile(
        r"```anki[ \t]*\r?\n(?P<content>.*?)\r?\n```[ \t]*$",
        re.DOTALL | re.MULTILINE,
    )
    blocks = list(block_pattern.finditer(md_text))
    if not quiet:
        print(f"\n[DEBUG {source_file}] 检测到 ```anki 块数量={len(blocks)}")

    for idx, m in enumerate(blocks):
        card = parse_anki_block(m.group("content"), source_file=source_file)
        if card is None:
            print(f"⚠️ [{source_file}] 区块 {idx}: ```anki 解析失败，跳过")
            continue
        card.tags = list(dict.fromkeys(card.tags + extra_default_tags))
        cards.append(card)
        if not quiet:
            print(f"✅ [{source_file}] anki块解析成功 ankid={card.ankid}, type={card.card_type}, suspend={card.suspend}")

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
                    source_file=source_file,
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
                    source_file=source_file,
                )
            )
            if not quiet:
                print(f"✅ [{source_file}] 简易Cloze解析成功 ankid={ankid_val}, suspend={need_suspend}")

        i = j

    return cards


def scan_md_files(folder: Path, quiet: bool = False, append_tags: Optional[List[str]] = None) -> List[Card]:
    all_cards: List[Card] = []
    # rglob 兼容 Bear 导出时可能出现的子目录结构
    for f in sorted(folder.rglob("*.md")):
        try:
            text = f.read_text(encoding="utf-8")
            rel_name = str(f.relative_to(folder))
            cards = parse_anki_markdown(text, source_file=rel_name, quiet=quiet, append_tags=append_tags)
            all_cards.extend(cards)
        except Exception as e:
            print(f"❌ 读取文件失败 {f}: {e}")
    return all_cards


def export_tsv(cards: List[Card], out_path: Path):
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        for c in cards:
            tag_str = ",".join(c.tags)
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


def confirm_deletion(
    del_items: List[Tuple[str, dict]],
    candidate_decks: Set[str],
    yes: bool = False,
    max_delete: int = 20,
) -> bool:
    """防误删确认：阈值检查 + 备份 + 人工确认"""
    count = len(del_items)
    if max_delete > 0 and count > max_delete:
        print(
            f"🛑 触发防误删保护：待删除笔记数 ({count}) 超过上限 (--max-delete={max_delete})。"
            f"请核对目录是否正确，或使用 --max-delete 调大阈值。"
        )
        return False

    print(f"\n⚠️ 即将删除 {count} 条 Anki 笔记：")
    for aid, info in del_items[:15]:
        fields = info.get("fields", {})
        preview_val = (
            fields.get("front", {}).get("value")
            or fields.get("text", {}).get("value")
            or ""
        ).replace("\n", " ")[:40]
        print(f"  - ankid={aid} (nid={info.get('noteId')}): {preview_val}")
    if count > 15:
        print(f"  ... 还有 {count - 15} 条")

    if candidate_decks:
        print(f"⚠️ 关联空卡组候选（若为空将被清理）: {sorted(candidate_decks)}")

    # 写入备份快照
    backup_name = f"anki_delete_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    backup_payload = [
        {
            "ankid": aid,
            "noteId": info.get("noteId"),
            "modelName": info.get("modelName"),
            "tags": info.get("tags"),
            "fields": {k: v.get("value", "") for k, v in info.get("fields", {}).items()},
        }
        for aid, info in del_items
    ]
    Path(backup_name).write_text(json.dumps(backup_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"💾 已保存待删笔记备份至: {backup_name}")

    if yes:
        return True

    ans = input("请输入 DELETE 确认删除（其他输入取消）: ").strip()
    return ans == "DELETE"


def push_to_anki(
    cards: List[Card],
    note_folder: Path,
    toanki: bool = False,
    allow_delete: bool = False,
    yes: bool = False,
    max_delete: int = 20,
    quiet: bool = False,
):
    media_cache: Dict[Path, str] = {}

    # 先处理每张卡片的图片路径
    for c in cards:
        md_path = (note_folder / c.source_file) if c.source_file else None
        c.front = process_images_in_text(c.front, md_path, media_cache, toanki=toanki, quiet=quiet, ankid=c.ankid)
        c.back = process_images_in_text(c.back, md_path, media_cache, toanki=toanki, quiet=quiet, ankid=c.ankid)

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
            del_items: List[Tuple[str, dict]] = [
                (aid, info) for aid, info in existing_map.items() if aid not in local_ankid_set
            ]
            cards_of_deleted: List[int] = []
            for _, info in del_items:
                cards_of_deleted.extend(info.get("cards", []))

            deleted_note_decks: Set[str] = set()
            if cards_of_deleted:
                deck_map = anki_invoke("getDecks", {"cards": cards_of_deleted})
                deleted_note_decks.update(deck_map.keys())

            all_candidate_decks = (deleted_note_decks | empty_deck_candidates) - {"Default"}

            if del_items:
                if not confirm_deletion(del_items, all_candidate_decks, yes=yes, max_delete=max_delete):
                    print("🚫 已取消笔记与空卡组删除。")
                    return
                to_del_nids = [info["noteId"] for _, info in del_items]
                anki_invoke("deleteNotes", {"notes": to_del_nids})
                print(f"🗑️ 删除 {len(to_del_nids)} 条笔记（Markdown 中已不存在）")
            else:
                print("✅ 没有需要删除的笔记")

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
    parser.add_argument("--yes", "-y", action="store_true", help="删除时跳过交互确认")
    parser.add_argument("--max-delete", type=int, default=20, help="单次允许删除的最大笔记数（0表示不限制）")
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

    push_to_anki(
        result_cards,
        note_folder=note_folder,
        toanki=args.toanki,
        allow_delete=args.allow_delete,
        yes=args.yes,
        max_delete=args.max_delete,
        quiet=args.quiet,
    )
    export_tsv(result_cards, Path(args.tsv))


if __name__ == "__main__":
    main()
