# ==========================================
# Version: 1.5.0
# Date: 2026-09-16
# Summary: TYPEタグを公式ジャンルの日本語にする
# ==========================================
"""TYPEタグ（公式ジャンル）の付与。"""

from __future__ import annotations

from modules.ai_generator import genre_tags_for
from modules.dmm_api import FanzaItem

# チップは記事ごとの公式ジャンルから動的に出す。
MOOD_OPTIONS: tuple[str, ...] = ()


def infer_moods(item: FanzaItem, *, summary: str = "") -> list[str]:
    """
    公式ジャンルをTYPEタグにする。

    Love / Short などの英語気分タグは使わない。
    """
    _ = summary
    return genre_tags_for(item)
