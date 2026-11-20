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
# 匹配 #toanki/xxx/yyy  [可选 @suspend]
DECK_HEAD_PATTERN = re.compile(r"^#toanki/(.*?)(\s+@suspend)?\s*$")
MD_IMAGE_PATTERN = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)\s]+)(?:\s+\"[^\"]*\")?\)")
SINGLE_IMAGE_LINE_PATTERN = re.compile(
    r"^\s*!\[(?P<alt>[^\]]*)\]\((?P<src>[^)\s]+)(?:\s+\"[^\"]*\")?\)\s*(?:<!--.*?-->\s*)?$"
)
MD_BASE64_IMG_PATTERN = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\(data:image/(?P<ext>[a-zA-Z0-9+.-]+);base64,(?P<b64>[^)]+)\)"
)
HTML_BASE64_IMG_PATTERN = re.compile(
    r'<img\b[^>]*\bsrc=["\']data:image/(?P<ext>[a-zA-Z0-9+.-]+);base64,(?P<b64>[^"\']+)["\'][^>]*>',
    re.IGNORECASE,
)
ALT_ATTR_PATTERN = re.compile(r'\balt=["\']([^"\']*)["\']', re.IGNORECASE)
MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".bmp": "image/bmp",
    ".ico": "image/x-icon",
}


@dataclass
class Card:
    uuid: str
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


def parse_deck_line(line: str) -> Tuple[Optional[str], bool]:
    """
    解析 #toanki/xxx/yyy [@suspend]
    返回 (deck_name:Optional[str], suspend_flag:bool)
    deck_name为None代表格式非法
    """
    m = DECK_HEAD_PATTERN.match(line.strip())
    if not m:
        return None, False
    path_raw = m.group(1).strip()
    has_suspend = bool(m.group(2))
    parts = [p.strip() for p in path_raw.split("/") if p.strip()]
    deck_name = "::".join(parts)
    return deck_name, has_suspend


def parse_tags(raw: Optional[str]) -> List[str]:
    if not raw or not raw.strip():
        return []
    parts = re.split(r"[,，\s]+", raw.strip())
    cleaned = [re.sub(r"\s+", "_", p.strip()) for p in parts if p.strip()]
    return list(dict.fromkeys(cleaned))


def to_cloze_text(text: str) -> Optional[str]:
    """所有挖空统一使用 c1，不再递增编号"""
    if not CLOZE_PATTERN.search(text):
        return None
    def repl(match: re.Match) -> str:
        content = match.group(1).strip()
        return f"{{{{c1::{content}}}}}"
    return CLOZE_PATTERN.sub(repl, text)


def guess_mime_type(path: Path) -> str:
    return MIME_TYPES.get(path.suffix.lower(), "application/octet-stream")


def normalize_img_ext(ext_raw: str) -> str:
    ext = ext_raw.lower().split("+")[0].strip()
    if ext == "jpeg":
        return "jpg"
    if ext == "svg+xml":
        return "svg"
    return ext or "png"


def decode_base64_image(b64_raw: str, ext_raw: str) -> Tuple[bytes, str]:
    clean_b64 = re.sub(r"\s+", "", b64_raw)
    missing_pad = (-len(clean_b64)) % 4
    if missing_pad:
        clean_b64 += "=" * missing_pad
    raw_bytes = base64.b64decode(clean_b64, validate=False)
    ext = normalize_img_ext(ext_raw)
    return raw_bytes, ext


def store_bytes_to_anki_media(
    raw_bytes: bytes,
    ext: str,
    b64_cache: Dict[str, str],
    prefix: str = "bear_b64",
    local_save_dir: Optional[Path] = None,
) -> str:
    digest_full = hashlib.md5(raw_bytes).hexdigest()
    if digest_full in b64_cache:
        return b64_cache[digest_full]
    short_hash = digest_full[:8]
    anki_filename = f"{prefix}_{short_hash}.{ext}"
    if local_save_dir is not None:
        local_save_dir.mkdir(parents=True, exist_ok=True)
        (local_save_dir / anki_filename).write_bytes(raw_bytes)
    b64_data = base64.b64encode(raw_bytes).decode("ascii")
    anki_invoke("storeMediaFile", {"filename": anki_filename, "data": b64_data})
    b64_cache[digest_full] = anki_filename
    return anki_filename


