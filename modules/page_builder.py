# ==========================================
# Version: 2.19.0
# Date: 2026-09-16
# Summary: 公開記事からジャンル欄を外す
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
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from modules.ai_generator import (
    extract_card_summary,
)
from modules.dmm_api import (
    FanzaItem,
    RelatedWorks,
    fetch_fanza_item_by_content_id,
    fetch_related_works,
)
from modules.moods import infer_moods

logger = logging.getLogger(__name__)

DOCS_DIR_NAME = "docs"
COVERS_DIR_NAME = "covers"
TEMPLATES_DIR_NAME = "templates"
INDEX_ENTRIES_FILE = ".index_entries.json"
CHAT_PAGE_FILENAME = "chat.html"
SITE_NAME = "夜のリブレ"
AFFILIATE_GATE = "https://al.fanza.co.jp/"
LIVECHAT_BANNER_AFFILIATE_ID = "nightlibrary-001"
NEW_ARRIVAL_WIDGET_ID = "9f031fe56e47210db1962053ed1bbddc"
JACKET_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.dmm.co.jp/",
    "Accept": "image/jpeg,image/png,image/webp,image/*;q=0.8",
}


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
    actresses: list[str] = field(default_factory=list)
    maker: str = ""
    genres: list[str] = field(default_factory=list)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def docs_dir() -> Path:
    path = _project_root() / DOCS_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def covers_dir() -> Path:
    path = docs_dir() / COVERS_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def cover_relpath_for(content_id: str) -> str:
    safe_id = re.sub(r"[^\w\-]", "_", content_id)
    return f"{COVERS_DIR_NAME}/{safe_id}.jpg"


def cover_file_for(content_id: str) -> Path:
    return docs_dir() / cover_relpath_for(content_id)


