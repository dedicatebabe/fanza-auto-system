# ==========================================
# Version: 2.7.0
# Date: 2026-09-14
# Summary: 見出しとカード文言を日本人にもわかる英語に
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

from modules.ai_generator import extract_card_summary
from modules.dmm_api import FanzaItem
from modules.moods import MOOD_OPTIONS, infer_moods, infer_moods_from_text

logger = logging.getLogger(__name__)

DOCS_DIR_NAME = "docs"
TEMPLATES_DIR_NAME = "templates"
INDEX_ENTRIES_FILE = ".index_entries.json"
SITE_NAME = "Yoru no Libre X"


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


def _parse_yen_from_text(raw: str) -> int | None:
    digits = re.sub(r"[^\d]", "", raw or "")
    if not digits:
        return None
    return int(digits)


def extract_prices_from_html(raw_html: str) -> tuple[int | None, int | None, float | None]:
    """本文に残った定価・販売価格・割引率を拾う。"""
    text = raw_html or ""
    list_match = re.search(r"定価\s*([0-9,]+)円", text)
    sale_match = re.search(r"販売価格[:：]\s*([0-9,]+)円", text)
    if sale_match is None:
        sale_match = re.search(r"(?:今|約\d+%OFFの)\s*([0-9,]+)円", text)
    off_match = re.search(r"約?\s*(\d+)\s*%\s*OFF", text, flags=re.IGNORECASE)
    list_price = _parse_yen_from_text(list_match.group(1)) if list_match else None
    sale_price = _parse_yen_from_text(sale_match.group(1)) if sale_match else None
    discount = float(off_match.group(1)) if off_match else None
    if discount is None and list_price and sale_price and list_price > sale_price:
        discount = round((1.0 - sale_price / list_price) * 100.0, 1)
    return list_price, sale_price, discount


def _item_with_html_prices(item: FanzaItem, raw_html: str) -> FanzaItem:
    """APIで欠けた価格を本文から補う。"""
    list_price, sale_price, discount = extract_prices_from_html(raw_html)
    return FanzaItem(
        content_id=item.content_id,
        title=item.title,
        image_url=item.image_url,
        affiliate_url=item.affiliate_url,
        list_price=item.list_price or list_price,
        sale_price=item.sale_price or sale_price,
        discount_percent=item.discount_percent if item.discount_percent is not None else discount,
        review_average=item.review_average,
        review_count=item.review_count,
        description=item.description,
    )


def _format_price_display(item: FanzaItem) -> str:
    if item.list_price and item.sale_price and item.list_price > item.sale_price:
        return f"定価 {item.list_price:,}円 → 今 {item.sale_price:,}円"
    if item.sale_price is not None:
        return f"{item.sale_price:,}円"
    if item.list_price is not None and item.discount_percent is not None:
        return f"定価 {item.list_price:,}円 / 約{int(item.discount_percent)}%OFF"
    if item.discount_percent is not None:
        return f"いま約{int(item.discount_percent)}%OFF"
    return "価格は公式サイトでご確認ください"


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
    summary: str = "",
) -> str:
    """個別記事 HTML をテンプレートから生成する。"""
    page_title = html.escape(item.title)
    meta_source = (summary or item.description.replace("\n", " ")).strip()
    description_meta = html.escape(meta_source[:160])
    image = html.escape(item.image_url) if item.image_url else ""
    affiliate = html.escape(item.affiliate_url, quote=True)
    canonical = html.escape(build_cushion_page_url(pages_base_url, item.content_id))
    price_line = html.escape(_format_price_display(item))

    discount_badge = ""
    if item.discount_percent is not None and item.discount_percent >= 30:
        discount_badge = (
            f'<span class="badge">{int(item.discount_percent)}% OFF</span>'
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
    moods = [m for m in entry.moods if m in MOOD_OPTIONS] or ["Short"]
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
        f'<div class="more">Read →</div>'
        f"</div></a>"
    )


