# ==========================================
# Version: 1.3.0
# Date: 2026-09-14
# Summary: FANZA URLはXに出さない。ジャケット画像を添付する
# ==========================================
"""X（Twitter）への安全な投稿モジュール。"""

from __future__ import annotations

import logging
import random
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
import tweepy

logger = logging.getLogger(__name__)

MIN_PRE_POST_SLEEP_SEC = 30
MAX_PRE_POST_SLEEP_SEC = 300
ALLOWED_IMAGE_HOSTS = (
    "pics.dmm.co.jp",
    "pics.dmm.com",
    "awsimgsrc.dmm.co.jp",
    "awsimgsrc.dmm.com",
)


def _build_api_v2_client(
    api_key: str,
    api_secret: str,
    access_token: str,
    access_secret: str,
) -> tweepy.Client:
    """OAuth 1.0a User Context の Tweepy Client（API v2）を生成する。"""
    return tweepy.Client(
        consumer_key=api_key,
        consumer_secret=api_secret,
        access_token=access_token,
        access_token_secret=access_secret,
        wait_on_rate_limit=True,
    )


def _build_api_v1(
    api_key: str,
    api_secret: str,
    access_token: str,
    access_secret: str,
) -> tweepy.API:
    """画像アップロード用の API v1.1 クライアント。"""
    auth = tweepy.OAuth1UserHandler(
        api_key,
        api_secret,
        access_token,
        access_secret,
    )
    return tweepy.API(auth, wait_on_rate_limit=True)


def pre_post_random_sleep() -> int:
    """
    投稿前のランダム待機（凍結・Bot 検知対策）。

    戻り値: 実際に待機した秒数
    """
    seconds = random.randint(MIN_PRE_POST_SLEEP_SEC, MAX_PRE_POST_SLEEP_SEC)
    logger.info("X 投稿前待機: %s 秒", seconds)
    time.sleep(seconds)
    return seconds


def _tweet_id_from_response(response: object) -> str:
    tweet_id = ""
    if response is not None and getattr(response, "data", None):
        tweet_id = str(response.data.get("id", ""))
    if not tweet_id:
        raise RuntimeError(f"X 投稿レスポンスに ID がありません: {response}")
    return tweet_id


def _is_allowed_image_url(image_url: str) -> bool:
    host = (urlparse(image_url).hostname or "").lower()
    return any(host == allowed or host.endswith("." + allowed) for allowed in ALLOWED_IMAGE_HOSTS)


def _upload_jacket_media(
    image_url: str,
    *,
    api_key: str,
    api_secret: str,
    access_token: str,
    access_secret: str,
) -> str | None:
    """DMM公式ジャケットを一時保存して media_id を返す。失敗時は None。"""
    url = (image_url or "").strip()
    if not url or not _is_allowed_image_url(url):
        logger.warning("画像URLが空、または許可ホストではないため添付しない")
        return None
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        suffix = Path(urlparse(url).path).suffix.lower() or ".jpg"
        if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
            suffix = ".jpg"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
            tmp.write(response.content)
            tmp.flush()
            api = _build_api_v1(api_key, api_secret, access_token, access_secret)
            media = api.media_upload(filename=tmp.name)
        media_id = str(getattr(media, "media_id", "") or "")
        if not media_id:
            logger.warning("画像アップロード結果に media_id がない")
            return None
        logger.info("X 画像アップロード成功 media_id=%s", media_id)
        return media_id
    except Exception as exc:  # noqa: BLE001
        logger.warning("X 画像アップロード失敗: %s", exc)
        return None


def post_to_x(
    text: str,
    *,
    api_key: str,
    api_secret: str,
    access_token: str,
    access_secret: str,
    skip_sleep: bool = False,
    image_url: str | None = None,
) -> str:
    """
    紹介ページURLを含む投稿文を X に投稿する。

    FANZA商品URLは置かない。ジャケットがある場合のみ画像添付する。
    戻り値: 投稿 ID（文字列）
    """
    if not all([api_key, api_secret, access_token, access_secret]):
        raise ValueError("X API 認証情報が不足しています。")
    if not text or not text.strip():
        raise ValueError("投稿文が空です。")

    if not skip_sleep:
        pre_post_random_sleep()

    media_ids: list[str] = []
    if image_url:
        media_id = _upload_jacket_media(
            image_url,
            api_key=api_key,
            api_secret=api_secret,
            access_token=access_token,
            access_secret=access_secret,
        )
        if media_id:
            media_ids.append(media_id)

    client = _build_api_v2_client(api_key, api_secret, access_token, access_secret)
    logger.info("X へ投稿します（文字数=%s media=%s）", len(text), len(media_ids))
    kwargs: dict = {
        "text": text,
        "possibly_sensitive": True,
    }
    if media_ids:
        kwargs["media_ids"] = media_ids
    response = client.create_tweet(**kwargs)
    tweet_id = _tweet_id_from_response(response)
    logger.info("X 投稿成功 tweet_id=%s", tweet_id)
    return tweet_id
