# ==========================================
# Version: 1.3.0
# Date: 2026-09-14
# Summary: ジャンル・出演・メーカーを構造化して保持
# ==========================================
"""DMM アフィリエイト API v3 連携モジュール。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
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

FetchMode = Literal["sale", "rank"]


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


def _iteminfo_names(iteminfo: dict[str, Any], key: str, *, limit: int = 8) -> tuple[str, ...]:
    """iteminfo の name 配列を取り出す。"""
    rows = iteminfo.get(key)
    if not isinstance(rows, list):
        return ()
    names: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw = str(row.get("name") or "").strip()
        if not raw:
            continue
        cleaned = _clean_person_name(raw) if key == "actress" else raw
        if cleaned and cleaned not in names:
            names.append(cleaned)
        if len(names) >= limit:
            break
    return tuple(names)


def _extract_structured_info(raw_item: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...], str]:
    """ジャンル・出演・メーカーを API の iteminfo から取る。"""
    iteminfo = raw_item.get("iteminfo") or {}
    if not isinstance(iteminfo, dict):
        iteminfo = {}
    genres = _iteminfo_names(iteminfo, "genre", limit=8)
    actresses = _iteminfo_names(iteminfo, "actress", limit=6)
    makers = _iteminfo_names(iteminfo, "maker", limit=1)
    maker = makers[0] if makers else ""
    return genres, actresses, maker


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

    genres, actresses, maker = _extract_structured_info(raw)
    description = _extract_description(
        genres=genres,
        actresses=actresses,
        maker=maker,
    )

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
    )


def _fetch_item_list_page(
    api_id: str,
    affiliate_id: str,
    *,
    mode: FetchMode,
    offset: int,
    hits: int = DEFAULT_HITS,
) -> list[dict[str, Any]]:
    """ItemList API を1ページ分呼び出す。"""
    # sale も rank で取得し、割引率はクライアント側で判定する。
    # sort=price は高額作品が多く、割引差分が出にくい。
    sort_param = "rank"
    params = {
        "api_id": api_id,
        "affiliate_id": affiliate_id,
        "site": SITE_FANZA,
        "service": SERVICE_DIGITAL,
        "floor": FLOOR_VIDEOA,
        "hits": str(hits),
        "offset": str(max(1, offset)),
        "sort": sort_param,
        "output": "json",
    }
    url = f"{ITEM_LIST_URL}?{urlencode(params)}"
    logger.debug("DMM API リクエスト: offset=%s sort=%s mode=%s", offset, sort_param, mode)
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


def _item_matches_mode(item: FanzaItem, mode: FetchMode) -> bool:
    """取得モードに応じて作品をフィルタする。"""
    if mode == "rank":
        return True
    if item.discount_percent is None:
        return False
    return item.discount_percent >= MIN_SALE_DISCOUNT_PERCENT


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
