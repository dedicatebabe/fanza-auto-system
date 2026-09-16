# ==========================================
# Version: 1.1.0
# Date: 2026-09-17
# Summary: 女優イベントを1週間前・前日・直前に宣伝する
# ==========================================
"""FANZAライブチャット女優イベントの公式バナーを読む。"""

from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

BANNER_URL = (
    "https://www.dmm.co.jp/live/api/-/online-banner/"
    "?size=300_250&type=avevent&af_id=nightlibrary-001"
)
JST = ZoneInfo("Asia/Tokyo")
SLOT_WEEK = "week"
SLOT_YDAY = "yday"
SLOT_SOON = "soon"
FRAME_RE = re.compile(
    r'<li class="frame"><a class="js-lc-i3Link" '
    r'href="(?P<href>https://www\.dmm\.co\.jp/live/chat/-/event-room/'
    r'=/character_id=(?P<character_id>\d+)/event=(?P<event_id>\d+)/[^"]*)"'
    r'[^>]*>\s*<div class="data"><img src="(?P<image>https://pics\.dmm\.co\.jp/'
    r'livechat/event/event_\d+\.jpg)" alt="(?P<name>[^"]+)">'
    r'</div><div class="data"><span>(?P=name)</span></div>'
    r'<div class="data"><p>(?P<when>[^<]+)</p></div></a></li>',
)
WHEN_RE = re.compile(
    r"(?P<month>\d{1,2})月(?P<day>\d{1,2})日"
    r"(?:\([^)]+\))?(?P<hour>\d{1,2}):(?P<minute>\d{2})"
)
PROMO_BY_SLOT = {
    SLOT_WEEK: (
        "来週、ライブチャットに出る。",
        "来週この顔がライブに出る。",
        "来週のライブ、この一本。",
    ),
    SLOT_YDAY: (
        "明日、ライブチャット。",
        "明日この時間にライブ。",
        "明日、この顔がチャットに出る。",
    ),
    SLOT_SOON: (
        "まもなくライブチャット。",
        "今夜この顔がライブに出る。",
        "もうすぐ始まる。ライブチャット。",
    ),
}


@dataclass(frozen=True)
class ActressEvent:
    """公式バナー上の女優イベント1件。"""

    event_id: str
    name: str
    when_text: str
    starts_at: datetime
    image_url: str
    room_url: str


def _parse_when(when_text: str, *, now: datetime) -> datetime | None:
    match = WHEN_RE.search(when_text or "")
    if not match:
        return None
    month = int(match.group("month"))
    day = int(match.group("day"))
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    local_now = now.astimezone(JST)
    year = local_now.year
    try:
        starts = datetime(year, month, day, hour, minute, tzinfo=JST)
    except ValueError:
        return None
    if starts < local_now - timedelta(days=2):
        try:
            starts = datetime(year + 1, month, day, hour, minute, tzinfo=JST)
        except ValueError:
            return None
    return starts


def fetch_actress_events(*, now: datetime | None = None) -> list[ActressEvent]:
    """公式オンラインバナーから女優イベント一覧を取る。"""
    response = requests.get(
        BANNER_URL,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.dmm.co.jp/",
        },
        timeout=30,
    )
    response.raise_for_status()
    now = now or datetime.now(timezone.utc)
    events: list[ActressEvent] = []
    seen: set[str] = set()
    for match in FRAME_RE.finditer(response.text):
        event_id = match.group("event_id")
        if event_id in seen:
            continue
        when_text = match.group("when").strip()
        starts_at = _parse_when(when_text, now=now)
        if starts_at is None:
            continue
        seen.add(event_id)
        events.append(
            ActressEvent(
                event_id=event_id,
                name=match.group("name").strip(),
                when_text=when_text,
                starts_at=starts_at,
                image_url=match.group("image"),
                room_url=match.group("href"),
            )
        )
    events.sort(key=lambda e: e.starts_at)
    logger.info("女優イベント %s 件", len(events))
    return events


def featured_events(events: list[ActressEvent]) -> list[ActressEvent]:
    """同じ日は最初の1人だけ。"""
    by_day: dict = {}
    for event in events:
        day = event.starts_at.astimezone(JST).date()
        if day not in by_day:
            by_day[day] = event
    return list(by_day.values())


def due_live_posts(
    events: list[ActressEvent],
    *,
    skip_ids: set[str],
    now: datetime | None = None,
) -> list[tuple[ActressEvent, str]]:
    """1週間前・前日・直前で、まだ出していない投稿。"""
    now = now or datetime.now(timezone.utc)
    local_now = now.astimezone(JST)
    today = local_now.date()
    due: list[tuple[ActressEvent, str]] = []
    for event in featured_events(events):
        event_day = event.starts_at.astimezone(JST).date()
        slots: list[str] = []
        if today == event_day - timedelta(days=7):
            slots.append(SLOT_WEEK)
        if today == event_day - timedelta(days=1):
            slots.append(SLOT_YDAY)
        if today == event_day and local_now < event.starts_at:
            slots.append(SLOT_SOON)
        for slot in slots:
            cid = event_content_id(event, slot)
            if cid in skip_ids:
                continue
            due.append((event, slot))
            logger.info(
                "投稿予定 slot=%s name=%s when=%s",
                slot,
                event.name,
                event.when_text,
            )
    return due


def build_live_event_tweet(
    event: ActressEvent,
    *,
    slot: str,
    chat_page_url: str,
) -> str:
    """名前と日時に、枠ごとの宣伝を足す。誘導はサイトのライブチャット。"""
    lines = PROMO_BY_SLOT.get(slot) or PROMO_BY_SLOT[SLOT_SOON]
    promo = random.choice(lines)
    return f"{event.name}\n{event.when_text}\n{promo}\n{chat_page_url}"


def event_content_id(event: ActressEvent, slot: str = SLOT_SOON) -> str:
    """posted.json 用の ID。枠ごとに分ける。"""
    return f"avevent-{event.event_id}-{slot}"