def save_jacket_cover(content_id: str, image_url: str) -> str:
    """
    DMM公式ジャケットを docs/covers に保存する。

    戻り値: 保存できた相対パス。失敗時は空文字。
    """
    url = (image_url or "").strip()
    if not url:
        return ""
    dest = cover_file_for(content_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        response = requests.get(url, headers=JACKET_HEADERS, timeout=30)
        response.raise_for_status()
        if len(response.content) < 1024:
            logger.warning("ジャケットが小さすぎるため保存しない content_id=%s", content_id)
            return ""
        dest.write_bytes(response.content)
        logger.info("ジャケットを保存: %s", dest)
        return cover_relpath_for(content_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ジャケット保存失敗 content_id=%s: %s", content_id, exc)
        return ""


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
    range_match = re.search(
        r"定価\s*([0-9,]+)円\s*→\s*今\s*([0-9,]+)円",
        text,
    )
    if range_match:
        list_price = _parse_yen_from_text(range_match.group(1))
        sale_price = _parse_yen_from_text(range_match.group(2))
        discount = None
        if list_price and sale_price and list_price > sale_price:
            discount = round((1.0 - sale_price / list_price) * 100.0, 1)
        off_match = re.search(r"約?\s*(\d+)\s*%\s*OFF", text, flags=re.IGNORECASE)
        if off_match:
            discount = float(off_match.group(1))
        return list_price, sale_price, discount

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
    if sale_price is None and list_price is None:
        simple = re.search(r'<p class="meta">\s*([0-9,]+)円\s*</p>', text)
        if simple:
            sale_price = _parse_yen_from_text(simple.group(1))
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
        genres=item.genres,
        actresses=item.actresses,
        maker=item.maker,
        comment=item.comment,
        series=item.series,
        series_id=item.series_id,
        actress_ids=item.actress_ids,
        maker_id=item.maker_id,
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


def _related_price_line(item: FanzaItem) -> str:
    if item.discount_percent is not None and item.discount_percent >= 30 and item.sale_price is not None:
        return f"{item.sale_price:,}円 / {int(item.discount_percent)}%OFF"
    if item.sale_price is not None:
        return f"{item.sale_price:,}円"
    if item.list_price is not None:
        return f"{item.list_price:,}円"
    return "公式で確認"


def _render_related_card(item: FanzaItem) -> str:
    href = html.escape(item.affiliate_url, quote=True)
    title = html.escape(item.title)
    price = html.escape(_related_price_line(item))
    thumb = ""
    if item.image_url:
        src = html.escape(item.image_url, quote=True)
        thumb = f'<div class="thumb"><img src="{src}" alt="{title}" loading="lazy"></div>'
    else:
        thumb = '<div class="thumb"></div>'
    return (
        f'<a class="related-card" href="{href}" rel="nofollow sponsored noopener" '
        f'target="_blank">{thumb}<div class="body"><p class="price">{price}</p>'
        f"<h3>{title}</h3></div></a>"
    )


def _render_related_section(title: str, items: tuple[FanzaItem, ...] | list[FanzaItem]) -> str:
    if not items:
        return ""
    heading = html.escape(title)
    cards = "\n".join(_render_related_card(item) for item in items)
    return (
        f"<section class=\"related-block\">\n"
        f"  <h2>{heading}</h2>\n"
        f"  <div class=\"related-grid\">\n    {cards}\n  </div>\n"
        f"</section>\n"
    )


def wrap_affiliate_url(destination: str, affiliate_id: str) -> str:
    """公式URLを DMM アフィリゲートで包む。"""
    dest = (destination or "").strip()
    af_id = (affiliate_id or "").strip()
    if not dest or not af_id:
        return ""
    return f"{AFFILIATE_GATE}?{urlencode({'lurl': dest, 'af_id': af_id, 'ch': 'api'})}"


def _affiliate_id_from_url(url: str) -> str:
    """既存アフィリURLから af_id を取り出す。"""
    text = html.unescape((url or "").strip())
    if not text:
        return ""
    values = parse_qs(urlparse(text).query).get("af_id") or []
    return str(values[0]).strip() if values else ""


def _resolve_affiliate_id(*candidates: str) -> str:
    """af_id そのもの、またはアフィリURLから ID を決める。"""
    for raw in candidates:
        text = (raw or "").strip()
        if not text:
            continue
        if "://" in text or "af_id=" in text:
            found = _affiliate_id_from_url(text)
            if found:
                return found
            continue
        if re.fullmatch(r"[\w.\-]+-\d+", text):
            return text
    return ""


def _render_chat_cards(affiliate_id: str) -> str:
    """公式ライブチャットバナー。管理画面の埋め込みを使う。"""
    _ = affiliate_id
    af = html.escape(LIVECHAT_BANNER_AFFILIATE_ID, quote=True)
    event_src = (
        "https://www.dmm.co.jp/live/api/-/online-banner/"
        f"?size=300_250&type=avevent&af_id={af}"
    )
    amateur_src = (
        "https://livechat.dmm.co.jp/publicads"
        f"?&size=S&design=B&affiliate_id={af}"
    )
    return (
        '<div class="chat-banner">'
        '<iframe id="onlineBannerAvevent" title="FANZAライブチャット 女優イベント" '
        'frameborder="0" scrolling="no" width="300" height="250" '
        f'src="{event_src}"></iframe></div>\n'
        '<div class="chat-banner">'
        '<iframe id="onlineBannerAmateur" title="FANZAライブチャット 素人" '
        'frameborder="0" scrolling="no" width="300" height="250" '
        f'src="{amateur_src}"></iframe></div>'
    )


def _render_new_arrival_frame() -> str:
    """公式新着ウィジェットのバナー枠だけ。"""
    wid = html.escape(NEW_ARRIVAL_WIDGET_ID, quote=True)
    return (
        '<div class="chat-banner">'
        f'<ins class="dmm-widget-placement" data-id="{wid}" '
        'style="background:transparent"></ins>'
        f'<script src="https://widget-view.dmm.co.jp/js/placement.js" '
        f'class="dmm-widget-scripts" data-id="{wid}"></script>'
        "</div>"
    )


def render_new_arrival_widget() -> str:
    """記事用の公式FANZA動画新着ウィジェット。"""
    return (
        '<section class="widget-strip">\n'
        "  <h2>FANZAの新着</h2>\n"
        '  <p class="widget-lead">公式バナー。紹介してない作品も出る。</p>\n'
        f'  {_render_new_arrival_frame()}\n'
        "</section>\n"
    )


def render_chat_block(affiliate_id: str, *, more_link: bool = True) -> str:
    """記事下のチャット入口。"""
    cards = _render_chat_cards(affiliate_id)
    more = ""
    if more_link:
        more = '<a class="chat-more" href="chat.html">チャットの入口へ</a>'
    return (
        '<section class="chat-block">\n'
        "  <h2>今いるチャット</h2>\n"
        '  <p class="chat-lead">公式バナー。今チャット中の顔が出る。</p>\n'
        f'  <div class="chat-grid">\n    {cards}\n  </div>\n'
        f"  {more}\n"
        "</section>\n"
    )


def render_index_banner_row(affiliate_id: str) -> str:
    """トップ最下部の公式バナー3つ。見出しは出さない。"""
    cards = _render_chat_cards(affiliate_id)
    widget = _render_new_arrival_frame()
    return (
        '<section class="banner-row" id="live">\n'
        f'  <div class="banner-grid">\n    {cards}\n    {widget}\n  </div>\n'
        "</section>\n"
    )


def render_related_html(related: RelatedWorks | None) -> str:
    """記事下の関連作品HTML。空なら何も出さない。"""
    if related is None:
        return ""
    parts: list[str] = []
    if related.series_items:
        label = f"同じシリーズ：{related.series_name}" if related.series_name else "同じシリーズ"
        parts.append(_render_related_section(label, related.series_items))
    if related.actress_items:
        label = f"{related.actress_name}の作品" if related.actress_name else "同じ出演"
        parts.append(_render_related_section(label, related.actress_items))
    if related.maker_items:
        label = f"同じメーカー：{related.maker_name}" if related.maker_name else "同じメーカー"
        parts.append(_render_related_section(label, related.maker_items))
    if not parts:
        return ""
    return '<div class="related">\n' + "".join(parts) + "</div>\n"


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


def _write_chat_page(
    docs_path: Path,
    *,
    pages_base_url: str,
    affiliate_id: str,
) -> None:
    """チャット専用ページを書き出す。"""
    cards = _render_chat_cards(affiliate_id)
    if not cards:
        cards = '<p class="empty">公式の待機一覧は準備中です。</p>'
    template = _load_template(CHAT_PAGE_FILENAME)
    page_html = _apply_template(
        template,
        {
            "PAGE_TITLE": "今いるチャット",
            "META_DESCRIPTION": "公式バナー。今チャット中の顔が出る。",
            "CANONICAL_URL": html.escape(pages_base_url.rstrip("/") + "/chat.html"),
            "CHAT_CARDS": cards,
            "YEAR": str(datetime.now(timezone.utc).year),
        },
    )
    dest = docs_path / CHAT_PAGE_FILENAME
    dest.write_text(page_html, encoding="utf-8")
    logger.info("chat.html を出力: %s", dest)


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
        raw_actresses = row.get("actresses") or []
        actresses = [str(a).strip() for a in raw_actresses if str(a).strip()]
        raw_genres = row.get("genres") or []
        genres = [str(g).strip() for g in raw_genres if str(g).strip()]
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
                    actresses=actresses,
                    maker=str(row.get("maker", "") or ""),
                    genres=genres,
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
                "actresses": e.actresses,
                "maker": e.maker,
                "genres": e.genres,
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
    related: RelatedWorks | None = None,
    affiliate_id: str = "",
    related_html: str | None = None,
) -> str:
    """個別記事 HTML をテンプレートから生成する。"""
    page_title = html.escape(item.title)
    meta_source = (summary or item.description.replace("\n", " ")).strip()
    description_meta = html.escape(meta_source[:160])
    affiliate = html.escape(item.affiliate_url, quote=True)
    resolved_af_id = _resolve_affiliate_id(affiliate_id, item.affiliate_url)
    canonical = html.escape(build_cushion_page_url(pages_base_url, item.content_id))
    price_line = html.escape(_format_price_display(item))
    cover_rel = cover_relpath_for(item.content_id)
    cover_path = cover_file_for(item.content_id)
    if cover_path.exists():
        hero_src = cover_rel
        og_src = html.escape(f"{pages_base_url.rstrip('/')}/{cover_rel}")
    else:
        hero_src = html.escape(item.image_url) if item.image_url else ""
        og_src = hero_src

    discount_badge = ""
    if item.discount_percent is not None and item.discount_percent >= 30:
        discount_badge = (
            f'<span class="badge">{int(item.discount_percent)}% OFF</span>'
        )

    og_image_tag = ""
    if og_src:
        og_image_tag = (
            f'<meta property="og:image" content="{og_src}">\n'
            f'  <meta name="twitter:image" content="{og_src}">'
        )

    hero_block = ""
    if hero_src:
        hero_block = (
            f'<div class="hero-media"><img src="{hero_src}" alt="{page_title}" '
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
            "RELATED_BLOCK": related_html if related_html is not None else render_related_html(related),
            "NEW_ARRIVAL_WIDGET": render_new_arrival_widget(),
            "CHAT_BLOCK": render_chat_block(resolved_af_id),
            "AFFILIATE_URL": affiliate,
            "YEAR": str(datetime.now(timezone.utc).year),
        },
    )