def build_base64_img_tag(img_path: Path, alt: str = "") -> str:
    raw_bytes = img_path.resolve().read_bytes()
    mime = guess_mime_type(img_path)
    b64_str = base64.b64encode(raw_bytes).decode("ascii")
    alt_attr = f' alt="{escape_anki(alt)}"' if alt else ""
    return f'<img src="data:{mime};base64,{b64_str}"{alt_attr}>'


def store_local_image_to_anki(img_path: Path, media_cache: Dict[Path, str]) -> str:
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


def extract_base64_in_text(
    text: str,
    b64_cache: Dict[str, str],
    toanki: bool,
    quiet: bool,
    uuid: str,
    local_save_dir: Optional[Path] = None,
) -> str:
    if not text or "data:image/" not in text:
        return text
    def _convert_b64(alt: str, ext_raw: str, b64_raw: str, original_snippet: str) -> str:
        try:
            raw_bytes, ext = decode_base64_image(b64_raw, ext_raw)
        except Exception as e:
            print(f"❌ [uuid={uuid}] Base64 图片解码失败: {e}")
            return original_snippet
        if not toanki:
            if not quiet:
                print(
                    f"🖼️ [预览][uuid={uuid}] 检测到内联 Base64 图片 "
                    f"(格式={ext}, 大小={len(raw_bytes)} bytes)"
                )
            return original_snippet
        try:
            media_name = store_bytes_to_anki_media(
                raw_bytes=raw_bytes,
                ext=ext,
                b64_cache=b64_cache,
                local_save_dir=local_save_dir,
            )
            alt_attr = f' alt="{escape_anki(alt)}"' if alt else ""
            if not quiet:
                print(f"🖼️ [uuid={uuid}] Base64 已存为 Anki 媒体文件: {media_name}")
            return f'<img src="{escape_anki(media_name)}"{alt_attr}>'
        except Exception as e:
            print(f"❌ [uuid={uuid}] Base64 图片上传 Anki 失败: {e}")
            return original_snippet
    def repl_md(match: re.Match) -> str:
        alt = match.group("alt").strip()
        ext_raw = match.group("ext")
        b64_raw = match.group("b64")
        return _convert_b64(alt, ext_raw, b64_raw, match.group(0))
    def repl_html(match: re.Match) -> str:
        full_tag = match.group(0)
        alt_m = ALT_ATTR_PATTERN.search(full_tag)
        alt = alt_m.group(1).strip() if alt_m else ""
        ext_raw = match.group("ext")
        b64_raw = match.group("b64")
        return _convert_b64(alt, ext_raw, b64_raw, full_tag)
    updated = MD_BASE64_IMG_PATTERN.sub(repl_md, text)
    updated = HTML_BASE64_IMG_PATTERN.sub(repl_html, updated)
    return updated


def process_images_in_text(
    text: str,
    md_file_path: Optional[Path],
    media_cache: Dict[Path, str],
    b64_cache: Dict[str, str],
    toanki: bool,
    quiet: bool,
    uuid: str,
    local_save_dir: Optional[Path] = None,
) -> str:
    if not text:
        return text
    text = extract_base64_in_text(
        text=text,
        b64_cache=b64_cache,
        toanki=toanki,
        quiet=quiet,
        uuid=uuid,
        local_save_dir=local_save_dir,
    )
    if "![" not in text:
        return text
    base_dir = md_file_path.parent if md_file_path else Path(".")
    def repl(match: re.Match) -> str:
        alt = match.group("alt").strip()
        raw_src = unquote(match.group("src").strip())
        if raw_src.startswith(("http://", "https://", "data:")):
            return match.group(0)
        candidate = Path(raw_src)
        if not candidate.is_absolute():
            candidate = base_dir / candidate
        if not candidate.exists() or not candidate.is_file():
            print(f"⚠️ [uuid={uuid}] 图片文件不存在，跳过上传: {raw_src} (解析路径: {candidate})")
            return match.group(0)
        if not toanki:
            if not quiet:
                print(f"🖼️ [预览][uuid={uuid}] 检测到本地图片: {candidate}")
            return match.group(0)
        try:
            media_name = store_local_image_to_anki(candidate, media_cache)
            if local_save_dir is not None:
                local_save_dir.mkdir(parents=True, exist_ok=True)
                (local_save_dir / media_name).write_bytes(candidate.resolve().read_bytes())
            alt_attr = f' alt="{escape_anki(alt)}"' if alt else ""
            return f'<img src="{escape_anki(media_name)}"{alt_attr}>'
        except Exception as e:
            print(f"❌ [uuid={uuid}] 上传图片失败 {candidate}: {e}")
            return match.group(0)
    return MD_IMAGE_PATTERN.sub(repl, text)


