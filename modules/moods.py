# ==========================================
# Version: 1.3.0
# Date: 2026-09-13
# Summary: 気分タグの表示を短い英語に変更
# ==========================================
"""気分（mood）タグの定義と推定。"""

from __future__ import annotations

from modules.dmm_api import FanzaItem

# 公式ジャンルのコピーではなく、編集メディア側の気分タグ
MOOD_OPTIONS: tuple[str, ...] = (
    "Sweet",
    "Taboo",
    "Quick",
    "Actress",
    "Story",
    "On sale",
)


def infer_moods_from_text(
    text: str,
    *,
    discount_percent: float | None = None,
) -> list[str]:
    """
    テキストと割引率から気分タグを推定する。

    背徳に当たる場合は、恋人／彼女などの甘め語を無視する。
    """
    blob = (text or "").lower()
    moods: list[str] = []

    if discount_percent is not None and discount_percent >= 30:
        moods.append("On sale")
    elif "セール" in blob or "%off" in blob or "% off" in blob or "％off" in blob or "on sale" in blob:
        moods.append("On sale")

    is_haitoku = any(
        k in blob
        for k in (
            "ntr",
            "寝取",
            "寝取り",
            "不倫",
            "上司",
            "相部屋",
            "裏切り",
            "禁断",
            "人妻",
            "浮気",
            "内緒",
            "寝取られ",
            "恋人の目の前",
            "彼氏の目の前",
            "好きぴ",
            "好きピ",
        )
    )
    if is_haitoku:
        moods.append("Taboo")

    if not is_haitoku and any(
        k in blob
        for k in ("いちゃ", "いちゃラブ", "純愛", "甘め", "デート", "sweet")
    ):
        moods.append("Sweet")

    if any(
        k in blob
        for k in (
            "ベスト",
            "総集編",
            "短時間",
            "ダイジェスト",
            "4時間",
            "8時間",
            "18時間",
            "コンプリート",
            "1116分",
        )
    ):
        moods.append("Quick")

    if any(
        k in blob
        for k in ("専属", "デビュー", "debut", "主演", "単体")
    ):
        moods.append("Actress")

    if any(
        k in blob
        for k in ("ドラマ", "物語", "ストーリー", "シナリオ", "感動")
    ):
        moods.append("Story")

    if not moods:
        moods.append("Actress" if "出演" in blob else "Quick")

    ordered: list[str] = []
    for mood in MOOD_OPTIONS:
        if mood in moods and mood not in ordered:
            ordered.append(mood)
    return ordered[:3]


def infer_moods(item: FanzaItem, *, summary: str = "") -> list[str]:
    """
    タイトル・概要・割引から気分タグを推定する。

    Gemini に依存せず安定して付与する（公式ジャンル名は使わない）。
    """
    return infer_moods_from_text(
        f"{item.title}\n{item.description}\n{summary}",
        discount_percent=item.discount_percent,
    )