def _render_card(entry: IndexEntry) -> str:
    title = html.escape(entry.title)
    href = html.escape(entry.article_filename)
    date_str = html.escape(entry.created_at[:10] if entry.created_at else "")
    summary = html.escape(entry.summary or "レビュー記事を見る")
    moods = [m.strip() for m in entry.moods if str(m).strip()]
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
        f'<div class="more">続きを見る</div>'
        f"</div></a>"
    )


def _type_tags_from_entries(entries: list[IndexEntry]) -> list[str]:
    """掲載記事の公式ジャンルをTYPEチップにする。"""
    seen: list[str] = []
    for entry in entries:
        for mood in entry.moods:
            tag = str(mood).strip()
            if tag and tag not in seen:
                seen.append(tag)
    return sorted(seen)


def _render_mood_filters(entries: list[IndexEntry]) -> str:
    chips = [
        '<button type="button" class="mood-chip is-active" data-mood="all" aria-pressed="true">すべて</button>'
    ]
    for mood in _type_tags_from_entries(entries):
        chips.append(
            f'<button type="button" class="mood-chip" data-mood="{html.escape(mood)}" '
            f'aria-pressed="false">{html.escape(mood)}</button>'
        )
    return "\n".join(chips)


def _render_index_page(
    entries: list[IndexEntry],
    *,
    pages_base_url: str,
    affiliate_id: str = "",
) -> str:
    """トップ index.html をテンプレートから生成する。"""
    sorted_entries = sorted(entries, key=lambda e: e.created_at, reverse=True)
    if sorted_entries:
        cards = "\n".join(_render_card(e) for e in sorted_entries)
    else:
        cards = '<p class="empty">まだ紹介はありません。次の更新まで待ってください。</p>'

    template = _load_template("index.html")
    return _apply_template(
        template,
        {
            "PAGE_TITLE": SITE_NAME,
            "META_DESCRIPTION": "今夜見る一本を短く紹介。あとは公式で。",
            "CANONICAL_URL": html.escape(pages_base_url.rstrip("/") + "/"),
            "BANNER_ROW": render_index_banner_row(affiliate_id),
            "MOOD_FILTERS": _render_mood_filters(sorted_entries),
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
    related: RelatedWorks | None = None,
    related_html: str | None = None,
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
    saved_cover = save_jacket_cover(item.content_id, item.image_url)
    if cover_file_for(item.content_id).exists():
        card_image = cover_relpath_for(item.content_id)
    else:
        card_image = saved_cover or (item.image_url or "")
    card_summary = (summary or "").strip() or extract_card_summary(
        article_html_body,
        priced_item,
    )
    mood_tags = moods or infer_moods(priced_item, summary=card_summary)

    affiliate_id = _resolve_affiliate_id(priced_item.affiliate_url)
    page_html = _render_article_page(
        priced_item,
        article_html_body,
        pages_base_url=github_pages_base_url,
        summary=card_summary,
        related=related,
        affiliate_id=affiliate_id,
        related_html=related_html,
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
            image_url=card_image,
            summary=card_summary,
            moods=mood_tags,
            actresses=list(priced_item.actresses),
            maker=priced_item.maker,
            genres=list(priced_item.genres),
        )
    )
    _save_index_entries(docs_path, entries)

    index_html = _render_index_page(
        entries,
        pages_base_url=github_pages_base_url,
        affiliate_id=affiliate_id,
    )
    (docs_path / "index.html").write_text(index_html, encoding="utf-8")
    _write_chat_page(
        docs_path,
        pages_base_url=github_pages_base_url,
        affiliate_id=affiliate_id,
    )
    logger.info("index.html を更新しました（件数=%s moods=%s）", len(entries), mood_tags)
    return article_path


def _existing_related_html(article_html: str) -> str:
    """公開済み記事から関連作品ブロックを残す。"""
    start = article_html.find('<div class="related">')
    if start < 0:
        return ""
    end = len(article_html)
    for marker in ('<section class="widget-strip">', '<section class="chat-block">', '<a class="back"'):
        pos = article_html.find(marker, start)
        if pos != -1:
            end = min(end, pos)
    chunk = article_html[start:end].rstrip()
    return chunk + "\n" if chunk else ""


def _article_body_fragment(article_html: str) -> str:
    """公開済み記事からレビュー本文だけを取り出す。"""
    match = re.search(
        r'<div class="content">(.*?)</div>\s*<div class="cta-panel">',
        article_html,
        flags=re.DOTALL,
    )
    body = match.group(1).strip() if match else article_html
    body = re.sub(
        r"<h2>ジャンル</h2>\s*<ul>.*?</ul>\s*",
        "",
        body,
        flags=re.DOTALL,
    )
    return body.strip()


def _affiliate_url_from_html(article_html: str) -> str:
    match = re.search(r'class="cta"[^>]*href="([^"]+)"', article_html)
    if not match:
        return ""
    return html.unescape(match.group(1))


def _parse_credit_value(body: str, label: str) -> str:
    match = re.search(
        rf"<p>\s*{re.escape(label)}:\s*([^<]+)</p>",
        body,
    )
    if not match:
        return ""
    return html.unescape(match.group(1)).strip()


def _item_from_published_page(entry: IndexEntry, page_html: str) -> FanzaItem:
    """公開済みHTMLから型枠再生成用の FanzaItem を復元する。"""
    body = _article_body_fragment(page_html)
    price_source = page_html.split('<div class="content">', 1)[0]
    list_price, sale_price, discount = extract_prices_from_html(price_source)
    genres = tuple(
        html.unescape(g) for g in re.findall(r"<li>(.*?)</li>", body) if g.strip()
    )
    if not genres:
        genres = tuple(entry.genres)
    actress_raw = _parse_credit_value(body, "出演").replace("、ほか", "")
    actresses = tuple(p.strip() for p in actress_raw.split("、") if p.strip())
    if not actresses:
        actresses = tuple(entry.actresses)
    maker = _parse_credit_value(body, "メーカー") or entry.maker
    source_image = entry.image_url
    if source_image.startswith(COVERS_DIR_NAME + "/"):
        source_image = ""
    desc_parts = [
        f"ジャンル: {' / '.join(genres)}" if genres else "",
        f"出演: {' / '.join(actresses)}" if actresses else "",
        f"メーカー: {maker}" if maker else "",
    ]
    return FanzaItem(
        content_id=entry.content_id,
        title=entry.title,
        image_url=source_image,
        affiliate_url=_affiliate_url_from_html(page_html),
        list_price=list_price,
        sale_price=sale_price,
        discount_percent=discount if discount is not None else (
            30.0 if "セール" in entry.moods else None
        ),
        review_average=None,
        review_count=None,
        description="\n".join(p for p in desc_parts if p),
        genres=genres,
        actresses=actresses,
        maker=maker,
    )


def refresh_published_cards(
    *,
    github_pages_base_url: str,
    gemini_client=None,
    dmm_api_id: str = "",
    dmm_affiliate_id: str = "",
) -> int:
    """
    既存記事を現行テンプレで書き直し、関連作品を付け直す。

    Review本文は公開済みのものを残す。
    戻り値: 更新した件数
    """
    _ = gemini_client
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
        stub = _item_from_published_page(entry, page_html)
        live = None
        related = RelatedWorks()
        if dmm_api_id and dmm_affiliate_id:
            live = fetch_fanza_item_by_content_id(
                dmm_api_id,
                dmm_affiliate_id,
                stub.content_id,
            )
            if live is not None:
                related = fetch_related_works(dmm_api_id, dmm_affiliate_id, live)
        item = live or stub
        if item.image_url:
            save_jacket_cover(item.content_id, item.image_url)
        body = _article_body_fragment(page_html)
        related_html = None
        if not (related.series_items or related.actress_items or related.maker_items):
            kept = _existing_related_html(page_html)
            related_html = kept or None
        write_article_and_update_index(
            item,
            body,
            github_pages_base_url=github_pages_base_url,
            summary=entry.summary,
            moods=entry.moods or infer_moods(item, summary=entry.summary),
            created_at=entry.created_at,
            related=related,
            related_html=related_html,
        )
        latest = [
            e for e in _load_index_entries(docs_path) if e.content_id == entry.content_id
        ]
        refreshed.append(latest[0] if latest else entry)
        logger.info("記事を現行テンプレで再出力 content_id=%s", entry.content_id)

    _save_index_entries(docs_path, refreshed)
    chat_affiliate_id = _resolve_affiliate_id(dmm_affiliate_id)
    if not chat_affiliate_id and refreshed:
        sample_path = docs_path / refreshed[0].article_filename
        if sample_path.exists():
            chat_affiliate_id = _resolve_affiliate_id(
                _affiliate_url_from_html(sample_path.read_text(encoding="utf-8"))
            )
    index_html = _render_index_page(
        refreshed,
        pages_base_url=github_pages_base_url,
        affiliate_id=chat_affiliate_id,
    )
    (docs_path / "index.html").write_text(index_html, encoding="utf-8")
    _write_chat_page(
        docs_path,
        pages_base_url=github_pages_base_url,
        affiliate_id=chat_affiliate_id,
    )
    logger.info("index.html を再生成しました（件数=%s）", len(refreshed))
    return len(refreshed)
