# ==========================================
# Version: 1.0.0
# Date: 2026-09-13
# Summary: 公式ジャンルではなく「今夜の気分」タグを付与
# ==========================================
"""気分（mood）タグの定義と推定。"""

from __future__ import annotations

from modules.dmm_api import FanzaItem

# 公式ジャンルのコピーではなく、編集メディア側の「今夜の気分」
MOOD_OPTIONS: tuple[str, ...] = (
    "甘め",
    "背徳",
    "スピード重視",
    "女優推し",
    "物語寄り",
    "セール特価",
)


def infer_moods(item: FanzaItem, *, summary: str = "") -> list[str]:
    """
    タイトル・概要・割引から気分タグを推定する。

    Gemini に依存せず安定して付与する（公式ジャンル名は使わない）。
    """
    text = f"{item.title}\n{item.description}\n{summary}".lower()
    moods: list[str] = []

    if item.discount_percent is not None and item.discount_percent >= 30:
        moods.append("セール特価")

    rules: list[tuple[str, tuple[str, ...]]] = [
        ("背徳", ("ntr", "寝取", "不倫", "上司", "相部屋", "裏切り", "禁断", "人妻")),
        ("甘め", ("キス", "いちゃ", "恋人", "彼女", "純愛", "甘", "デート")),
        ("スピード重視", ("ベスト", "総集編", "短時間", "ダイジェスト", "4時間", "8時間", "コンプリート")),
        ("女優推し", ("専属", "デビュー", "debut", "主演", "単体")),
        ("物語寄り", ("ドラマ", "物語", "ストーリー", "シナリオ", "感動")),
    ]
    for mood, keywords in rules:
        if any(k in text for k in keywords):
            moods.append(mood)

    # 最低1つは付ける
    if not moods:
        moods.append("女優推し" if "出演" in item.description else "スピード重視")

    # 順序を固定しつつ重複除去、最大3つ
    ordered: list[str] = []
    for mood in MOOD_OPTIONS:
        if mood in moods and mood not in ordered:
            ordered.append(mood)
    return ordered[:3]
