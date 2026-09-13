# ==========================================
# Version: 2.3.0
# Date: 2026-09-13
# Summary: 気分チップ絞り込みと早期CTA対応
# ==========================================
"""GitHub Pages 向け HTML 生成モジュール。"""

from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from modules.dmm_api import FanzaItem
from modules.moods import MOOD_OPTIONS, infer_moods

logger = logging.getLogger(__name__)

DOCS_DIR_NAME = "docs"
TEMPLATES_DIR_NAME = "templates"
INDEX_ENTRIES_FILE = ".index_entries.json"
SITE_NAME = "夜野リブレX - FANZAおすすめメディア"


@dataclass
class IndexEntry:
    """トップページ一覧用エントリ。"""

    content_id: str
    title: str
    article_filename: str
    created_at: str
    image_url: str = ""
    summary: str = ""
    moods: list[str] = field(default_factory=list)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def docs_dir() -> Path:
    path = _project_root() / DOCS_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def templates_dir() -> Path:
    return _project_root() / TEMPLATES_DIR_NAME


def article_filename_for(content_id: str) -> str:
    safe_id = re.sub(r"[^\w\-]", "_", content_id)
    return f"article_{safe_id}.html"


def build_cushion_page_url(base_url: str, content_id: str) -> str:
    """個別記事の公開 URL を組み立てる（トップではなく article_*.html）。"""
    base = base_url.rstrip("/")
    return f"{base}/{article_filename_for(content_id)}"


def _format_price_display(item: FanzaItem) -> str:
    if item.sale_price is None:
        return "価格は公式サイトでご確認ください"
    if item.list_price and item.list_price > item.sale_price:
        return f"定価 {item.list_price:,}円 → 今 {item.sale_price:,}円"
    return f"{item.sale_price:,}円"


def _load_template(name: str) -> str:
    path = templates_dir() / name
    if not path.exists():
        raise FileNotFoundError(f"テンプレートが見つかりません: {path}")
    return path.read_text(encoding="utf-8")


def _apply_template(template: str, mapping: dict[str, str]) -> str:
    rendered = template
    for key, value in mapping.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    return rendered


def _plain_summary_from_html(article_html_body: str, fallback: str) -> str:
    text = re.sub(r"<[^>]+>", " ", article_html_body or "")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        text = fallback
    return text[:120]


def _load_index_entries(docs_path: Path) -> list[IndexEntry]:
    meta_path = docs_path / INDEX_ENTRIES_FILE
    if not meta_path.exists():
        return []
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("index メタデータ読み込み失敗: %s", exc)
        return []
    entries: list[IndexEntry] = []
    for row in data.get("entries", []):
        if not isinstance(row, dict):
            continue
        cid = str(row.get("content_id", "")).strip()
        title = str(row.get("title", "")).strip()
        filename = str(row.get("article_filename", "")).strip()
        created = str(row.get("created_at", "")).strip()
        raw_moods = row.get("moods") or []
        moods = [str(m).strip() for m in raw_moods if str(m).strip()]
        if cid and filename:
            entries.append(
                IndexEntry(
                    content_id=cid,
                    title=title or cid,
                    article_filename=filename,
                    created_at=created,
                    image_url=str(row.get("image_url", "") or ""),
                    summary=str(row.get("summary", "") or ""),
                    moods=moods,
                )
            )
    return entries


def _save_index_entries(docs_path: Path, entries: list[IndexEntry]) -> None:
    meta_path = docs_path / INDEX_ENTRIES_FILE
    payload = {
        "entries": [
            {
                "content_id": e.content_id,
                "title": e.title,
                "article_filename": e.article_filename,
                "created_at": e.created_at,
                "image_url": e.image_url,
                "summary": e.summary,
                "moods": e.moods,
            }
            for e in entries
        ]
    }
    meta_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _render_article_page(
    item: FanzaItem,
    article_html_body: str,
    *,
    pages_base_url: str,
) -> str:
    """個別記事 HTML をテンプレートから生成する。"""
    page_title = html.escape(item.title)
    description_meta = html.escape(item.description.replace("\n", " ")[:160])
    image = html.escape(item.image_url) if item.image_url else ""
    affiliate = html.escape(item.affiliate_url, quote=True)
    canonical = html.escape(build_cushion_page_url(pages_base_url, item.content_id))
    price_line = html.escape(_format_price_display(item))

    discount_badge = ""
    if item.discount_percent is not None and item.discount_percent >= 30:
        discount_badge = (
            f'<span class="badge">約{int(item.discount_percent)}% OFF</span>'
        )

    og_image_tag = ""
    if image:
        og_image_tag = f'<meta property="og:image" content="{image}">'

    hero_block = ""
    if image:
        hero_block = (
            f'<div class="hero-media"><img src="{image}" alt="{page_title}" '
            f'loading="lazy"></div>'
        )

    template = _load_template("article.html")
    return _apply_template(
        template,
        {
            "PAGE_TITLE": page_title,
            "META_DESCRIPTION": description_meta,
            "CANONICAL_URL": canonical,
            "OG_IMAGE_TAG": og_image_tag,
            "HERO_BLOCK": hero_block,
            "DISCOUNT_BADGE": discount_badge,
            "PRICE_LINE": price_line,
            "ARTICLE_BODY": article_html_body,
            "AFFILIATE_URL": affiliate,
            "YEAR": str(datetime.now(timezone.utc).year),
        },
    )


