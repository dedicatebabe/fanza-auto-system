# ==========================================
# Version: 1.1.0
# Date: 2026-09-13
# Summary: X API v2（tweepy.Client.create_tweet）へ移行
# ==========================================
"""X（Twitter）への安全な投稿モジュール。"""

from __future__ import annotations

import logging
import random
import time

import tweepy

logger = logging.getLogger(__name__)

MIN_PRE_POST_SLEEP_SEC = 30
MAX_PRE_POST_SLEEP_SEC = 300


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


def pre_post_random_sleep() -> int:
    """
    投稿前のランダム待機（凍結・Bot 検知対策）。

    戻り値: 実際に待機した秒数
    """
    seconds = random.randint(MIN_PRE_POST_SLEEP_SEC, MAX_PRE_POST_SLEEP_SEC)
    logger.info("X 投稿前待機: %s 秒", seconds)
    time.sleep(seconds)
    return seconds


def post_to_x(
    text: str,
    *,
    api_key: str,
    api_secret: str,
    access_token: str,
    access_secret: str,
    skip_sleep: bool = False,
) -> str:
    """
    クッションページ URL を含む投稿文を X に投稿する。

    戻り値: 投稿 ID（文字列）
    """
    if not all([api_key, api_secret, access_token, access_secret]):
        raise ValueError("X API 認証情報が不足しています。")
    if not text or not text.strip():
        raise ValueError("投稿文が空です。")

    if not skip_sleep:
        pre_post_random_sleep()

    client = _build_api_v2_client(api_key, api_secret, access_token, access_secret)
    logger.info("X へ投稿します（文字数=%s）", len(text))
    response = client.create_tweet(text=text)
    tweet_id = ""
    if response is not None and getattr(response, "data", None):
        tweet_id = str(response.data.get("id", ""))
    if not tweet_id:
        raise RuntimeError(f"X 投稿レスポンスに ID がありません: {response}")
    logger.info("X 投稿成功 tweet_id=%s", tweet_id)
    return tweet_id
