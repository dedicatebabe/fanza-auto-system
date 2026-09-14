# ==========================================
# Version: 1.2.0
# Date: 2026-09-14
# Summary: 本ツイのあと、FANZAアフィリエイトURLをリプする
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
REPLY_SLEEP_SEC = 3


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


def _tweet_id_from_response(response: object) -> str:
    tweet_id = ""
    if response is not None and getattr(response, "data", None):
        tweet_id = str(response.data.get("id", ""))
    if not tweet_id:
        raise RuntimeError(f"X 投稿レスポンスに ID がありません: {response}")
    return tweet_id


def post_to_x(
    text: str,
    *,
    api_key: str,
    api_secret: str,
    access_token: str,
    access_secret: str,
    skip_sleep: bool = False,
    reply_text: str | None = None,
) -> str:
    """
    本ツイを投稿し、必要なら続けてリプする。

    戻り値: 本ツイの投稿 ID（文字列）
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
    tweet_id = _tweet_id_from_response(response)
    logger.info("X 投稿成功 tweet_id=%s", tweet_id)

    reply = (reply_text or "").strip()
    if reply:
        time.sleep(REPLY_SLEEP_SEC)
        logger.info("X へリプします（文字数=%s）", len(reply))
        reply_response = client.create_tweet(
            text=reply,
            in_reply_to_tweet_id=tweet_id,
        )
        reply_id = _tweet_id_from_response(reply_response)
        logger.info("X リプ成功 tweet_id=%s", reply_id)
    return tweet_id