def _render_mood_filters() -> str:
    chips = [
        '<button type="button" class="mood-chip is-active" data-mood="all" aria-pressed="true">All</button>'
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
        cards = '<p class="empty">No review yet. Check after the next update.</p>'

    template = _load_template("index.html")
    return _apply_template(
        template,
        {
            "PAGE_TITLE": SITE_NAME,
            "META_DESCRIPTION": "Short FANZA review. Read a bit, then open FANZA.",
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
    created_at: str | None = None,
) -> Path:
    """
    個別記事 HTML を書き出し、index.html とメタデータを更新する。

    戻り値: 生成した記事ファイルの Path
    """
    docs_path = docs_dir()
    filename = article_filename_for(item.content_id)
    article_path = docs_path / filename
    now_iso = created_at or datetime.now(timezone.utc).isoformat()
    priced_item = _item_with_html_prices(item, article_html_body)
    card_summary = (summary or "").strip() or extract_card_summary(
        article_html_body,
        priced_item,
    )
    mood_tags = moods or infer_moods(priced_item, summary=card_summary)

    page_html = _render_article_page(
        priced_item,
        article_html_body,
        pages_base_url=github_pages_base_url,
        summary=card_summary,
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


def _article_body_fragment(article_html: str) -> str:
    """公開済み記事からレビュー本文だけを取り出す。"""
    match = re.search(
        r'<div class="content">(.*?)</div>\s*<div class="cta-panel">',
        article_html,
        flags=re.DOTALL,
    )
    if match:
        return match.group(1).strip()
    return article_html


def refresh_published_cards(*, github_pages_base_url: str) -> int:
    """
    既存記事からカード要約と気分タグを再抽出し、index.html を更新する。

    戻り値: 更新した件数
    """
    docs_path = docs_dir()
    entries = _load_index_entries(docs_path)
    if not entries:
        logger.warning("更新対象の記事がありません。")
        return 0

    refreshed: list[IndexEntry] = []
    for entry in entries:
        article_path = docs_path / entry.article_filename
        if not article_path.exists():
            refreshed.append(entry)
            continue
        page_html = article_path.read_text(encoding="utf-8")
        body = _article_body_fragment(page_html)
        list_price, sale_price, discount = extract_prices_from_html(page_html)
        stub = FanzaItem(
            content_id=entry.content_id,
            title=entry.title,
            image_url=entry.image_url,
            affiliate_url="",
            list_price=list_price,
            sale_price=sale_price,
            discount_percent=discount if discount is not None else (
                30.0 if any(m in entry.moods for m in ("Sale", "On sale", "セール特価")) else None
            ),
            review_average=None,
            review_count=None,
            description=body,
        )
        summary = extract_card_summary(body, stub)
        moods = infer_moods_from_text(
            f"{entry.title}\n{body}\n{summary}",
            discount_percent=stub.discount_percent,
        )
        price_line = html.escape(_format_price_display(stub))
        page_html = re.sub(
            r'<p class="meta">.*?</p>',
            f'<p class="meta">{price_line}</p>',
            page_html,
            count=1,
        )
        article_path.write_text(page_html, encoding="utf-8")
        refreshed.append(
            IndexEntry(
                content_id=entry.content_id,
                title=entry.title,
                article_filename=entry.article_filename,
                created_at=entry.created_at,
                image_url=entry.image_url,
                summary=summary,
                moods=moods,
            )
        )
        logger.info(
            "カード更新 content_id=%s moods=%s summary=%s",
            entry.content_id,
            moods,
            summary[:40],
        )

    _save_index_entries(docs_path, refreshed)
    index_html = _render_index_page(refreshed, pages_base_url=github_pages_base_url)
    (docs_path / "index.html").write_text(index_html, encoding="utf-8")
    logger.info("index.html を再生成しました（件数=%s）", len(refreshed))
    return len(refreshed)
