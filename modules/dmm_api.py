# ==========================================
# Version: 1.7.0
# Date: 2026-09-17
# Summary: ギルガメ・でらべっぴん世代の女優名を増やす
# ==========================================
"""DMM アフィリエイト API v3 連携モジュール。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

ITEM_LIST_URL = "https://api.dmm.com/affiliate/v3/ItemList"
SITE_FANZA = "FANZA"
SERVICE_DIGITAL = "digital"
FLOOR_VIDEOA = "videoa"
DEFAULT_HITS = 100
MAX_FETCH_PAGES = 10
MIN_SALE_DISCOUNT_PERCENT = 30.0
RELATED_FETCH_HITS = 20
RELATED_LIMIT = 4
NOSTALGIC_KEYWORD_HITS = 20

FetchMode = Literal["sale", "rank", "nostalgic"]

# ギルガメッシュないと／でらべっぴん世代が覚える顔。熟女ジャンルではない。
NOSTALGIC_ACTRESSES = (
    "飯島愛",
    "川島和津実",
    "かとうれいこ",
    "細川ふみえ",
    "吉野公佳",
    "杉本彩",
    "黒木香",
    "松坂季実子",
    "樹まり子",
    "朝岡実嶺",
    "白石ひとみ",
    "小室友里",
    "あいだもも",
    "卑弥呼",
    "五島めぐ",
    "菊池エリ",
    "中原絵美",
    "斉藤唯",
    "村上麗奈",
    "小林ひとみ",
    "滝川真子",
    "憂木瞳",
    "浅倉舞",
    "橘ますみ",
    "桜樹ルイ",
    "寺崎泉",
    "田中露央沙",
    "藤谷しおり",
    "水沢早紀",
    "宏岡みらい",
    "吉川りりあ",
    "栗田ひろこ",
    "早乙女美紀",
    "田村香織",
    "御藤静",
    "木田彩水",
    "庄司みゆき",
    "河合美果",
    "木下優",
    "小沢奈美",
    "いとうしいな",
    "観月マリ",
    "水野さやか",
    "篠原真女",
    "星野ひかる",
    "工藤ひとみ",
    "水島みなみ",
    "瞳リョウ",
    "金沢文子",
    "三浦あいか",
    "及川奈央",
    "長谷川瞳",
    "夢野まりあ",
    "灘ジュン",
    "美竹涼子",
    "小沢まどか",
    "高樹マリア",
    "小沢菜穂",
    "天海麗",
    "桜朱音",
    "原田真緒",
    "蒼井そら",
    "穂花",
    "みひろ",
    "夏目ナナ",
    "吉沢明歩",
    "麻美ゆま",
    "小川あさ美",
    "小澤マリア",
    "あいだゆあ",
    "明日花キララ",
    "初音みのり",
    "希崎ジェシカ",
    "希美まゆ",
    "小向美奈子",
    "西條るり",
    "かすみ果穂",
    "西野翔",
    "上原カエラ",
    "麻生希",
    "安部ちなつ",
    "宝生瑠璃",
    "三浦綺音",
    "青木美津子",
    "松田千奈",
    "水野はるき",
    "加山なつこ",
    "八神康子",
    "北原梨奈",
    "清岡純子",
    "岸ゆり",
)


@dataclass(frozen=True)
class FanzaItem:
    """FANZA 作品1件分の正規化データ。"""

    content_id: str
    title: str
    image_url: str
    affiliate_url: str
    list_price: int | None
    sale_price: int | None
    discount_percent: float | None
    review_average: float | None
    review_count: int | None
    description: str
    genres: tuple[str, ...] = ()
    actresses: tuple[str, ...] = ()
    maker: str = ""
    comment: str = ""
    series: str = ""
    series_id: str = ""
    actress_ids: tuple[str, ...] = ()
    maker_id: str = ""


@dataclass(frozen=True)
class RelatedWorks:
    """記事下に出す関連作品。"""

    series_name: str = ""
    series_items: tuple[FanzaItem, ...] = ()
    actress_name: str = ""
    actress_items: tuple[FanzaItem, ...] = ()
    maker_name: str = ""
    maker_items: tuple[FanzaItem, ...] = ()


def _parse_yen(value: Any) -> int | None:
    """価格文字列を整数（円）に変換する。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in ("-", "—"):
        return None
    digits = re.sub(r"[^\d]", "", text)
    if not digits:
        return None
    return int(digits)


