import re
import csv
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
DEFAULT_DATA_DIR = "data"
DEFAULT_DELETE_BACKUP_SUBDIR = "delete_backup"
DEFAULT_TSV_NAME = "anki_export.tsv"
DEFAULT_SUSPEND_CSV_NAME = "suspend_audit.csv"
DEFAULT_META_WARN_CSV_NAME = "meta_dash_warnings.csv"
CLOZE_PATTERN = re.compile(r"\{\{\s*(.+?)\s*\}\}")
COMMENT_BLOCK_PATTERN = re.compile(r"%%.*?%%", re.DOTALL)
CARD_SEP_PATTERN = re.compile(r"(?m)^---\s*$")
DECK_HEAD_PATTERN = re.compile(
    r"^[ \t]*(?:\[\^[^\]]+\]:\s*|[-*]\s+|\d+\.\s*)?#toanki/(.*?)(?:\s+@(suspended|active))?\s*$",
    re.IGNORECASE,
)
INLINE_DECK_PATTERN = re.compile(r"[ \t]*#toanki/[^\s<！!]*")
LIST_HEAD_PATTERN = re.compile(r"^[ \t]*(?:[-*]\s+|\d+\.\s*)")
LINE_PREFIX_PATTERN = re.compile(r"^\s*(?:\[\^[^\]]+\]:\s*|[-*]\s+|\d+\.\s*)")
INLINE_META_PATTERN = re.compile(r"<[!！]-{1,2}—?\s*(.*?)\s*-{1,2}—?>")
INLINE_EXTRA_LINE_PATTERN = re.compile(r"^[ \t]*- ")
MD_IMAGE_PATTERN = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)\s]+)(?:\s+\"[^\"]*\")?\)")
INLINE_BANG_MEDIA_PATTERN = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)\s]+)(?:\s+\"[^\"]*\")?\)(?:\s*<[!！]-{1,2}—?.*?-{1,2}—?>)?"
)
INLINE_PLAIN_MEDIA_PATTERN = re.compile(
    r"\[(?P<alt>[^\]]*)\]\((?P<src>[^)\s]+)(?:\s+\"[^\"]*\")?\)(?:\s*<[!！]-{1,2}—?.*?-{1,2}—?>)?"
)
SINGLE_MEDIA_LINE_PATTERN = re.compile(
    r"^\s*!\[(?P<alt>[^\]]*)\]\((?P<src>[^)\s]+)(?:\s+\"[^\"]*\")?\)\s*(?:<[!！]-{1,2}—?.*?-{1,2}—?>\s*)?$"
)
SINGLE_PLAIN_MEDIA_LINE_PATTERN = re.compile(
    r"^\s*\[(?P<alt>[^\]]*)\]\((?P<src>[^)\s]+)(?:\s+\"[^\"]*\")?\)\s*(?:<[!！]-{1,2}—?.*?-{1,2}—?>\s*)?$"
)
INLINE_TRAILING_MEDIA_PATTERN = re.compile(
    r"(?P<bang>!)?\[(?P<alt>[^\]]*)\]\((?P<src>[^)\s]+)(?:\s+\"[^\"]*\")?\)(?:\s*<[!！]-{1,2}—?.*?-{1,2}—?>)?\s*$"
)
SINGLE_IMAGE_LINE_PATTERN = SINGLE_MEDIA_LINE_PATTERN
AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac", ".opus", ".wma"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv", ".ogv", ".3gp"}
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
CUSTOM_KV_LINE_PATTERN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)[ \t]*:[ \t]*(.*)$")


@dataclass
class Card:
    uuid: str
    card_type: str
    deck: str
    tags: List[str] = field(default_factory=list)
    suspend_action: Optional[str] = None
    front: str = ""
    back: str = ""
    source_file: str = ""
    note_type: Optional[str] = None
    custom_fields: Dict[str, str] = field(default_factory=dict)

    @property
    def suspend(self) -> bool:
        return self.suspend_action == "suspended"

    @property
    def model_name(self) -> str:
        if self.note_type:
            return self.note_type
        if self.card_type == "basic":
            return "Basic-md2anki"
        return "Cloze-md2anki"


@dataclass
class ParseContext:
    meta_dash_warnings: List[Dict[str, str]] = field(default_factory=list)


def resolve_data_file_path(raw_path: str, data_dir: Path, default_name: str) -> Path:
    cleaned = raw_path.strip() if raw_path and raw_path.strip() else default_name
    p = Path(cleaned)
    if p.is_absolute() or len(p.parts) > 1:
        return p
    return data_dir / p.name


def strip_comments(text: str) -> str:
    return COMMENT_BLOCK_PATTERN.sub("", text)


def parse_inline_card_meta(raw_meta: str) -> Dict[str, object]:
    prop_dict: Dict[str, object] = {}
    for part in re.split(r"[;；]+", raw_meta):
        token = part.strip()
        if not token:
            continue
        kv_match = re.match(r"^([A-Za-z_]+)\s*[:：]\s*(.+?)\s*$", token)
        if kv_match:
            key = kv_match.group(1).strip().lower()
            val = kv_match.group(2).strip()
            prop_dict[key] = val
        else:
            flag = token.lstrip("@").strip().lower()
            prop_dict[flag] = True
    return prop_dict


def classify_nonstandard_comment(raw_comment: str) -> Optional[Tuple[str, str]]:
    has_bang = "！" in raw_comment
    has_em_dash = "—" in raw_comment
    if has_bang and has_em_dash:
        return ("fullwidth_bang_and_em_dash", "全角感叹号 `！` 与全角破折号 `—`")
    if has_bang:
        return ("fullwidth_bang", "全角感叹号 `！`")
    if has_em_dash:
        return ("fullwidth_em_dash", "全角破折号 `—`")
    return None


def anki_invoke(action: str, params: Optional[dict] = None):
    payload = {"action": action, "version": 6}
    if params is not None:
        payload["params"] = params
    try:
        resp = requests.post(ANKI_CONNECT_URL, json=payload, timeout=15)
        resp.raise_for_status()
        ret = resp.json()
    except requests.exceptions.ConnectionError as e:
        raise RuntimeError(
            f"无法连接到 AnkiConnect ({ANKI_CONNECT_URL})：请确认 Anki 已启动且已安装并启用 AnkiConnect 插件。"
        ) from e
    except requests.exceptions.Timeout as e:
        raise RuntimeError(
            f"连接 AnkiConnect ({ANKI_CONNECT_URL}) 超时（action={action}）。"
        ) from e
    except Exception as e:
        raise RuntimeError(f"AnkiConnect [{action}] 请求失败: {e}") from e
    if ret.get("error"):
        raise RuntimeError(f"AnkiConnect [{action}] error: {ret['error']}")
    return ret.get("result")


