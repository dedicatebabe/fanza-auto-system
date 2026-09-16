# ==========================================
# Version: 2.12.0
# Date: 2026-09-17
# Summary: 女優イベントを3回告知し、懐かしい女優を毎日追加する
# ==========================================
"""
FANZA（DMM API v3）のセール・人気作品を取得し、
GitHub Pages 用クッションページ生成と X 投稿を行うメインスクリプト。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from modules.ai_generator import (
    create_gemini_client,
    extract_card_summary,
    generate_article_html,
    generate_x_post_text,
    x_post_body_for_article,
)
from modules.dmm_api import FetchMode, fetch_fanza_item_for_posting, fetch_related_works
from modules.live_events import (
    build_live_event_tweet,
    due_live_posts,
    event_content_id,
    fetch_actress_events,
)
from modules.moods import infer_moods
from modules.page_builder import (
    build_cushion_page_url,
    cover_file_for,
    refresh_published_cards,
    write_article_and_update_index,
)
from modules.x_poster import post_to_x

POSTED_JSON = "posted.json"
POSTED_RETENTION_DAYS = 30

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("fanza-auto-system")


def project_root() -> Path:
    return Path(__file__).resolve().parent


def load_posted_history(path: Path) -> list[dict]:
    """posted.json を読み込む。"""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("posted.json の読み込みに失敗: %s", exc)
        return []
    posts = data.get("posts", [])
    if not isinstance(posts, list):
        return []
    return [p for p in posts if isinstance(p, dict)]


def save_posted_history(path: Path, posts: list[dict]) -> None:
    """posted.json を保存する。"""
    payload = {"posts": posts}
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def content_ids_posted_within_days(posts: list[dict], days: int) -> set[str]:
    """指定日数以内に投稿済みの content_id 集合を返す。"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    blocked: set[str] = set()
    for row in posts:
        cid = str(row.get("content_id", "")).strip()
        if not cid:
            continue
        raw_time = row.get("posted_at")
        if not raw_time:
            blocked.add(cid)
            continue
        try:
            posted_at = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
            if posted_at.tzinfo is None:
                posted_at = posted_at.replace(tzinfo=timezone.utc)
        except ValueError:
            blocked.add(cid)
            continue
        if posted_at >= cutoff:
            blocked.add(cid)
    return blocked


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"環境変数 {name} が設定されていません。")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FANZA 自動アフィリエイト実行")
    parser.add_argument(
        "--mode",
        choices=("sale", "rank", "nostalgic"),
        default="sale",
        help="sale=高割引セール優先, rank=売上順, nostalgic=懐かしい女優を追加",
    )
    parser.add_argument(
        "--live-event",
        action="store_true",
        help="女優イベントを1件、X に投稿する（動画紹介とは別枠）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="X 投稿と posted.json 更新をスキップ（ページ生成のみ）",
    )
    parser.add_argument(
        "--skip-x-sleep",
        action="store_true",
        help="X 投稿前のランダム待機をスキップ（デバッグ用）",
    )
    parser.add_argument(
        "--refresh-index",
        action="store_true",
        help="既存記事のカード要約と気分タグだけ再生成する",
    )
    return parser.parse_args()


def run_live_event_post(args: argparse.Namespace) -> int:
    """女優イベントを、1週間前・前日・直前の分だけ X に出す。"""
    pages_base = require_env("BASE_URL")
    chat_url = pages_base.rstrip("/") + "/chat.html"
    posted_path = project_root() / POSTED_JSON
    history = load_posted_history(posted_path)
    skip_ids = content_ids_posted_within_days(history, POSTED_RETENTION_DAYS)
    events = fetch_actress_events()
    due = due_live_posts(events, skip_ids=skip_ids)
    if not due:
        logger.info("今出す女優イベント告知はありません。")
        return 0
    x_api_key = ""
    x_api_secret = ""
    x_access_token = ""
    x_access_secret = ""
    if not args.dry_run:
        x_api_key = require_env("X_API_KEY")
        x_api_secret = require_env("X_API_SECRET")
        x_access_token = require_env("X_ACCESS_TOKEN")
        x_access_secret = require_env("X_ACCESS_SECRET")
    posted = 0
    for index, (event, slot) in enumerate(due):
        tweet_text = build_live_event_tweet(
            event,
            slot=slot,
            chat_page_url=chat_url,
        )
        logger.info("女優イベント投稿文 slot=%s:\n%s", slot, tweet_text)
        if args.dry_run:
            posted += 1
            continue
        post_to_x(
            tweet_text,
            api_key=x_api_key,
            api_secret=x_api_secret,
            access_token=x_access_token,
            access_secret=x_access_secret,
            skip_sleep=args.skip_x_sleep or index > 0,
            image_url=event.image_url,
        )
        cid = event_content_id(event, slot)
        now_iso = datetime.now(timezone.utc).isoformat()
        history = [h for h in history if str(h.get("content_id")) != cid]
        history.append(
            {
                "content_id": cid,
                "posted_at": now_iso,
                "mode": f"live-event-{slot}",
                "cushion_url": chat_url,
            }
        )
        save_posted_history(posted_path, history)
        skip_ids.add(cid)
        posted += 1
        logger.info("posted.json を更新しました content_id=%s", cid)
    logger.info("女優イベント告知 %s 件", posted)
    return 0