def _calc_discount_percent(list_price: int | None, sale_price: int | None) -> float | None:
    """定価と販売価格から割引率（%）を算出する。"""
    if list_price is None or sale_price is None:
        return None
    if list_price <= 0 or sale_price >= list_price:
        return None
    return round((1.0 - sale_price / list_price) * 100.0, 1)


def _clean_person_name(name: str) -> str:
    """出演名から読み仮名括弧を外す。"""
    text = (name or "").strip()
    if not text:
        return ""
    return text.split("（")[0].split("(")[0].strip() or text


def _iteminfo_id_names(
    iteminfo: dict[str, Any],
    key: str,
    *,
    limit: int = 8,
    clean_person: bool = False,
) -> tuple[tuple[str, str], ...]:
    """iteminfo の id と name を取り出す。"""
    rows = iteminfo.get(key)
    if not isinstance(rows, list):
        return ()
    pairs: list[tuple[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_name = str(row.get("name") or "").strip()
        if not raw_name:
            continue
        name = _clean_person_name(raw_name) if clean_person else raw_name
        item_id = str(row.get("id") or "").strip()
        if name and (item_id, name) not in pairs:
            pairs.append((item_id, name))
        if len(pairs) >= limit:
            break
    return tuple(pairs)


def _iteminfo_names(iteminfo: dict[str, Any], key: str, *, limit: int = 8) -> tuple[str, ...]:
    """iteminfo の name 配列を取り出す。"""
    clean_person = key == "actress"
    return tuple(
        name
        for _item_id, name in _iteminfo_id_names(
            iteminfo,
            key,
            limit=limit,
            clean_person=clean_person,
        )
    )


def _extract_structured_info(
    raw_item: dict[str, Any],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], str, str, str, str]:
    """ジャンル・出演・メーカー・シリーズを API の iteminfo から取る。"""
    iteminfo = raw_item.get("iteminfo") or {}
    if not isinstance(iteminfo, dict):
        iteminfo = {}
    genres = _iteminfo_names(iteminfo, "genre", limit=8)
    actress_pairs = _iteminfo_id_names(iteminfo, "actress", limit=6, clean_person=True)
    actresses = tuple(name for _aid, name in actress_pairs)
    actress_ids = tuple(aid for aid, _name in actress_pairs)
    maker_pairs = _iteminfo_id_names(iteminfo, "maker", limit=1)
    maker_id = maker_pairs[0][0] if maker_pairs else ""
    maker = maker_pairs[0][1] if maker_pairs else ""
    series_pairs = _iteminfo_id_names(iteminfo, "series", limit=1)
    series_id = series_pairs[0][0] if series_pairs else ""
    series = series_pairs[0][1] if series_pairs else ""
    return genres, actresses, actress_ids, maker, maker_id, series, series_id


def _extract_description(
    *,
    genres: tuple[str, ...],
    actresses: tuple[str, ...],
    maker: str,
) -> str:
    """API 属性をテキスト概要にする（X・気分タグ用）。"""
    parts: list[str] = []
    if genres:
        parts.append("ジャンル: " + " / ".join(genres))
    if actresses:
        parts.append("出演: " + " / ".join(actresses))
    if maker:
        parts.append("メーカー: " + maker)
    if not parts:
        return "人気のデジタル動画作品です。詳細は公式ページをご確認ください。"
    return "\n".join(parts)


def _normalize_item(raw: dict[str, Any]) -> FanzaItem | None:
    """API の item オブジェクトを FanzaItem に変換する。"""
    content_id = raw.get("content_id") or raw.get("product_id")
    if not content_id:
        return None
    content_id = str(content_id).strip()
    title = str(raw.get("title") or "").strip()
    if not title:
        return None

    image_url = ""
    image_obj = raw.get("imageURL") or raw.get("image_url") or {}
    if isinstance(image_obj, dict):
        image_url = (
            image_obj.get("large")
            or image_obj.get("small")
            or image_obj.get("list")
            or ""
        )
    image_url = str(image_url).strip()

    affiliate_url = str(raw.get("affiliateURL") or raw.get("affiliate_url") or raw.get("URL") or "").strip()
    if not affiliate_url:
        return None

    prices = raw.get("prices") or {}
    sale_price = _parse_yen(prices.get("price"))
    list_price = _parse_yen(prices.get("list_price"))
    deliveries = prices.get("deliveries") or {}
    delivery = deliveries.get("delivery") if isinstance(deliveries, dict) else deliveries
    if isinstance(delivery, dict):
        delivery = [delivery]
    if isinstance(delivery, list):
        for row in delivery:
            if not isinstance(row, dict):
                continue
            parsed = _parse_yen(row.get("price"))
            if parsed is not None and sale_price is None:
                sale_price = parsed
    if list_price is None and sale_price is not None:
        list_price = sale_price

    discount_percent = _calc_discount_percent(list_price, sale_price)

    review = raw.get("review") or {}
    review_average: float | None = None
    review_count: int | None = None
    if review.get("average") not in (None, ""):
        try:
            review_average = float(review["average"])
        except (TypeError, ValueError):
            review_average = None
    if review.get("count") not in (None, ""):
        try:
            review_count = int(review["count"])
        except (TypeError, ValueError):
            review_count = None

    genres, actresses, actress_ids, maker, maker_id, series, series_id = _extract_structured_info(raw)
    description = _extract_description(
        genres=genres,
        actresses=actresses,
        maker=maker,
    )
    comment = str(raw.get("comment") or "").strip()

    return FanzaItem(
        content_id=content_id,
        title=title,
        image_url=image_url,
        affiliate_url=affiliate_url,
        list_price=list_price,
        sale_price=sale_price,
        discount_percent=discount_percent,
        review_average=review_average,
        review_count=review_count,
        description=description,
        genres=genres,
        actresses=actresses,
        maker=maker,
        comment=comment,
        series=series,
        series_id=series_id,
        actress_ids=actress_ids,
        maker_id=maker_id,
    )


def _fetch_item_list(
    api_id: str,
    affiliate_id: str,
    *,
    sort: str = "rank",
    offset: int = 1,
    hits: int = DEFAULT_HITS,
    extra: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """ItemList API を1ページ分呼び出す。"""
    params = {
        "api_id": api_id,
        "affiliate_id": affiliate_id,
        "site": SITE_FANZA,
        "service": SERVICE_DIGITAL,
        "floor": FLOOR_VIDEOA,
        "hits": str(hits),
        "offset": str(max(1, offset)),
        "sort": sort,
        "output": "json",
    }
    if extra:
        for key, value in extra.items():
            if value:
                params[key] = value
    url = f"{ITEM_LIST_URL}?{urlencode(params)}"
    logger.debug("DMM API リクエスト: offset=%s sort=%s extra=%s", offset, sort, extra)
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    payload = response.json()
    result = payload.get("result") or {}
    status = result.get("status")
    if status not in (200, "200"):
        message = result.get("message") or payload
        raise RuntimeError(f"DMM API エラー status={status}: {message}")
    items = result.get("items") or []
    if not isinstance(items, list):
        return []
    return [i for i in items if isinstance(i, dict)]


def _fetch_item_list_page(
    api_id: str,
    affiliate_id: str,
    *,
    mode: FetchMode,
    offset: int,
    hits: int = DEFAULT_HITS,
) -> list[dict[str, Any]]:
    """投稿候補用の ItemList を1ページ分呼び出す。"""
    _ = mode
    # sale も rank で取得し、割引率はクライアント側で判定する。
    # sort=price は高額作品が多く、割引差分が出にくい。
    return _fetch_item_list(
        api_id,
        affiliate_id,
        sort="rank",
        offset=offset,
        hits=hits,
    )


def _item_matches_mode(item: FanzaItem, mode: FetchMode) -> bool:
    """取得モードに応じて作品をフィルタする。"""
    if mode in ("rank", "nostalgic"):
        return True
    if item.discount_percent is None:
        return False
    return item.discount_percent >= MIN_SALE_DISCOUNT_PERCENT


def _item_has_actress(item: FanzaItem, name: str) -> bool:
    """出演またはタイトルにその女優名があるか。"""
    needle = (name or "").replace(" ", "").replace("　", "")
    if not needle:
        return False
    for actress in item.actresses:
        if needle == actress.replace(" ", "").replace("　", ""):
            return True
    title = (item.title or "").replace(" ", "").replace("　", "")
    return needle in title


def fetch_nostalgic_item_for_posting(
    api_id: str,
    affiliate_id: str,
    *,
    skip_content_ids: set[str],
) -> FanzaItem:
    """懐かしい女優の未投稿作品を1件取る。既存の sale/rank とは別枠。"""
    if not api_id or not affiliate_id:
        raise ValueError("DMM_API_ID と DMM_AFFILIATE_ID が必要です。")
    start = datetime.now(timezone.utc).timetuple().tm_yday % len(NOSTALGIC_ACTRESSES)
    for step in range(len(NOSTALGIC_ACTRESSES)):
        name = NOSTALGIC_ACTRESSES[(start + step) % len(NOSTALGIC_ACTRESSES)]
        raw_items = _fetch_item_list(
            api_id,
            affiliate_id,
            sort="rank",
            offset=1,
            hits=NOSTALGIC_KEYWORD_HITS,
            extra={"keyword": name},
        )
        for raw in raw_items:
            item = _normalize_item(raw)
            if item is None:
                continue
            if item.content_id in skip_content_ids:
                continue
            if not _item_has_actress(item, name):
                continue
            logger.info(
                "懐かしい女優を採用: name=%s content_id=%s title=%s",
                name,
                item.content_id,
                item.title[:40],
            )
            return item
    raise RuntimeError("懐かしい女優の未投稿作品が見つかりませんでした。")


def fetch_fanza_item_for_posting(
    api_id: str,
    affiliate_id: str,
    *,
    mode: FetchMode,
    skip_content_ids: set[str],
) -> FanzaItem:
    """
    未投稿候補となる FANZA 作品を1件取得する。

    skip_content_ids に含まれる content_id はスキップし、
    最大 MAX_FETCH_PAGES ページまで再取得を試みる。
    """
    if mode == "nostalgic":
        return fetch_nostalgic_item_for_posting(
            api_id,
            affiliate_id,
            skip_content_ids=skip_content_ids,
        )
    if not api_id or not affiliate_id:
        raise ValueError("DMM_API_ID と DMM_AFFILIATE_ID が必要です。")

    hits = DEFAULT_HITS
    for page_index in range(MAX_FETCH_PAGES):
        offset = page_index * hits + 1
        raw_items = _fetch_item_list_page(
            api_id,
            affiliate_id,
            mode=mode,
            offset=offset,
            hits=hits,
        )
        if not raw_items:
            logger.warning("DMM API: offset=%s で作品が0件でした。", offset)
            continue

        candidates: list[FanzaItem] = []
        for raw in raw_items:
            normalized = _normalize_item(raw)
            if normalized is None:
                continue
            if normalized.content_id in skip_content_ids:
                continue
            if not _item_matches_mode(normalized, mode):
                continue
            candidates.append(normalized)

        if not candidates:
            continue

        if mode == "sale":
            candidates.sort(
                key=lambda x: x.discount_percent if x.discount_percent is not None else 0.0,
                reverse=True,
            )

        selected = candidates[0]
        logger.info(
            "採用作品: content_id=%s title=%s discount=%s",
            selected.content_id,
            selected.title[:40],
            selected.discount_percent,
        )
        return selected

    mode_label = "セール（30%以上OFF）" if mode == "sale" else "売上順（rank）"
    raise RuntimeError(
        f"未投稿の対象作品が見つかりませんでした（mode={mode_label}）。"
        "posted.json の履歴または API 設定を確認してください。"
    )


def fetch_fanza_item_by_content_id(
    api_id: str,
    affiliate_id: str,
    content_id: str,
) -> FanzaItem | None:
    """content_id 指定で FANZA 作品を1件取得する。"""
    cid = (content_id or "").strip()
    if not api_id or not affiliate_id or not cid:
        return None
    params = {
        "api_id": api_id,
        "affiliate_id": affiliate_id,
        "site": SITE_FANZA,
        "service": SERVICE_DIGITAL,
        "floor": FLOOR_VIDEOA,
        "cid": cid,
        "hits": "1",
        "offset": "1",
        "output": "json",
    }
    url = f"{ITEM_LIST_URL}?{urlencode(params)}"
    logger.info("DMM API: cid=%s を取得", cid)
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    payload = response.json()
    result = payload.get("result") or {}
    status = result.get("status")
    if status not in (200, "200"):
        logger.warning("DMM API cid取得エラー status=%s", status)
        return None
    items = result.get("items") or []
    if not isinstance(items, list):
        return None
    for raw in items:
        if not isinstance(raw, dict):
            continue
        normalized = _normalize_item(raw)
        if normalized is not None:
            return normalized
    return None


def _normalize_list(
    raw_items: list[dict[str, Any]],
    *,
    exclude_ids: set[str],
    limit: int,
) -> list[FanzaItem]:
    """API生データから関連候補を売上順のまま正規化する。"""
    picked: list[FanzaItem] = []
    seen = set(exclude_ids)
    for raw in raw_items:
        item = _normalize_item(raw)
        if item is None or item.content_id in seen:
            continue
        seen.add(item.content_id)
        picked.append(item)
        if len(picked) >= limit:
            break
    return picked


def _fetch_related_by_article(
    api_id: str,
    affiliate_id: str,
    *,
    article: str,
    article_id: str,
    exclude_ids: set[str],
    limit: int = RELATED_LIMIT,
) -> list[FanzaItem]:
    """article / article_id 指定で売上順の関連作品を取る。"""
    if not article_id:
        return []
    try:
        raw_items = _fetch_item_list(
            api_id,
            affiliate_id,
            sort="rank",
            offset=1,
            hits=RELATED_FETCH_HITS,
            extra={"article": article, "article_id": article_id},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("関連作品の取得に失敗 article=%s id=%s: %s", article, article_id, exc)
        return []
    return _normalize_list(raw_items, exclude_ids=exclude_ids, limit=limit)


def fetch_related_works(
    api_id: str,
    affiliate_id: str,
    item: FanzaItem,
) -> RelatedWorks:
    """
    同じシリーズ、同じ主演女優の作品を FANZA 売上順で取得する。

    取れない場合は空。投稿本体は落とさない。
    """
    exclude = {item.content_id}
    series_items = _fetch_related_by_article(
        api_id,
        affiliate_id,
        article="series",
        article_id=item.series_id,
        exclude_ids=exclude,
    )
    exclude.update(x.content_id for x in series_items)

    actress_id = item.actress_ids[0] if item.actress_ids else ""
    actress_name = item.actresses[0] if item.actresses else ""
    actress_items = _fetch_related_by_article(
        api_id,
        affiliate_id,
        article="actress",
        article_id=actress_id,
        exclude_ids=exclude,
    )
    exclude.update(x.content_id for x in actress_items)

    maker_items: list[FanzaItem] = []
    if not series_items and not actress_items:
        maker_items = _fetch_related_by_article(
            api_id,
            affiliate_id,
            article="maker",
            article_id=item.maker_id,
            exclude_ids=exclude,
        )
    logger.info(
        "関連作品 content_id=%s series=%s(%s) actress=%s(%s) maker=%s(%s)",
        item.content_id,
        len(series_items),
        item.series or "-",
        len(actress_items),
        actress_name or "-",
        len(maker_items),
        item.maker or "-",
    )
    return RelatedWorks(
        series_name=item.series,
        series_items=tuple(series_items),
        actress_name=actress_name,
        actress_items=tuple(actress_items),
        maker_name=item.maker,
        maker_items=tuple(maker_items),
    )