def check_anki_connect() -> bool:
    try:
        ver = anki_invoke("version")
        print(f"🔌 已连接 AnkiConnect (API v{ver})")
        return True
    except RuntimeError as e:
        print(f"\n❌ {e}")
        print("💡 排查建议：")
        print("   1. 确认桌面端 Anki 正在运行（后台托盘中存在 Anki 进程）")
        print("   2. 确认已安装 AnkiConnect 插件（插件代码: 2055492159），且未被禁用")
        print(f"   3. 确认本地端口 {ANKI_CONNECT_URL} 未被防火墙或代理拦截")
        return False


def escape_anki(s: str) -> str:
    return s.replace('"', '\\"')


def parse_deck_line(line: str) -> Tuple[Optional[str], Optional[str]]:
    m = DECK_HEAD_PATTERN.match(line)
    if not m:
        return None, None
    path_raw = m.group(1).strip()
    flag_raw = m.group(2).lower() if m.group(2) else None
    parts = [p.strip() for p in path_raw.split("/") if p.strip()]
    if not parts:
        return None, None
    deck_name = "::".join(parts)
    return deck_name, flag_raw


def parse_tags(raw: Optional[str]) -> List[str]:
    if not raw or not raw.strip():
        return []
    parts = re.split(r"[,，\s]+", raw.strip())
    cleaned = [re.sub(r"\s+", "_", p.strip()) for p in parts if p.strip()]
    return list(dict.fromkeys(cleaned))


def to_cloze_text(text: str) -> Optional[str]:
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


def get_media_kind(raw_src: str) -> Optional[str]:
    clean_src = unquote(raw_src).split("?", 1)[0].split("#", 1)[0].strip()
    ext = Path(clean_src).suffix.lower()
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in VIDEO_EXTS:
        return "video"
    return None


def build_av_tag_from_path(media_path: Path, kind: str) -> str:
    raw_bytes = media_path.resolve().read_bytes()
    b64_str = base64.b64encode(raw_bytes).decode("ascii")
    ext = media_path.suffix.lower().lstrip(".") or ("mp4" if kind == "video" else "mp3")
    filename = escape_anki(media_path.name)
    prefix = "bear_vid" if kind == "video" else "bear_aud"
    return (
        f'<span data-anki-av-kind="{kind}" data-anki-av-prefix="{prefix}" '
        f'data-anki-av-filename="{filename}" data-anki-av-ext="{ext}">'
        f'data:{kind}/{ext};base64,{b64_str}</span>'
    )


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