def extract_trailing_images_after_block(
    lines: List[str],
    start_line_idx: int,
    md_file_path: Optional[Path],
    uuid: str,
    quiet: bool = False,
) -> Tuple[List[str], int]:
    img_tags: List[str] = []
    base_dir = md_file_path.parent if md_file_path else Path(".")
    idx = start_line_idx
    while idx < len(lines):
        line = lines[idx]
        if not line.strip():
            break
        m = SINGLE_IMAGE_LINE_PATTERN.match(line)
        if not m:
            break
        alt = m.group("alt").strip()
        raw_src = unquote(m.group("src").strip())
        if raw_src.startswith("data:"):
            alt_attr = f' alt="{escape_anki(alt)}"' if alt else ""
            img_tags.append(f'<img src="{raw_src}"{alt_attr}>')
            idx += 1
            continue
        if raw_src.startswith(("http://", "https://")):
            if not quiet:
                print(f"⚠️ [uuid={uuid}] 紧跟图片为网络链接，跳过转为 base64: {raw_src}")
            idx += 1
            continue
        candidate = Path(raw_src)
        if not candidate.is_absolute():
            candidate = base_dir / candidate
        if not candidate.exists() or not candidate.is_file():
            print(f"⚠️ [uuid={uuid}] 紧跟图片文件不存在，跳过: {raw_src} (解析路径: {candidate})")
            idx += 1
            continue
        try:
            tag = build_base64_img_tag(candidate, alt=alt)
            img_tags.append(tag)
            if not quiet:
                print(f"🖼️ [uuid={uuid}] 提取紧跟图片并转为 base64: {candidate.name}")
        except Exception as e:
            print(f"❌ [uuid={uuid}] 读取紧跟图片失败 {candidate}: {e}")
        idx += 1
    return img_tags, idx


def append_to_back(existing_back: str, extra_items: List[str]) -> str:
    if not extra_items:
        return existing_back
    joined_imgs = "\n".join(extra_items)
    if existing_back.strip():
        return f"{existing_back}\n\n{joined_imgs}"
    return joined_imgs


def get_parent_deck_chain(deck_name: str) -> List[str]:
    parts = deck_name.split("::")
    return ["::".join(parts[:i]) for i in range(len(parts) - 1, 0, -1)]


def is_deck_empty(deck_name: str) -> bool:
    cids = anki_invoke("findCards", {"query": f'deck:"{escape_anki(deck_name)}"'})
    return len(cids) == 0


def parse_plain_block(
    block_content: str,
    deck_anki: str,
    suspend_flag: bool,
    source_file: str = "",
    trailing_back_items: Optional[List[str]] = None,
) -> Optional[Card]:
    """解析裸 ``` 块，块头部：Tags:/uuid: ；@suspend移到块外#toanki行"""
    lines = block_content.splitlines()
    in_header = True
    tags_raw: Optional[str] = None
    uuid: Optional[str] = None
    body_lines: List[str] = []
    for line in lines:
        stripped = line.strip()
        if in_header:
            low = stripped.lower()
            # 块内部不再识别 @suspend
            if stripped.startswith("Tags:"):
                tags_raw = stripped.removeprefix("Tags:").strip()
                continue
            if stripped.startswith("uuid:"):
                uuid = stripped.removeprefix("uuid:").strip()
                continue
            in_header = False
        body_lines.append(line)
    if not uuid:
        return None
    deck_name = deck_anki
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
    combined_back = append_to_back(extra_text, trailing_back_items or [])
    cloze_text = to_cloze_text(front_text)
    if cloze_text is not None:
        return Card(
            uuid=uuid,
            card_type="cloze",
            deck=deck_name,
            tags=tag_list,
            suspend=suspend_flag,
            front=cloze_text,
            back=combined_back,
            source_file=source_file,
        )
    if not combined_back.strip():
        return None
    return Card(
        uuid=uuid,
        card_type="basic",
        deck=deck_name,
        tags=tag_list,
        suspend=suspend_flag,
        front=front_text,
        back=combined_back,
        source_file=source_file,
    )