def main() -> int:
    """メイン処理。"""
    args = parse_args()
    mode: FetchMode = args.mode  # type: ignore[assignment]

    root = project_root()
    load_dotenv(root / ".env")

    try:
        if args.refresh_index:
            pages_base = require_env("BASE_URL")
            dmm_api_id = os.getenv("DMM_API_ID", "").strip()
            dmm_affiliate_id = os.getenv("DMM_AFFILIATE_ID", "").strip()
            count = refresh_published_cards(
                github_pages_base_url=pages_base,
                dmm_api_id=dmm_api_id,
                dmm_affiliate_id=dmm_affiliate_id,
            )
            logger.info("カード再生成が完了しました（%s件）", count)
            return 0

        if args.live_event:
            return run_live_event_post(args)

        dmm_api_id = require_env("DMM_API_ID")
        dmm_affiliate_id = require_env("DMM_AFFILIATE_ID")
        gemini_key = require_env("GEMINI_API_KEY")
        pages_base = require_env("BASE_URL")

        x_api_key = os.getenv("X_API_KEY", "").strip()
        x_api_secret = os.getenv("X_API_SECRET", "").strip()
        x_access_token = os.getenv("X_ACCESS_TOKEN", "").strip()
        x_access_secret = os.getenv("X_ACCESS_SECRET", "").strip()

        posted_path = root / POSTED_JSON
        history = load_posted_history(posted_path)
        skip_ids = content_ids_posted_within_days(history, POSTED_RETENTION_DAYS)
        logger.info(
            "実行 mode=%s / 30日以内スキップ ID 数=%s",
            mode,
            len(skip_ids),
        )

        item = fetch_fanza_item_for_posting(
            dmm_api_id,
            dmm_affiliate_id,
            mode=mode,
            skip_content_ids=skip_ids,
        )

        # X には FANZA 直リンクを置かず、紹介ページへ誘導する
        article_url = build_cushion_page_url(pages_base, item.content_id)
        logger.info("個別記事 URL: %s", article_url)

        gemini_client = create_gemini_client(gemini_key)
        tweet_text = generate_x_post_text(
            gemini_client,
            item,
            cushion_page_url=article_url,
        )
        logger.info("生成ツイート:\n%s", tweet_text)
        review_text = x_post_body_for_article(tweet_text)
        article_html = generate_article_html(item, review_text=review_text)
        card_summary = extract_card_summary(
            article_html,
            item,
            review_text=review_text,
        )
        moods = infer_moods(item, summary=card_summary)
        logger.info("TYPEタグ: %s", moods)
        related = fetch_related_works(dmm_api_id, dmm_affiliate_id, item)
        logger.info(
            "関連作品 series=%s actress=%s",
            len(related.series_items),
            len(related.actress_items),
        )

        write_article_and_update_index(
            item,
            article_html,
            github_pages_base_url=pages_base,
            summary=card_summary,
            moods=moods,
            related=related,
        )

        if args.dry_run:
            logger.info("dry-run: X 投稿と履歴更新をスキップしました。")
            logger.info("生成ツイート: %s", tweet_text)
            return 0

        if not all([x_api_key, x_api_secret, x_access_token, x_access_secret]):
            raise RuntimeError("X API 認証情報が不足しています（dry-run は --dry-run）。")

        post_to_x(
            tweet_text,
            api_key=x_api_key,
            api_secret=x_api_secret,
            access_token=x_access_token,
            access_secret=x_access_secret,
            skip_sleep=args.skip_x_sleep,
            image_url=item.image_url,
            local_image_path=str(cover_file_for(item.content_id)),
        )

        now_iso = datetime.now(timezone.utc).isoformat()
        history = [h for h in history if str(h.get("content_id")) != item.content_id]
        history.append(
            {
                "content_id": item.content_id,
                "posted_at": now_iso,
                "mode": mode,
                "cushion_url": article_url,
            }
        )
        save_posted_history(posted_path, history)
        logger.info("posted.json を更新しました content_id=%s", item.content_id)
        return 0

    except Exception as exc:
        logger.exception("処理中にエラーが発生しました: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