def extract_trailing_av_in_text(
    text: str,
    b64_cache: Dict[str, str],
    toanki: bool,
    quiet: bool,
    uuid: str,
    local_save_dir: Optional[Path] = None,
) -> str:
    if not text or "data-anki-av-filename=" not in text:
        return text

    av_span_pattern = re.compile(
        r'<span\b[^>]*\bdata-anki-av-kind=["\']([^"\']*)["\'][^>]*'
        r'\bdata-anki-av-prefix=["\']([^"\']*)["\'][^>]*'
        r'\bdata-anki-av-filename=["\']([^"\']*)["\'][^>]*'
        r'\bdata-anki-av-ext=["\']([^"\']*)["\'][^>]*>'
        r"data:[^;]+;base64,([^<]+)</span>",
        re.IGNORECASE,
    )

    def repl_av(match: re.Match) -> str:
        kind = match.group(1).strip().lower()
        prefix = match.group(2).strip() or ("bear_vid" if kind == "video" else "bear_aud")
        orig_name = match.group(3).strip()
        ext_raw = match.group(4).strip()
        b64_raw = match.group(5)
        icon = "🎬" if kind == "video" else "🔊"
        label = "紧跟视频" if kind == "video" else "紧跟音频"
        try:
            raw_bytes, ext = decode_base64_image(b64_raw, ext_raw)
        except Exception as e:
            print(f"❌ [uuid={uuid}] {label} Base64 解码失败 ({orig_name}): {e}")
            return match.group(0)
        if not toanki:
            if not quiet:
                print(
                    f"{icon} [预览][uuid={uuid}] 检测到{label}: {orig_name} "
                    f"(格式={ext}, 大小={len(raw_bytes)} bytes)"
                )
            return f"[sound:{orig_name}]"
        try:
            stem = Path(orig_name).stem or kind or "media"
            safe_stem = re.sub(r"[^\w\-]", "_", stem)
            digest_full = hashlib.md5(raw_bytes).hexdigest()
            if digest_full in b64_cache:
                media_name = b64_cache[digest_full]
            else:
                media_name = f"{prefix}_{safe_stem}_{digest_full[:8]}.{ext}"
                if local_save_dir is not None:
                    local_save_dir.mkdir(parents=True, exist_ok=True)
                    (local_save_dir / media_name).write_bytes(raw_bytes)
                b64_data = base64.b64encode(raw_bytes).decode("ascii")
                anki_invoke("storeMediaFile", {"filename": media_name, "data": b64_data})
                b64_cache[digest_full] = media_name
            if not quiet:
                print(f"{icon} [uuid={uuid}] {label}已存为 Anki 媒体文件: {media_name}")
            return f"[sound:{media_name}]"
        except Exception as e:
            print(f"❌ [uuid={uuid}] {label}上传 Anki 失败 ({orig_name}): {e}")
            return match.group(0)

    return av_span_pattern.sub(repl_av, text)


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
    text = extract_trailing_av_in_text(
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
            print(f"⚠️ [uuid={uuid}] 媒体文件不存在，跳过上传: {raw_src} (解析路径: {candidate})")
            return match.group(0)
        if not toanki:
            if not quiet:
                print(f"🖼️ [预览][uuid={uuid}] 检测到本地媒体文件: {candidate}")
            return match.group(0)
        try:
            av_kind = get_media_kind(raw_src)
            if av_kind in ("audio", "video"):
                raw_bytes = candidate.resolve().read_bytes()
                digest_full = hashlib.md5(raw_bytes).hexdigest()
                if digest_full in b64_cache:
                    media_name = b64_cache[digest_full]
                else:
                    prefix = "bear_vid" if av_kind == "video" else "bear_aud"
                    default_ext = ".mp4" if av_kind == "video" else ".mp3"
                    safe_stem = re.sub(r"[^\w\-]", "_", candidate.stem) or av_kind
                    suffix = candidate.suffix.lower() or default_ext
                    media_name = f"{prefix}_{safe_stem}_{digest_full[:8]}{suffix}"
                    if local_save_dir is not None:
                        local_save_dir.mkdir(parents=True, exist_ok=True)
                        (local_save_dir / media_name).write_bytes(raw_bytes)
                    b64_data = base64.b64encode(raw_bytes).decode("ascii")
                    anki_invoke("storeMediaFile", {"filename": media_name, "data": b64_data})
                    b64_cache[digest_full] = media_name
                return f"[sound:{media_name}]"
            media_name = store_local_image_to_anki(candidate, media_cache)
            if local_save_dir is not None:
                local_save_dir.mkdir(parents=True, exist_ok=True)
                (local_save_dir / media_name).write_bytes(candidate.resolve().read_bytes())
            alt_attr = f' alt="{escape_anki(alt)}"' if alt else ""
            return f'<img src="{escape_anki(media_name)}"{alt_attr}>'
        except Exception as e:
            print(f"❌ [uuid={uuid}] 上传媒体文件失败 {candidate}: {e}")
            return match.group(0)

    return MD_IMAGE_PATTERN.sub(repl, text)


def _resolve_single_media_match(
    has_bang: bool,
    alt: str,
    raw_src_quoted: str,
    md_file_path: Optional[Path],
    uuid: str,
    quiet: bool = False,
    source_desc: str = "紧跟媒体",
) -> Optional[str]:
    raw_src = unquote(raw_src_quoted.strip())
    av_kind = get_media_kind(raw_src)
    base_dir = md_file_path.parent if md_file_path else Path(".")

    if av_kind in ("audio", "video"):
        icon = "🎬" if av_kind == "video" else "🔊"
        kind_cn = "视频" if av_kind == "video" else "音频"
        if raw_src.startswith("data:"):
            return raw_src
        if raw_src.startswith(("http://", "https://")):
            if not quiet:
                print(f"⚠️ [uuid={uuid}] {source_desc}{kind_cn}为网络链接，跳过: {raw_src}")
            return None
        candidate = Path(raw_src)
        if not candidate.is_absolute():
            candidate = base_dir / candidate
        if not candidate.exists() or not candidate.is_file():
            print(f"⚠️ [uuid={uuid}] {source_desc}{kind_cn}文件不存在，跳过: {raw_src} (解析路径: {candidate})")
            return None
        try:
            tag = build_av_tag_from_path(candidate, kind=av_kind)
            if not quiet:
                print(f"{icon} [uuid={uuid}] 提取{source_desc}{kind_cn}: {candidate.name}")
            return tag
        except Exception as e:
            print(f"❌ [uuid={uuid}] 读取{source_desc}{kind_cn}失败 {candidate}: {e}")
            return None

    if not has_bang:
        return None
    if raw_src.startswith("data:"):
        alt_attr = f' alt="{escape_anki(alt)}"' if alt else ""
        return f'<img src="{raw_src}"{alt_attr}>'
    if raw_src.startswith(("http://", "https://")):
        if not quiet:
            print(f"⚠️ [uuid={uuid}] {source_desc}图片为网络链接，跳过转为 base64: {raw_src}")
        return None
    candidate = Path(raw_src)
    if not candidate.is_absolute():
        candidate = base_dir / candidate
    if not candidate.exists() or not candidate.is_file():
        print(f"⚠️ [uuid={uuid}] {source_desc}图片文件不存在，跳过: {raw_src} (解析路径: {candidate})")
        return None
    try:
        tag = build_base64_img_tag(candidate, alt=alt)
        if not quiet:
            print(f"🖼️ [uuid={uuid}] 提取{source_desc}图片并转为 base64: {candidate.name}")
        return tag
    except Exception as e:
        print(f"❌ [uuid={uuid}] 读取{source_desc}图片失败 {candidate}: {e}")
        return None


def extract_inline_trailing_media(
    line_after_meta: str,
    md_file_path: Optional[Path],
    uuid: str,
    quiet: bool = False,
) -> Tuple[str, List[str]]:
    trailing_tags: List[str] = []
    remaining = line_after_meta
    while True:
        m = INLINE_TRAILING_MEDIA_PATTERN.search(remaining)
        if not m:
            break
        has_bang = bool(m.group("bang"))
        alt = m.group("alt").strip()
        raw_src = m.group("src")
        clean_src = unquote(raw_src.strip()).split("?", 1)[0].split("#", 1)[0].strip()
        is_known_media = has_bang or (Path(clean_src).suffix.lower() in (AUDIO_EXTS | VIDEO_EXTS))
        if not is_known_media:
            break
        tag = _resolve_single_media_match(
            has_bang=has_bang,
            alt=alt,
            raw_src_quoted=raw_src,
            md_file_path=md_file_path,
            uuid=uuid,
            quiet=quiet,
            source_desc="单行行尾紧跟",
        )
        if tag is not None:
            trailing_tags.insert(0, tag)
        remaining = remaining[: m.start()].rstrip()
    return remaining, trailing_tags


def _strip_media_line_comments(text: str) -> str:
    return re.sub(r"<[!！]-{1,2}—?.*?-{1,2}—?>", "", text)


INLINE_ANY_MEDIA_PATTERN = re.compile(
    r"(?P<bang>!)\[(?P<alt>[^\]]*)\]\((?P<src>[^)\s]+)(?:\s+\"[^\"]*\")?\)(?:\s*<![!！]-{1,2}—?.*?-{1,2}—?>)?"
    r"|"
    r"\[(?P<p_alt>[^\]]*)\]\((?P<p_src>[^)\s]+)(?:\s+\"[^\"]*\")?\)(?:\s*<![!！]-{1,2}—?.*?-{1,2}—?>)?"
)


def extract_media_from_trailing_line(
    line: str,
    md_file_path: Optional[Path],
    uuid: str,
    quiet: bool = False,
    source_desc: str = "紧跟",
) -> List[str]:
    stripped = line.strip()
    if not stripped:
        return []

    matches = list(INLINE_ANY_MEDIA_PATTERN.finditer(stripped))
    if not matches:
        return []

    rem = _strip_media_line_comments(INLINE_ANY_MEDIA_PATTERN.sub("", stripped)).strip()
    if rem:
        return []

    parsed_items: List[Tuple[bool, str, str]] = []
    for m in matches:
        if m.group("bang") is not None:
            parsed_items.append((True, m.group("alt").strip(), m.group("src")))
        else:
            src_val = m.group("p_src")
            if get_media_kind(src_val) is None:
                return []
            parsed_items.append((False, m.group("p_alt").strip(), src_val))

    matched_tags: List[str] = []
    for has_bang, alt, raw_src in parsed_items:
        tag = _resolve_single_media_match(
            has_bang=has_bang,
            alt=alt,
            raw_src_quoted=raw_src,
            md_file_path=md_file_path,
            uuid=uuid,
            quiet=quiet,
            source_desc=source_desc,
        )
        if tag is not None:
            matched_tags.append(tag)
    return matched_tags

def extract_trailing_images_after_block(
    lines: List[str],
    start_line_idx: int,
    md_file_path: Optional[Path],
    uuid: str,
    quiet: bool = False,
) -> Tuple[List[str], int]:
    media_tags: List[str] = []
    idx = start_line_idx
    while idx < len(lines):
        line = lines[idx]
        if not line.strip():
            break
        line_tags = extract_media_from_trailing_line(
            line=line,
            md_file_path=md_file_path,
            uuid=uuid,
            quiet=quiet,
            source_desc="紧跟",
        )
        if not line_tags:
            break
        media_tags.extend(line_tags)
        idx += 1
    return media_tags, idx


def split_media_tags_by_type(media_tags: List[str]) -> Tuple[List[str], List[str]]:
    audio_tags: List[str] = []
    image_tags: List[str] = []
    for tag in media_tags:
        if 'data-anki-av-kind="audio"' in tag or tag.startswith("[sound:"):
            audio_tags.append(tag)
        elif tag.startswith("<img") or "data:image/" in tag:
            image_tags.append(tag)
    return audio_tags, image_tags


def merge_field_with_media(existing_val: str, media_items: List[str], sep: str = "\n") -> str:
    if not media_items:
        return existing_val
    joined = sep.join(media_items)
    if existing_val.strip():
        return f"{existing_val.strip()}{sep}{joined}"
    return joined


def append_to_back(existing_back: str, extra_items: List[str]) -> str:
    if not extra_items:
        return existing_back
    joined_media = "\n".join(extra_items)
    if existing_back.strip():
        return f"{existing_back}\n\n{joined_media}"
    return joined_media


def get_parent_deck_chain(deck_name: str) -> List[str]:
    parts = deck_name.split("::")
    return ["::".join(parts[:i]) for i in range(len(parts) - 1, 0, -1)]


def is_deck_empty(deck_name: str) -> bool:
    cids = anki_invoke("findCards", {"query": f'deck:"{escape_anki(deck_name)}"'})
    return len(cids) == 0


def parse_custom_notetype_block(
    lines: List[str],
    note_type: str,
    deck_anki: str,
    suspend_action: Optional[str],
    source_file: str = "",
    trailing_back_items: Optional[List[str]] = None,
) -> Tuple[Optional[Card], Optional[str]]:
    tags_raw: Optional[str] = None
    uuid: Optional[str] = None
    fields_map: Dict[str, str] = {}
    current_key: Optional[str] = None

    for raw_line in lines:
        stripped = raw_line.strip()
        m = CUSTOM_KV_LINE_PATTERN.match(stripped)
        if m:
            k = m.group(1).strip()
            v = m.group(2).strip()
            k_lower = k.lower()
            if k_lower == "notetype":
                current_key = None
                continue
            if k_lower == "uuid":
                uuid = v
                current_key = None
                continue
            if k_lower == "tags":
                tags_raw = v
                current_key = None
                continue
            fields_map[k] = v
            current_key = k
        else:
            if current_key is not None:
                prev = fields_map.get(current_key, "")
                fields_map[current_key] = f"{prev}\n{raw_line}".strip() if prev else raw_line.strip()

    if not uuid:
        return None, "缺少 uuid"

    for k in list(fields_map.keys()):
        cleaned_v = strip_comments(fields_map[k]).strip()
        fields_map[k] = cleaned_v.replace("\r\n", "<br>").replace("\n", "<br>")

    audio_items, image_items = split_media_tags_by_type(trailing_back_items or [])

    if note_type == "NCE1_Core":
        for req_f in (
            "Lesson",
            "Sentence_EN",
            "Sentence_CN",
            "Target_Word",
            "Target_Word_Hint",
            "Phonetic_Notes",
            "Audio",
            "Image",
        ):
            fields_map.setdefault(req_f, "")
        if not fields_map.get("Sentence_EN", "").strip() and not fields_map.get("Sentence_CN", "").strip():
            return None, "NCE1_Core 缺少 Sentence_EN 或 Sentence_CN 内容"
        if audio_items:
            fields_map["Audio"] = merge_field_with_media(fields_map.get("Audio", ""), audio_items, sep=" ")
        if image_items:
            fields_map["Image"] = merge_field_with_media(fields_map.get("Image", ""), image_items, sep="\n")
    else:
        key_lookup = {k.lower(): k for k in fields_map.keys()}
        if audio_items:
            aud_key = key_lookup.get("audio", "Audio")
            fields_map[aud_key] = merge_field_with_media(fields_map.get(aud_key, ""), audio_items, sep=" ")
        if image_items:
            img_key = key_lookup.get("image", "Image")
            fields_map[img_key] = merge_field_with_media(fields_map.get(img_key, ""), image_items, sep="\n")

    tag_list = parse_tags(tags_raw)
    preview_front = (
        fields_map.get("Sentence_EN")
        or fields_map.get("front")
        or fields_map.get("Front")
        or next((v for v in fields_map.values() if v.strip()), "")
    )
    preview_back = (
        fields_map.get("Sentence_CN")
        or fields_map.get("back")
        or fields_map.get("Back")
        or ""
    )

    return (
        Card(
            uuid=uuid,
            card_type="custom",
            deck=deck_anki,
            tags=tag_list,
            suspend_action=suspend_action,
            front=preview_front,
            back=preview_back,
            source_file=source_file,
            note_type=note_type,
            custom_fields=fields_map,
        ),
        None,
    )


def parse_plain_block(
    block_content: str,
    deck_anki: str,
    suspend_action: Optional[str],
    source_file: str = "",
    trailing_back_items: Optional[List[str]] = None,
) -> Tuple[Optional[Card], Optional[str]]:
    lines = block_content.splitlines()

    detected_note_type: Optional[str] = None
    for line in lines:
        s = line.strip()
        if not s:
            continue
        m_nt = re.match(r"^noteType\s*:\s*(.+?)\s*$", s, re.IGNORECASE)
        if m_nt:
            detected_note_type = m_nt.group(1).strip()
            break

    if detected_note_type:
        return parse_custom_notetype_block(
            lines=lines,
            note_type=detected_note_type,
            deck_anki=deck_anki,
            suspend_action=suspend_action,
            source_file=source_file,
            trailing_back_items=trailing_back_items,
        )

    in_header = True
    tags_raw: Optional[str] = None
    uuid: Optional[str] = None
    body_lines: List[str] = []
    for line in lines:
        stripped = line.strip()
        if in_header:
            if stripped.startswith("Tags:"):
                tags_raw = stripped.removeprefix("Tags:").strip()
                continue
            if stripped.startswith("uuid:"):
                uuid = stripped.removeprefix("uuid:").strip()
                continue
            in_header = False
        body_lines.append(line)
    if not uuid:
        return None, "缺少 uuid"
    deck_name = deck_anki
    tag_list = parse_tags(tags_raw)
    body_text = "\n".join(body_lines)
    body_text = strip_comments(body_text)

    sep_match = CARD_SEP_PATTERN.search(body_text)
    if sep_match:
        front_raw = body_text[: sep_match.start()]
        extra_raw = body_text[sep_match.end() :]
        has_sep = True
    else:
        front_raw = body_text
        extra_raw = ""
        has_sep = False

    front_text = front_raw.strip()
    extra_text = extra_raw.strip()
    if not front_text:
        return None, "正面内容为空"

    combined_back = append_to_back(extra_text, trailing_back_items or [])
    cloze_text = to_cloze_text(front_text)
    if cloze_text is not None:
        return (
            Card(
                uuid=uuid,
                card_type="cloze",
                deck=deck_name,
                tags=tag_list,
                suspend_action=suspend_action,
                front=cloze_text,
                back=combined_back,
                source_file=source_file,
            ),
            None,
        )

    if not has_sep:
        return None, "Basic 缺少独占一行的 --- 分隔符"

    if not combined_back.strip():
        return None, "Basic 缺少背面内容"

    return (
        Card(
            uuid=uuid,
            card_type="basic",
            deck=deck_name,
            tags=tag_list,
            suspend_action=suspend_action,
            front=front_text,
            back=combined_back,
            source_file=source_file,
        ),
        None,
    )


def parse_anki_markdown(
    md_text: str,
    source_file: str = "",
    md_file_path: Optional[Path] = None,
    quiet: bool = False,
    append_tags: Optional[List[str]] = None,
    parse_ctx: Optional[ParseContext] = None,
) -> List[Card]:
    extra_default_tags = parse_tags(",".join(append_tags)) if append_tags else ["bear-app"]
    cards: List[Card] = []
    consumed_line_indices: Set[int] = set()
    lines = md_text.splitlines()
    i = 0
    block_idx = 0

    while i < len(lines):
        line1 = lines[i] if i < len(lines) else ""
        deck_name, suspend_action = parse_deck_line(line1)

        if deck_name is not None and (i + 1) < len(lines) and re.match(r"^\s*```\s*$", lines[i + 1]):
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
                block_idx += 1
                i += 1
                continue
            raw_block = "\n".join(lines[start_idx + 1 : j])
            uuid_preview = "unknown"
            for bline in raw_block.splitlines():
                s = bline.strip()
                if s.startswith("uuid:"):
                    uuid_preview = s.removeprefix("uuid:").strip()
                    break
            trailing_media, after_media_idx = extract_trailing_images_after_block(
                lines=lines,
                start_line_idx=j + 1,
                md_file_path=md_file_path,
                uuid=uuid_preview,
                quiet=quiet,
            )
            card, err_reason = parse_plain_block(
                raw_block,
                deck_anki=deck_name,
                suspend_action=suspend_action,
                source_file=source_file,
                trailing_back_items=trailing_media,
            )
            if card is None:
                print(f"⚠️ [{source_file}] 区块 {block_idx} (uuid={uuid_preview}): 解析失败（{err_reason}），跳过")
            else:
                card.tags = list(dict.fromkeys(card.tags + extra_default_tags))
                cards.append(card)
                for k in range(j + 1, after_media_idx):
                    consumed_line_indices.add(k)
                if not quiet:
                    print(
                        f"✅ [{source_file}] 裸块解析成功 uuid={card.uuid}, model={card.model_name}, "
                        f"suspend_action={card.suspend_action or 'none'}, 紧跟媒体={len(trailing_media)}项"
                    )
            block_idx += 1
            i = after_media_idx
            continue

        if deck_name is not None and (i + 1) < len(lines):
            next_s = lines[i + 1].strip()
            if re.match(r"^noteType\s*:\s*\S+", next_s, re.IGNORECASE):
                j = i + 1
                block_lines: List[str] = []
                while j < len(lines):
                    cur_line = lines[j]
                    cur_s = cur_line.strip()
                    if not cur_s:
                        break
                    if re.match(r"^\s*```", cur_line):
                        break
                    next_deck, _ = parse_deck_line(cur_line)
                    if next_deck is not None:
                        break
                    block_lines.append(cur_line)
                    j += 1
                uuid_preview = "unknown"
                for bline in block_lines:
                    s = bline.strip()
                    if s.lower().startswith("uuid:"):
                        uuid_preview = s.split(":", 1)[1].strip()
                        break
                trailing_media, after_media_idx = extract_trailing_images_after_block(
                    lines=lines,
                    start_line_idx=j,
                    md_file_path=md_file_path,
                    uuid=uuid_preview,
                    quiet=quiet,
                )
                card, err_reason = parse_plain_block(
                    "\n".join(block_lines),
                    deck_anki=deck_name,
                    suspend_action=suspend_action,
                    source_file=source_file,
                    trailing_back_items=trailing_media,
                )
                if card is None:
                    print(f"⚠️ [{source_file}] 无围栏 noteType 区块 (uuid={uuid_preview}): 解析失败（{err_reason}），跳过")
                else:
                    card.tags = list(dict.fromkeys(card.tags + extra_default_tags))
                    cards.append(card)
                    for k in range(j, after_media_idx):
                        consumed_line_indices.add(k)
                    if not quiet:
                        print(
                            f"✅ [{source_file}] 无围栏 noteType 块解析成功 uuid={card.uuid}, "
                            f"model={card.model_name}, suspend_action={card.suspend_action or 'none'}, "
                            f"紧跟媒体={len(trailing_media)}项"
                        )
                block_idx += 1
                i = after_media_idx
                continue

        i += 1

    if not quiet:
        print(f"\n[DEBUG {source_file}] 裸```块处理完毕，共解析 {block_idx} 个区块")

    i = 0
    while i < len(lines):
        if i in consumed_line_indices:
            i += 1
            continue
        line = lines[i]
        if "#toanki/" not in line:
            i += 1
            continue
        attr_match = INLINE_META_PATTERN.search(line)
        if not attr_match:
            i += 1
            continue
        raw_comment_snippet = attr_match.group(0)
        nonstd_info = classify_nonstandard_comment(raw_comment_snippet)
        prop_dict = parse_inline_card_meta(attr_match.group(1))
        uuid_val = str(prop_dict.get("uuid", "")).strip()
        if nonstd_info is not None:
            warn_type, warn_desc = nonstd_info
            warn_msg = (
                f"⚠️ [{source_file}:{i + 1}] (uuid={uuid_val or 'unknown'}) "
                f"检测到非标准注释符号（{warn_desc}）：{raw_comment_snippet}，建议改为 <!-- ... -->"
            )
            print(warn_msg)
            if parse_ctx is not None:
                parse_ctx.meta_dash_warnings.append(
                    {
                        "uuid": uuid_val,
                        "warning_type": warn_type,
                        "raw_comment": raw_comment_snippet,
                        "source": source_file,
                        "line": str(i + 1),
                    }
                )
        if not uuid_val:
            print(f"⚠️ [{source_file}] 单行卡片解析失败（缺少 uuid）：{line}")
            i += 1
            continue

        has_suspended = bool(prop_dict.get("suspended", False))
        has_active = bool(prop_dict.get("active", False))
        if has_suspended and has_active:
            print(f"⚠️ [{source_file}] 单行卡片 (uuid={uuid_val}) 解析失败（不能同时指定 suspended 与 active）：{line}")
            i += 1
            continue
        if has_suspended:
            suspend_action: Optional[str] = "suspended"
        elif has_active:
            suspend_action = "active"
        else:
            suspend_action = None

        tag_list = parse_tags(str(prop_dict["tags"])) if "tags" in prop_dict else []
        deck_match = INLINE_DECK_PATTERN.search(line)
        if not deck_match:
            print(f"⚠️ [{source_file}] 单行卡片 (uuid={uuid_val}) 解析失败（缺少必须的 #toanki/ 卡组标记）：{line}")
            i += 1
            continue
        deck_str = deck_match.group(0).strip()
        deck_name, inline_deck_action = parse_deck_line(deck_str)
        if not deck_name:
            print(f"⚠️ [{source_file}] 单行卡片 (uuid={uuid_val}) 解析失败（卡组名不可为空）：{line}")
            i += 1
            continue
        if inline_deck_action is not None:
            if suspend_action is not None and suspend_action != inline_deck_action:
                print(f"⚠️ [{source_file}] 单行卡片 (uuid={uuid_val}) 解析失败（卡组标记与注释内的 suspended/active 冲突）：{line}")
                i += 1
                continue
            suspend_action = inline_deck_action

        has_list_head = bool(LIST_HEAD_PATTERN.match(line))

        after_meta_str = line[attr_match.end() :]
        before_meta_str = line[: attr_match.start()]
        inline_media_tags: List[str] = []
        if after_meta_str.strip():
            cleaned_after, inline_media_tags = extract_inline_trailing_media(
                line_after_meta=after_meta_str,
                md_file_path=md_file_path,
                uuid=uuid_val,
                quiet=quiet,
            )
            line_for_body = before_meta_str + cleaned_after
        else:
            line_for_body = before_meta_str

        main_text = INLINE_DECK_PATTERN.sub("", line_for_body)
        main_text = LINE_PREFIX_PATTERN.sub("", main_text).strip()
        main_text = strip_comments(main_text)

        extra_lines: List[str] = []
        multiline_media_tags: List[str] = []
        j = i + 1
        while j < len(lines):
            if j in consumed_line_indices:
                break
            subline = lines[j]
            if not subline.strip():
                break
            if "#toanki/" in subline and INLINE_META_PATTERN.search(subline):
                break
            if INLINE_EXTRA_LINE_PATTERN.match(subline):
                s = INLINE_EXTRA_LINE_PATTERN.sub("", subline, count=1).strip()
                s = strip_comments(s)
                extra_lines.append(s)
                j += 1
            elif not has_list_head:
                sub_tags = extract_media_from_trailing_line(
                    line=subline,
                    md_file_path=md_file_path,
                    uuid=uuid_val,
                    quiet=quiet,
                    source_desc="单行换行紧跟",
                )
                if sub_tags:
                    multiline_media_tags.extend(sub_tags)
                    j += 1
                else:
                    break
            else:
                break

        meta_extra_val = str(prop_dict.get("extra", "")).strip()
        merged_tags = list(dict.fromkeys(tag_list + extra_default_tags))

        # 判断单行卡片类型：
        # 1) 含 {{...}} 挖空标记，且左侧主体不含 '::' 问答分隔 -> Cloze（此时右侧若出现 '::' 视为方案二的 inline extra）
        # 2) 含 '::' 且未在左侧作为 Cloze 出现 -> Basic（Basic 不支持 extra 字段）
        is_single_cloze = False
        cloze_body_text = main_text
        inline_extra_from_sep = ""

        if "::" in main_text:
            left_part, right_part = main_text.split("::", 1)
            if CLOZE_PATTERN.search(left_part):
                is_single_cloze = True
                cloze_body_text = left_part.strip()
                inline_extra_from_sep = right_part.strip()
        elif CLOZE_PATTERN.search(main_text):
            is_single_cloze = True
            cloze_body_text = main_text.strip()

        if not is_single_cloze and "::" in main_text:
            if meta_extra_val or extra_lines:
                print(
                    f"⚠️ [{source_file}:{i + 1}] 单行 Basic (uuid={uuid_val}) 检测到 extra 内容（注释 extra 或子列表），"
                    f"Basic 卡片不支持 extra 字段，已忽略"
                )
            front, back = main_text.split("::", 1)
            front, back = front.strip(), back.strip()
            if not front:
                print(f"⚠️ [{source_file}] 单行 Basic (uuid={uuid_val}) 解析失败（正面为空）：{line}")
                i = j
                continue
            if not back:
                print(f"⚠️ [{source_file}] 单行 Basic (uuid={uuid_val}) 解析失败（背面为空）：{line}")
                i = j
                continue
            full_back = append_to_back(back, inline_media_tags + multiline_media_tags)
            cards.append(
                Card(
                    uuid=uuid_val,
                    card_type="basic",
                    deck=deck_name,
                    tags=merged_tags,
                    suspend_action=suspend_action,
                    front=front,
                    back=full_back,
                    source_file=source_file,
                )
            )
            if not quiet:
                print(
                    f"✅ [{source_file}] 单行Basic解析成功 uuid={uuid_val}, "
                    f"suspend_action={suspend_action or 'none'}, 紧跟媒体={len(inline_media_tags) + len(multiline_media_tags)}项"
                )
        else:
            if not cloze_body_text:
                print(f"⚠️ [{source_file}] 单行 Cloze (uuid={uuid_val}) 解析失败（挖空内容为空）：{line}")
                i = j
                continue
            cloze_text = to_cloze_text(cloze_body_text)
            if cloze_text is None:
                print(f"⚠️ [{source_file}] 单行 Cloze (uuid={uuid_val}) 解析失败（缺少 {{{{}}}} 挖空标记）：{line}")
                i = j
                continue

            cloze_extra_parts: List[str] = []
            if meta_extra_val:
                cloze_extra_parts.append(meta_extra_val)
            if inline_extra_from_sep:
                cloze_extra_parts.append(inline_extra_from_sep)
            if extra_lines:
                cloze_extra_parts.append("\n".join(extra_lines))
            extra_text = "\n".join(cloze_extra_parts)
            combined_back = append_to_back(extra_text, inline_media_tags + multiline_media_tags)

            cards.append(
                Card(
                    uuid=uuid_val,
                    card_type="cloze",
                    deck=deck_name,
                    tags=merged_tags,
                    suspend_action=suspend_action,
                    front=cloze_text,
                    back=combined_back,
                    source_file=source_file,
                )
            )
            if not quiet:
                print(
                    f"✅ [{source_file}] 单行Cloze解析成功 uuid={uuid_val}, "
                    f"suspend_action={suspend_action or 'none'}, 紧跟媒体={len(inline_media_tags) + len(multiline_media_tags)}项"
                )
        i = j
    return cards


def scan_md_files(
    folder: Path,
    quiet: bool = False,
    append_tags: Optional[List[str]] = None,
    parse_ctx: Optional[ParseContext] = None,
) -> List[Card]:
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
                parse_ctx=parse_ctx,
            )
            all_cards.extend(cards)
        except Exception as e:
            print(f"❌ 读取文件失败 {f}: {e}")
    return all_cards