def _render_card(entry: IndexEntry) -> str:
    title = html.escape(entry.title)
    href = html.escape(entry.article_filename)
    date_str = html.escape(entry.created_at[:10] if entry.created_at else "")
    summary = html.escape(entry.summary or "レビュー記事を見る")
    moods = [m for m in entry.moods if m in MOOD_OPTIONS] or ["スピード重視"]
    moods_attr = html.escape(",".join(moods))
    search_blob = html.escape(
        f"{entry.title} {entry.summary} {' '.join(moods)}".lower()
    )
    mood_pills = "".join(
        f'<span class="mood-pill">{html.escape(m)}</span>' for m in moods
    )
    thumb = ""
    if entry.image_url:
        img = html.escape(entry.image_url)
        thumb = f'<div class="thumb"><img src="{img}" alt="{title}" loading="lazy"></div>'
    else:
        thumb = '<div class="thumb"></div>'
    return (
        f'<a class="work-card" href="{href}" data-moods="{moods_attr}" '
        f'data-search="{search_blob}">'
        f"{thumb}"
        f'<div class="body">'
        f'<div class="date">{date_str}</div>'
        f"<h3>{title}</h3>"
        f'<div class="mood-row">{mood_pills}</div>'
        f'<p class="summary">{summary}</p>'
        f'<div class="more">レビューを読む →</div>'
        f"</div></a>"
    )


def _render_mood_filters() -> str:
    chips = [
        '<button type="button" class="mood-chip is-active" data-mood="all" aria-pressed="true">すべて</button>'
    ]
    for mood in MOOD_OPTIONS:
        chips.append(
            f'<button type="button" class="mood-chip" data-mood="{html.escape(mood)}" '
            f'aria-pressed="false">{html.escape(mood)}</button>'
        )
    return "\n".join(chips)


def _render_index_page(entries: list[IndexEntry], *, pages_base_url: str) -> str:
    """トップ index.html をテンプレートから生成する。"""
    sorted_entries = sorted(entries, key=lambda e: e.created_at, reverse=True)
    if sorted_entries:
        cards = "\n".join(_render_card(e) for e in sorted_entries)
    else:
        cards = '<p class="empty">まだ記事がありません。次回の更新をお待ちください。</p>'

    template = _load_template("index.html")
    return _apply_template(
        template,
        {
            "PAGE_TITLE": SITE_NAME,
            "META_DESCRIPTION": "セール中の注目作と評判のタイトルをレビュー形式で紹介",
            "CANONICAL_URL": html.escape(pages_base_url.rstrip("/") + "/"),
            "MOOD_FILTERS": _render_mood_filters(),
            "CARD_GRID": cards,
            "YEAR": str(datetime.now(timezone.utc).year),
        },
    )


def write_article_and_update_index(
    item: FanzaItem,
    article_html_body: str,
    *,
    github_pages_base_url: str,
    summary: str | None = None,
    moods: list[str] | None = None,
) -> Path:
    """
    個別記事 HTML を書き出し、index.html とメタデータを更新する。

    戻り値: 生成した記事ファイルの Path
    """
    docs_path = docs_dir()
    filename = article_filename_for(item.content_id)
    article_path = docs_path / filename
    now_iso = datetime.now(timezone.utc).isoformat()
    card_summary = (summary or "").strip() or _plain_summary_from_html(
        article_html_body,
        item.title,
    )
    mood_tags = moods or infer_moods(item, summary=card_summary)

    page_html = _render_article_page(
        item,
        article_html_body,
        pages_base_url=github_pages_base_url,
    )
    article_path.write_text(page_html, encoding="utf-8")
    logger.info("記事 HTML を出力: %s", article_path)

    entries = _load_index_entries(docs_path)
    entries = [e for e in entries if e.content_id != item.content_id]
    entries.append(
        IndexEntry(
            content_id=item.content_id,
            title=item.title,
            article_filename=filename,
            created_at=now_iso,
            image_url=item.image_url or "",
            summary=card_summary,
            moods=mood_tags,
        )
    )
    _save_index_entries(docs_path, entries)

    index_html = _render_index_page(entries, pages_base_url=github_pages_base_url)
    (docs_path / "index.html").write_text(index_html, encoding="utf-8")
    logger.info("index.html を更新しました（件数=%s moods=%s）", len(entries), mood_tags)
    return article_path
