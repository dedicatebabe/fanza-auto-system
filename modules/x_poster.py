# ==========================================
# Version: 1.6.0
# Date: 2026-09-18
# Summary: 投稿はtweepy純正にし、センシティブ指定を外す
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
JACKET_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.dmm.co.jp/",
    "Accept": "image/jpeg,image/png,image/webp,image/*;q=0.8",
}


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


def _upload_local_media(
    path: Path,
    *,
    api_key: str,
    api_secret: str,
    access_token: str,
    access_secret: str,
) -> str | None:
    try:
        api = _build_api_v1(api_key, api_secret, access_token, access_secret)
        media = api.media_upload(filename=str(path))
        media_id = str(getattr(media, "media_id", "") or "")
        if not media_id:
            logger.warning("画像アップロード結果に media_id がない")
            return None
        logger.info("X 画像アップロード成功 media_id=%s path=%s", media_id, path)
        return media_id
    except Exception as exc:  # noqa: BLE001
        logger.warning("X 画像アップロード失敗: %s", exc)
        return None


def _upload_jacket_media(
    image_url: str,
    *,
    api_key: str,
    api_secret: str,
    access_token: str,
    access_secret: str,
    local_image_path: str | None = None,
) -> str | None:
    """ジャケットを media_id にする。ローカルカバーを優先する。"""
    local = Path(local_image_path) if local_image_path else None
    if local is not None and local.is_file():
        return _upload_local_media(
            local,
            api_key=api_key,
            api_secret=api_secret,
            access_token=access_token,
            access_secret=access_secret,
        )

    url = (image_url or "").strip()
    if not url or not _is_allowed_image_url(url):
        logger.warning("画像URLが空、または許可ホストではないため添付しない")
        return None
    try:
        response = requests.get(url, headers=JACKET_HEADERS, timeout=30)
        response.raise_for_status()
        suffix = Path(urlparse(url).path).suffix.lower() or ".jpg"
        if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
            suffix = ".jpg"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(response.content)
            tmp_path = Path(tmp.name)
        try:
            return _upload_local_media(
                tmp_path,
                api_key=api_key,
                api_secret=api_secret,
                access_token=access_token,
                access_secret=access_secret,
            )
        finally:
            tmp_path.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("X 画像ダウンロード失敗: %s", exc)
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
    local_image_path: str | None = None,
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
    if local_image_path or image_url:
        media_id = _upload_jacket_media(
            image_url or "",
            api_key=api_key,
            api_secret=api_secret,
            access_token=access_token,
            access_secret=access_secret,
            local_image_path=local_image_path,
        )
        if media_id:
            media_ids.append(media_id)
        else:
            logger.warning("ジャケット添付に失敗したためテキストのみ投稿する")

    client = _build_api_v2_client(api_key, api_secret, access_token, access_secret)
    logger.info("X へ投稿します（文字数=%s media=%s）", len(text), len(media_ids))
    kwargs: dict = {"text": text}
    if media_ids:
        kwargs["media_ids"] = media_ids
    try:
        response = client.create_tweet(**kwargs)
    except tweepy.HTTPException as exc:
        logger.error(
            "X 投稿失敗 status=%s errors=%s",
            getattr(exc, "response", None) and getattr(exc.response, "status_code", None),
            getattr(exc, "api_errors", None) or getattr(exc, "api_messages", None) or str(exc),
        )
        raise
    tweet_id = _tweet_id_from_response(response)
    logger.info("X 投稿成功 tweet_id=%s", tweet_id)
    return tweet_id