def export_meta_dash_warnings_csv(rows: List[Dict[str, str]], out_path: Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["uuid", "warning_type", "raw_comment", "source", "line"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"⚠️ 已导出注释符号告警表至: {out_path}（共 {len(rows)} 条，已覆盖更新）")


def export_tsv(cards: List[Card], out_path: Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        for c in cards:
            tag_str = ",".join(c.tags)
            front_clean = c.front.replace("\t", " ").replace("\r\n", "<br>").replace("\n", "<br>")
            if c.note_type:
                extra_parts = [f"{k}={v}" for k, v in c.custom_fields.items()]
                back_raw = " | ".join(extra_parts)
            else:
                back_raw = c.back
            back_clean = back_raw.replace("\t", " ").replace("\r\n", "<br>").replace("\n", "<br>")
            md_suspend_label = c.suspend_action or ""
            f.write(
                f"{c.uuid}\t{front_clean}\t{back_clean}\t{c.deck}\t{tag_str}\t{md_suspend_label}\t{c.source_file}\n"
            )


def fetch_existing_uuid_notes() -> Dict[str, dict]:
    nids = anki_invoke("findNotes", {"query": "uuid:*"})
    if not nids:
        return {}
    infos = anki_invoke("notesInfo", {"notes": nids})
    mapping: Dict[str, dict] = {}
    all_cids: List[int] = []
    for info in infos:
        aid = info.get("fields", {}).get("uuid", {}).get("value", "").strip()
        if aid:
            mapping[aid] = info
            all_cids.extend(info.get("cards") or [])

    suspended_cid_set: Set[int] = set()
    if all_cids:
        unique_cids = list(dict.fromkeys(all_cids))
        flags = anki_invoke("areSuspended", {"cards": unique_cids})
        for cid, is_susp in zip(unique_cids, flags):
            if is_susp:
                suspended_cid_set.add(cid)

    for info in mapping.values():
        cids = info.get("cards") or []
        info["_is_suspended"] = any(cid in suspended_cid_set for cid in cids)
    return mapping


def format_markdown_suspend_label(suspend_action: Optional[str]) -> str:
    if suspend_action == "suspended":
        return "@suspended"
    if suspend_action == "active":
        return "@active"
    return "未指定"


def build_suspend_audit_rows(
    local_cards: List[Card],
    existing_map: Dict[str, dict],
) -> List[Dict[str, str]]:
    audit_rows: List[Dict[str, str]] = []
    for c in local_cards:
        existing = existing_map.get(c.uuid)
        if existing is None:
            continue
        anki_is_suspended = bool(existing.get("_is_suspended", False))
        if not c.suspend and anki_is_suspended:
            print(
                f"⏸️ [uuid={c.uuid}] 保持挂起（尊重 Anki 状态） | "
                f"Anki=suspended, Markdown={format_markdown_suspend_label(c.suspend_action)}, source={c.source_file}"
            )
            audit_rows.append(
                {
                    "uuid": c.uuid,
                    "anki状态": "suspended",
                    "markdown状态": format_markdown_suspend_label(c.suspend_action),
                    "source": c.source_file,
                }
            )
    return audit_rows


def export_suspend_audit_csv(rows: List[Dict[str, str]], out_path: Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["uuid", "anki状态", "markdown状态", "source"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"📊 已导出挂起状态核对表至: {out_path}（共 {len(rows)} 条，已覆盖更新）")


def diff_cards(local_cards: List[Card], existing_map: Dict[str, dict], quiet: bool):
    local_dict: Dict[str, Card] = {c.uuid: c for c in local_cards}
    local_uuids = set(local_dict.keys())
    remote_uuids = set(existing_map.keys())

    add_list: List[Card] = []
    update_list: List[Tuple[Card, dict]] = []
    delete_uuids = remote_uuids - local_uuids

    for c in local_cards:
        old_note = existing_map.get(c.uuid)
        if old_note is None:
            add_list.append(c)
        else:
            old_model = old_note.get("modelName", "")
            if old_model != c.model_name:
                if not quiet:
                    print(f"⚠️ DIFF[{c.uuid}] 模型变更：旧={old_model}，新={c.model_name} → 将跳过该卡片同步")
                continue
            update_list.append((c, old_note))

    print("\n========== 📊 DIFF 差异预览 ==========")
    print(f"🟢 新增卡片({len(add_list)}):")
    for item in add_list:
        print(f"  - uuid={item.uuid} model={item.model_name} deck={item.deck} src={item.source_file}")

    print(f"\n🟡 更新卡片({len(update_list)}):")
    for c, _ in update_list:
        print(f"  - uuid={c.uuid} model={c.model_name} deck={c.deck} src={c.source_file}")

    print(f"\n🔴 Markdown不存在，待删除卡片({len(delete_uuids)}):")
    for uid in sorted(delete_uuids)[:20]:
        info = existing_map[uid]
        fields = info.get("fields", {})
        preview = (
            fields.get("front", {}).get("value")
            or fields.get("text", {}).get("value")
            or fields.get("Sentence_EN", {}).get("value")
            or ""
        ).replace("\n", " ")[:50]
        print(f"  - uuid={uid} preview={preview}")
    if len(delete_uuids) > 20:
        print(f"  ...共{len(delete_uuids)}条")
    print("======================================")
    print("💡提示：diff仅预览；执行真实同步请去掉--diff参数，搭配--toanki/--allow-delete")


def confirm_deletion(
    del_items: List[Tuple[str, dict]],
    candidate_decks: Set[str],
    backup_dir: Path = Path(DEFAULT_DATA_DIR) / DEFAULT_DELETE_BACKUP_SUBDIR,
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
            or fields.get("Sentence_EN", {}).get("value")
            or ""
        ).replace("\n", " ")[:40]
        print(f"  - uuid={aid} (nid={info.get('noteId')}): {preview_val}")
    if count > 15:
        print(f"  ... 还有 {count - 15} 条")
    if candidate_decks:
        print(f"⚠️ 关联空卡组候选（若为空将被清理）: {sorted(candidate_decks)}")
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"anki_delete_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
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
    backup_path.write_text(json.dumps(backup_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"💾 已保存待删笔记备份至: {backup_path}")
    if yes:
        return True
    ans = input("请输入 DELETE 确认删除（其他输入取消）: ").strip()
    return ans == "DELETE"


def push_to_anki(
    cards: List[Card],
    existing_map: Dict[str, dict],
    note_folder: Path,
    backup_dir: Path = Path(DEFAULT_DATA_DIR) / DEFAULT_DELETE_BACKUP_SUBDIR,
    toanki: bool = False,
    allow_delete: bool = False,
    yes: bool = False,
    max_delete: int = 20,
    quiet: bool = False,
    local_save_dir: Optional[Path] = None,
):
    media_cache: Dict[Path, str] = {}
    b64_cache: Dict[str, str] = {}
    model_fields_cache: Dict[str, Set[str]] = {}

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
        if c.custom_fields:
            for fk, fv in list(c.custom_fields.items()):
                c.custom_fields[fk] = process_images_in_text(
                    fv,
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
    local_uuid_set = {c.uuid for c in cards}
    deck_list: List[str] = anki_invoke("deckNames")
    empty_deck_candidates: Set[str] = set()
    to_suspend_cids: List[int] = []
    to_active_cids: List[int] = []

    for card in cards:
        try:
            if not quiet:
                print(
                    f"🔄 uuid={card.uuid}, model={card.model_name}, deck={card.deck}, "
                    f"suspend_action={card.suspend_action or 'none'}, tags={card.tags}"
                )
            if card.deck not in deck_list:
                if not quiet:
                    print(f"📂 卡组不存在，自动创建: {card.deck}")
                anki_invoke("createDeck", {"deck": card.deck})
                deck_list.append(card.deck)

            existing = existing_map.get(card.uuid)
            if existing is not None:
                old_model = existing.get("modelName", "")
                if old_model != card.model_name:
                    print(f"⚠️ [uuid={card.uuid}] 跳过同步！笔记模型发生变化：旧模型={old_model}，新模型={card.model_name}")
                    print("💡提示：AnkiConnect不支持变更笔记模型，请手动删除Anki内旧笔记后再重新同步。")
                    continue

            if card.note_type:
                model_name = card.model_name
                if model_name not in model_fields_cache:
                    try:
                        fnames = anki_invoke("modelFieldNames", {"modelName": model_name}) or []
                        model_fields_cache[model_name] = set(fnames)
                    except Exception as e:
                        print(f"⚠️ 获取模板 [{model_name}] 字段列表失败: {e}")
                        model_fields_cache[model_name] = set()

                fields = dict(card.custom_fields)
                allowed_fields = model_fields_cache.get(model_name, set())
                if not allowed_fields or "uuid" in allowed_fields:
                    fields["uuid"] = card.uuid
                if allowed_fields and "source" in allowed_fields:
                    fields["source"] = card.source_file
            elif card.card_type == "basic":
                fields = {
                    "front": card.front,
                    "back": card.back,
                    "uuid": card.uuid,
                    "source": card.source_file,
                }
                model_name = card.model_name
            else:
                fields = {
                    "text": card.front,
                    "extra": card.back,
                    "uuid": card.uuid,
                    "source": card.source_file,
                }
                model_name = card.model_name

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
                if card.suspend_action == "suspended":
                    to_suspend_cids.extend(card_ids)
                elif card.suspend_action == "active":
                    to_active_cids.extend(card_ids)
        except Exception as e:
            print(f"❌ 处理卡片异常 [uuid={card.uuid}]: {e}")

    if to_suspend_cids:
        anki_invoke("suspend", {"cards": list(dict.fromkeys(to_suspend_cids))})
        if not quiet:
            print(f"⏸️ 批量暂停(@suspended)卡片数量={len(set(to_suspend_cids))}")
    if to_active_cids:
        anki_invoke("unsuspend", {"cards": list(dict.fromkeys(to_active_cids))})
        if not quiet:
            print(f"▶️ 批量显式激活(@active)卡片数量={len(set(to_active_cids))}")

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
                if not confirm_deletion(
                    del_items,
                    all_candidate_decks,
                    backup_dir=backup_dir,
                    yes=yes,
                    max_delete=max_delete,
                ):
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
    parser = argparse.ArgumentParser(
        description="Markdown同步Anki脚本，裸```块 + 单行格式 + noteType自定义模板，卡组#toanki/，支持@suspended与@active显式解挂"
    )
    parser.add_argument("dir", nargs="?", default="./notes", help="笔记目录")
    parser.add_argument("--toanki", action="store_true", help="写入Anki；不加则预览")
    parser.add_argument("--diff", action="store_true", help="差异预览模式：显示新增/更新/待删，不改动Anki")
    parser.add_argument("--allow-delete", action="store_true", help="删除md不存在笔记，清理空卡组")
    parser.add_argument("--yes", "-y", action="store_true", help="删除时跳过交互确认")
    parser.add_argument("--max-delete", type=int, default=20, help="单次允许删除的最大笔记数（0表示不限制）")
    parser.add_argument("--quiet", action="store_true", help="安静模式")
    parser.add_argument("--append-tags", default="bear-app", help="追加标签，逗号分隔")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR, help="数据存放目录（默认：data）")
    parser.add_argument("--tsv", default=DEFAULT_TSV_NAME, help="导出 TSV 文件名或路径（默认存入 data/anki_export.tsv）")
    parser.add_argument(
        "--suspend-csv",
        default=DEFAULT_SUSPEND_CSV_NAME,
        help="导出挂起状态核对 CSV 文件名或路径（默认存入 data/suspend_audit.csv）",
    )
    parser.add_argument(
        "--meta-warn-csv",
        default=DEFAULT_META_WARN_CSV_NAME,
        help="导出非标准注释符号（全角！/ 全角—）告警 CSV 文件名或路径（默认存入 data/meta_dash_warnings.csv）",
    )
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
    data_dir = Path(args.data_dir)
    backup_dir = data_dir / DEFAULT_DELETE_BACKUP_SUBDIR
    tsv_path = resolve_data_file_path(args.tsv, data_dir, DEFAULT_TSV_NAME)
    suspend_csv_path = resolve_data_file_path(args.suspend_csv, data_dir, DEFAULT_SUSPEND_CSV_NAME)
    meta_warn_csv_path = resolve_data_file_path(args.meta_warn_csv, data_dir, DEFAULT_META_WARN_CSV_NAME)
    local_save_dir = Path(args.media_out_dir) if args.media_out_dir.strip() else None
    append_tags = parse_tags(args.append_tags)
    parse_ctx = ParseContext()
    result_cards = scan_md_files(
        note_folder,
        quiet=args.quiet,
        append_tags=append_tags,
        parse_ctx=parse_ctx,
    )
    seen: Set[str] = set()
    dup_set: Set[str] = set()
    for c in result_cards:
        if c.uuid in seen:
            dup_set.add(c.uuid)
        seen.add(c.uuid)
    if dup_set:
        export_meta_dash_warnings_csv(parse_ctx.meta_dash_warnings, meta_warn_csv_path)
        print(f"\n❌ 错误：检测到重复uuid！{sorted(dup_set)}")
        print("❌ 终止运行，不会推送、删除、导出")
        sys.exit(1)

    existing_map: Dict[str, dict] = {}
    if args.diff or args.toanki:
        if not check_anki_connect():
            export_meta_dash_warnings_csv(parse_ctx.meta_dash_warnings, meta_warn_csv_path)
            sys.exit(1)
        try:
            existing_map = fetch_existing_uuid_notes()
        except RuntimeError as e:
            export_meta_dash_warnings_csv(parse_ctx.meta_dash_warnings, meta_warn_csv_path)
            print(f"\n❌ {e}")
            sys.exit(1)

    audit_rows = build_suspend_audit_rows(result_cards, existing_map)

    if args.diff:
        diff_cards(result_cards, existing_map, args.quiet)
        export_tsv(result_cards, tsv_path)
        export_suspend_audit_csv(audit_rows, suspend_csv_path)
        export_meta_dash_warnings_csv(parse_ctx.meta_dash_warnings, meta_warn_csv_path)
        sys.exit(0)

    push_to_anki(
        result_cards,
        existing_map=existing_map,
        note_folder=note_folder,
        backup_dir=backup_dir,
        toanki=args.toanki,
        allow_delete=args.allow_delete,
        yes=args.yes,
        max_delete=args.max_delete,
        quiet=args.quiet,
        local_save_dir=local_save_dir,
    )
    export_tsv(result_cards, tsv_path)
    export_suspend_audit_csv(audit_rows, suspend_csv_path)
    export_meta_dash_warnings_csv(parse_ctx.meta_dash_warnings, meta_warn_csv_path)


if __name__ == "__main__":
    main()