def parse_anki_markdown(
    md_text: str,
    source_file: str = "",
    md_file_path: Optional[Path] = None,
    quiet: bool = False,
    append_tags: Optional[List[str]] = None,
) -> List[Card]:
    extra_default_tags = parse_tags(",".join(append_tags)) if append_tags else ["bear-app"]
    cards: List[Card] = []
    consumed_line_indices: Set[int] = set()
    lines = md_text.splitlines()
    i = 0
    block_idx = 0
    while i < len(lines):
        line1 = lines[i].strip() if i < len(lines) else ""
        deck_name, suspend_flag = parse_deck_line(line1)
        if deck_name is not None and (i+1) < len(lines) and re.match(r"^\s*```\s*$", lines[i+1]):
            start_idx = i + 1
            j = start_idx + 1
            found_close = False
            while j < len(lines):
                if re.match(r"^\s*```\s*$", lines[j]):
                    found_close = True
                    break
                j += 1
            if not found_close:
                print(f"⚠️ [{source_file}] 区块 {block_idx}: 未找到闭合 ```，跳过")
                block_idx +=1
                i +=1
                continue
            raw_block = "\n".join(lines[start_idx +1 : j])
            uuid_preview = "unknown"
            for bline in raw_block.splitlines():
                s = bline.strip()
                if s.startswith("uuid:"):
                    uuid_preview = s.removeprefix("uuid:").strip()
                    break
            trailing_imgs, after_img_idx = extract_trailing_images_after_block(
                lines=lines,
                start_line_idx=j+1,
                md_file_path=md_file_path,
                uuid=uuid_preview,
                quiet=quiet,
            )
            card = parse_plain_block(
                raw_block,
                deck_anki=deck_name,
                suspend_flag=suspend_flag,
                source_file=source_file,
                trailing_back_items=trailing_imgs,
            )
            if card is None:
                print(f"⚠️ [{source_file}] 区块{block_idx}: 解析失败，跳过")
            else:
                card.tags = list(dict.fromkeys(card.tags + extra_default_tags))
                cards.append(card)
                for k in range(j+1, after_img_idx):
                    consumed_line_indices.add(k)
                if not quiet:
                    print(f"✅ [{source_file}] 裸块解析成功 uuid={card.uuid}, type={card.card_type}, suspend={card.suspend}, 紧跟图片={len(trailing_imgs)}张")
            block_idx +=1
            i = after_img_idx
        else:
            i +=1

    if not quiet:
        print(f"\n[DEBUG {source_file}] 裸```块处理完毕，共解析 {block_idx} 个区块")

    i = 0
    while i < len(lines):
        if i in consumed_line_indices:
            i +=1
            continue
        line = lines[i]
        if not line.startswith("- "):
            i +=1
            continue
        attr_match = re.search(r"<!--\s*(uuid\s*:.+?)\s*-->", line)
        if not attr_match:
            i +=1
            continue
        prop_dict: Dict[str, object] = {}
        for part in attr_match.group(1).split(";"):
            part = part.strip()
            if not part:
                continue
            if ":" in part:
                k, v = part.split(":",1)
                prop_dict[k.strip()] = v.strip()
            else:
                prop_dict[part.strip()] = True
        uuid_val = str(prop_dict.get("uuid","")).strip()
        if not uuid_val:
            print(f"⚠️ [{source_file}] 简易行缺少 uuid：{line}")
            i +=1
            continue
        need_suspend = "suspend" in prop_dict
        tag_list = parse_tags(str(prop_dict["tags"])) if "tags" in prop_dict else []
        deck_match = re.search(r"#toanki/[\w\-/]+", line)
        if not deck_match:
            print(f"⚠️ [{source_file}] 简易行缺少必须的 #toanki/ 卡组标记：{line}")
            i +=1
            continue
        deck_str = deck_match.group(0)
        deck_name, _ = parse_deck_line(deck_str)
        if deck_name is None:
            print(f"⚠️ [{source_file}] 简易行卡组格式非法，必须#toanki/开头：{line}")
            i +=1
            continue
        main_text = re.sub(r"<!--\s*uuid\s*:.+?\s*-->", "", line)
        main_text = re.sub(r"#toanki/[\w\-/]+", "", main_text)
        main_text = main_text.removeprefix("- ").strip()

        extra_lines: List[str] = []
        j = i+1
        while j < len(lines):
            if j in consumed_line_indices:
                break
            subline = lines[j]
            if re.match(r"^\s+- ", subline):
                extra_lines.append(re.sub(r"^\s*-\s*", "", subline).strip())
                j +=1
            else:
                break
        extra = "\n".join(extra_lines)
        merged_tags = list(dict.fromkeys(tag_list + extra_default_tags))
        if "::" in main_text:
            front, back = main_text.split("::",1)
            front, back = front.strip(), back.strip()
            if not front or not back:
                print(f"⚠️ [{source_file}] 简易 Basic 正反面为空：{line}")
                i = j
                continue
            cards.append(
                Card(
                    uuid=uuid_val,
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
                print(f"✅ [{source_file}] 简易Basic解析成功 uuid={uuid_val}, suspend={need_suspend}")
        else:
            cloze_text = to_cloze_text(main_text)
            if cloze_text is None:
                print(f"⚠️ [{source_file}] 简易 Cloze 无 {{}} 挖空标记：{line}")
                i = j
                continue
            cards.append(
                Card(
                    uuid=uuid_val,
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
                print(f"✅ [{source_file}] 简易Cloze解析成功 uuid={uuid_val}, suspend={need_suspend}")
        i = j
    return cards


def scan_md_files(folder: Path, quiet: bool = False, append_tags: Optional[List[str]] = None) -> List[Card]:
    all_cards: List[Card] = []
    for f in sorted(folder.rglob("*.md")):
        try:
            text = f.read_text(encoding="utf-8")
            rel_name = str(f.relative_to(folder))
            cards = parse_anki_markdown(
                text,
                source_file=rel_name,
                md_file_path=f,
                quiet=quiet,
                append_tags=append_tags,
            )
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
            f.write(f"{c.uuid}\t{front_clean}\t{back_clean}\t{c.deck}\t{tag_str}\t{c.suspend}\n")


def fetch_existing_uuid_notes() -> Dict[str, dict]:
    nids = anki_invoke("findNotes", {"query": "uuid:*"})
    if not nids:
        return {}
    infos = anki_invoke("notesInfo", {"notes": nids})
    mapping: Dict[str, dict] = {}
    for info in infos:
        aid = info.get("fields", {}).get("uuid", {}).get("value", "").strip()
        if aid:
            mapping[aid] = info
    return mapping


def confirm_deletion(
    del_items: List[Tuple[str, dict]],
    candidate_decks: Set[str],
    yes: bool = False,
    max_delete: int = 20,
) -> bool:
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
        print(f"  - uuid={aid} (nid={info.get('noteId')}): {preview_val}")
    if count > 15:
        print(f"  ... 还有 {count - 15} 条")
    if candidate_decks:
        print(f"⚠️ 关联空卡组候选（若为空将被清理）: {sorted(candidate_decks)}")
    backup_name = f"anki_delete_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    backup_payload = [
        {
            "uuid": aid,
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
    local_save_dir: Optional[Path] = None,
):
    media_cache: Dict[Path, str] = {}
    b64_cache: Dict[str, str] = {}
    for c in cards:
        md_path = (note_folder / c.source_file) if c.source_file else None
        c.front = process_images_in_text(
            c.front,
            md_path,
            media_cache,
            b64_cache,
            toanki=toanki,
            quiet=quiet,
            uuid=c.uuid,
            local_save_dir=local_save_dir,
        )
        c.back = process_images_in_text(
            c.back,
            md_path,
            media_cache,
            b64_cache,
            toanki=toanki,
            quiet=quiet,
            uuid=c.uuid,
            local_save_dir=local_save_dir,
        )
    if not toanki:
        print("\n===== 仅预览模式，不写入 Anki =====")
        for c in cards:
            print(c)
        return
    print("===== 🚀 正式推送模式 =====")
    existing_map = fetch_existing_uuid_notes()
    local_uuid_set = {c.uuid for c in cards}
    deck_list: List[str] = anki_invoke("deckNames")
    empty_deck_candidates: Set[str] = set()
    to_suspend_cids: List[int] = []
    to_unsuspend_cids: List[int] = []
    for card in cards:
        try:
            if not quiet:
                print(f"🔄 uuid={card.uuid}, deck={card.deck}, suspend={card.suspend}, tags={card.tags}")
            if card.deck not in deck_list:
                if not quiet:
                    print(f"📂 卡组不存在，自动创建: {card.deck}")
                anki_invoke("createDeck", {"deck": card.deck})
                deck_list.append(card.deck)
            if card.card_type == "basic":
                fields = {"front": card.front, "back": card.back, "uuid": card.uuid}
                model_name = "Basic-md2anki"
            else:
                fields = {"text": card.front, "extra": card.back, "uuid": card.uuid}
                model_name = "Cloze-md2anki"
            existing = existing_map.get(card.uuid)
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
                print(f"➕ 新增笔记 uuid={card.uuid}, note_id={note_id}, card_ids={card_ids}")
            if card_ids:
                if card.suspend:
                    to_suspend_cids.extend(card_ids)
                else:
                    to_unsuspend_cids.extend(card_ids)
        except Exception as e:
            print(f"❌ 处理卡片异常 [uuid={card.get('uuid')}]: {e}")
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
                (aid, info) for aid, info in existing_map.items() if aid not in local_uuid_set
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
    parser = argparse.ArgumentParser(description="Markdown同步Anki脚本，裸```块 + 简易短横线格式，卡组#toanki/，@suspend写在卡组行末尾")
    parser.add_argument("dir", nargs="?", default="./notes", help="笔记目录")
    parser.add_argument("--toanki", action="store_true", help="写入Anki；不加则预览")
    parser.add_argument("--allow-delete", action="store_true", help="删除md不存在笔记，清理空卡组")
    parser.add_argument("--yes", "-y", action="store_true", help="删除时跳过交互确认")
    parser.add_argument("--max-delete", type=int, default=20, help="单次允许删除的最大笔记数（0表示不限制）")
    parser.add_argument("--quiet", action="store_true", help="安静模式")
    parser.add_argument("--append-tags", default="bear-app", help="追加标签，逗号分隔")
    parser.add_argument("--tsv", default="anki_export.tsv", help="导出 TSV 路径")
    parser.add_argument(
        "--media-out-dir",
        default="",
        help="可选：将解码后的图片文件额外保存到指定本地目录（默认不额外落盘）",
    )
    args = parser.parse_args()
    note_folder = Path(args.dir)
    if not note_folder.exists():
        print(f"❌ 目录不存在：{note_folder.resolve()}")
        sys.exit(1)
    local_save_dir = Path(args.media_out_dir) if args.media_out_dir.strip() else None
    append_tags = parse_tags(args.append_tags)
    result_cards = scan_md_files(note_folder, quiet=args.quiet, append_tags=append_tags)
    seen: Set[str] = set()
    dup_set: Set[str] = set()
    for c in result_cards:
        if c.uuid in seen:
            dup_set.add(c.uuid)
        seen.add(c.uuid)
    if dup_set:
        print(f"\n❌ 错误：检测到重复uuid！{sorted(dup_set)}")
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
        local_save_dir=local_save_dir,
    )
    export_tsv(result_cards, Path(args.tsv))


if __name__ == "__main__":
    main()
