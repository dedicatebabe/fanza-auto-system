# ==========================================
# Version: 2.17.0
# Date: 2026-09-16
# Summary: Reviewの定型文を公式ジャンル1文にし、For youを出さない
# ==========================================
"""Google Gemini API を用いたコンテンツ生成モジュール。"""

from __future__ import annotations

import html
import logging
import os
import random
import re
from pathlib import Path

from google import genai
from google.genai import types

from modules.dmm_api import FanzaItem

logger = logging.getLogger(__name__)

MODEL_NAME = "gemini-3.6-flash"
MAX_TWEET_LENGTH = 280
MIN_SALE_DISCOUNT_FOR_COPY = 30.0
CTA_LINE = "👇詳細はこちら"
DROP_X_TAGS = (
    "#FANZAおすすめ",
    "#FANZA",
    "#アダルト",
)
BANNED_X_HOOKS = (
    "マジでこの作品",
    "刺さる人には刺さりすぎてヤバい",
    "全男が好きなやつ来た",
    "刺さる",
    "この値段なら買い得",
    "今のうちにチェックしておくのが吉",
    "詳細はこちら",
    "をご紹介",
    "おすすめ記事",
    "サンプル見て判断",
    "今夜の候補",
    "初撮りの胸",
)
ARTICLE_HEADING_FIXES = (
    ("In a nutshell", "Review"),
    ("Highlights", "Point"),
    ("Who it's for", "For you"),
    ("One caveat", "Note"),
    ("Wrap-up", "Last"),
)


def create_gemini_client(api_key: str | None = None) -> genai.Client:
    """
    Gemini クライアントを生成する。

    api_key 未指定時は環境変数 GEMINI_API_KEY を使用する。
    """
    key = (api_key or os.getenv("GEMINI_API_KEY", "")).strip()
    if not key:
        raise ValueError("GEMINI_API_KEY が設定されていません。")
    return genai.Client(api_key=key)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_prompt_file(name: str) -> str:
    """prompts/ 配下のプロンプトを読み込む（ヘッダーコメント行は除去）。"""
    path = _project_root() / "prompts" / name
    if not path.exists():
        raise FileNotFoundError(f"プロンプトファイルがありません: {path}")
    lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("# ===") or stripped.startswith("# Version:") or stripped.startswith("# Date:") or stripped.startswith("# Summary:"):
            continue
        if stripped == "#" or stripped.startswith("# ====="):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _format_price_yen(amount: int | None) -> str:
    if amount is None:
        return "価格は公式ページをご確認ください"
    return f"{amount:,}円"


def _sanitize_for_x(text: str, *, max_len: int = 400) -> str:
    """X投稿用。規約回避のため露骨な表現を穏やかな語に置換する。"""
    cleaned = text
    replacements = (
        (r"[●○★☆]{2,}", ""),
        (r"レ[●\*xXｘＸ]プ", "過激な展開"),
        (r"デカチン|デカマラ|ちん[ぽポ]", "刺激的な展開"),
        (r"連続射精|大量精子|中出し|生ハメ|筆おろし", "禁断寄りの展開"),
        (r"射精|絶頂|性交|挿入", "密着した展開"),
        (r"フェラ|パイズリ|手コキ|脚コキ|おま[●\*]|まん[●\*]", "濃厚な密着"),
        (r"ドピュドピュ|暴発", ""),
        (r"キメセク", "危険な誘惑"),
        (r"緊縛", "拘束プレイ"),
        (r"アナル", "ディープな展開"),
    )
    for pattern, repl in replacements:
        cleaned = re.sub(pattern, repl, cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:max_len] if cleaned else "人気エンタメ作品"


def official_intro_text(item: FanzaItem) -> str:
    """APIのcommentがあればそれ、なければ公式タイトル（紹介文兼ねる）。"""
    comment = (item.comment or "").strip()
    compact = re.sub(r"\s+", "", comment)
    if len(compact) >= 18:
        return comment
    return (item.title or "").strip()


def _build_item_context(item: FanzaItem, *, for_x: bool = False) -> str:
    """プロンプト用の作品情報テキストを組み立てる。"""
    title = (item.title or "").strip()[:180]
    description = (item.description or "").strip()[:800]
    intro = official_intro_text(item)[:500]
    lines = [
        f"タイトル: {title}",
        f"content_id: {item.content_id}",
        f"定価: {_format_price_yen(item.list_price)}",
        f"販売価格: {_format_price_yen(item.sale_price)}",
    ]
    if item.discount_percent is not None:
        lines.append(f"割引率: 約{int(item.discount_percent)}%OFF")
    if item.review_average is not None:
        lines.append(f"レビュー平均: {item.review_average}（{item.review_count or 0}件）")
    actresses = item.actresses or ()
    if actresses:
        lines.append("出演: " + "、".join(actresses[:4]))
    lines.append("公式紹介文:\n" + intro)
    if not for_x:
        lines.append("概要・属性:\n" + description)
    return "\n".join(lines)


def _strip_code_fence(text: str) -> str:
    """Markdown のコードフェンスを除去する。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:html)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _extract_response_text(response: types.GenerateContentResponse) -> str:
    """レスポンスから可視テキストを抽出する。"""
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        prompt_feedback = getattr(response, "prompt_feedback", None)
        logger.warning("Gemini: candidates が空です prompt_feedback=%s", prompt_feedback)
        return ""

    candidate = candidates[0]
    finish_reason = getattr(candidate, "finish_reason", None)
    logger.info("Gemini: finish_reason=%s", finish_reason)

    content = getattr(candidate, "content", None)
    parts = getattr(content, "parts", None) or []
    texts: list[str] = []
    for part in parts:
        if getattr(part, "thought", False):
            continue
        part_text = getattr(part, "text", None)
        if part_text:
            texts.append(str(part_text))

    if texts:
        return "\n".join(texts).strip()

    try:
        fallback = (response.text or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gemini: response.text 取得失敗: %s", exc)
        fallback = ""
    return fallback


def _safety_settings(*, adult_ok: bool = False) -> list[types.SafetySetting]:
    """
    安全設定。

    adult_ok=True は Web 記事用（性的な紹介を通す）。
    X 投稿は adult_ok=False のまま厳しくする。
    """
    none_th = getattr(
        types.HarmBlockThreshold,
        "BLOCK_NONE",
        types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
    )
    explicit = none_th if adult_ok else types.HarmBlockThreshold.BLOCK_ONLY_HIGH
    mapping = (
        (types.HarmCategory.HARM_CATEGORY_HARASSMENT, types.HarmBlockThreshold.BLOCK_ONLY_HIGH),
        (types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, types.HarmBlockThreshold.BLOCK_ONLY_HIGH),
        (types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, explicit),
        (types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, types.HarmBlockThreshold.BLOCK_ONLY_HIGH),
    )
    return [
        types.SafetySetting(category=category, threshold=threshold)
        for category, threshold in mapping
    ]


def _generate_text(
    client: genai.Client,
    *,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    adult_ok: bool = False,
) -> str:
    """Gemini でテキストを1回生成する。"""
    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=temperature,
                max_output_tokens=4096,
                safety_settings=_safety_settings(adult_ok=adult_ok),
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gemini API 呼び出し失敗: %s", exc)
        return ""
    return _extract_response_text(response)


def _item_hay(item: FanzaItem) -> str:
    """タイトルとAPI属性を1本の検索用テキストにする。"""
    return "\n".join(
        [
            item.title or "",
            item.description or "",
            " ".join(item.genres),
            " ".join(item.actresses),
            item.maker or "",
        ]
    )


def _parse_labeled_line(description: str, label: str) -> tuple[str, ...]:
    for line in (description or "").splitlines():
        if line.startswith(label):
            raw = line.split(":", 1)[-1].split("：", 1)[-1]
            return tuple(p.strip() for p in raw.split("/") if p.strip())
    return ()


def _item_genres(item: FanzaItem) -> tuple[str, ...]:
    if item.genres:
        return item.genres
    return _parse_labeled_line(item.description, "ジャンル")


def _item_actresses(item: FanzaItem) -> tuple[str, ...]:
    if item.actresses:
        return item.actresses
    return _parse_labeled_line(item.description, "出演")


def _item_maker(item: FanzaItem) -> str:
    if item.maker:
        return item.maker
    names = _parse_labeled_line(item.description, "メーカー")
    return names[0] if names else ""


def _hay_has(hay: str, *keys: str) -> bool:
    return any(k in hay for k in keys)


SKIP_GENRES = {
    "ハイビジョン",
    "4K",
    "独占配信",
    "VR専用",
    "ハイクオリティVR",
    "単体作品",
}
SKIP_SITUATION_GENRES = SKIP_GENRES | {
    "16時間以上作品",
    "4時間以上作品",
    "2時間以上作品",
}


def _situation_line(item: FanzaItem) -> str:
    """API属性から1文。観た感想は書かない。"""
    hay = _item_hay(item)
    vr = _hay_has(hay, "VR", "8K", "vr")
    ntr = _hay_has(hay, "NTR", "寝取", "内緒", "禁断", "好きピ")
    debut = _hay_has(hay, "デビュー", "Debut", "専属")
    busty = _hay_has(hay, "Hカップ", "巨乳", "爆乳", "美乳", "グラマー")
    best = _hay_has(hay, "ベスト", "総集編", "18時間", "1116分")
    swim = _hay_has(hay, "水着", "ビーチ", "ビキニ")
    if vr and ntr:
        return "VRのNTR。彼女が目の前で他の男に抱かれる。"
    if ntr:
        return "寝取られ。彼氏や恋人の前で崩れる。"
    if debut and busty:
        return "専属の初撮り。巨乳の新人。"
    if debut:
        return "専属の初撮り。"
    if vr and best:
        return "VRベスト。場面を選んで観る。"
    if vr:
        return "VR。目の前で密着する。"
    if swim:
        return "水着。ビーチや屋外のシチュ。"
    if _hay_has(hay, "母乳"):
        if _hay_has(hay, "兄嫁", "義姉", "姉・妹"):
            return "母乳。兄嫁のシチュ。"
        return "母乳もの。"
    genres = [g for g in _item_genres(item) if g not in SKIP_SITUATION_GENRES]
    if genres:
        return "、".join(genres[:3]) + "。"
    return "公式のジャンルはPointへ。"


def _credit_lines(item: FanzaItem) -> list[str]:
    """出演・メーカー。APIにあるものだけ。"""
    lines: list[str] = []
    actresses = _item_actresses(item)
    maker = _item_maker(item)
    if actresses:
        shown = actresses[:4]
        credit = "出演: " + "、".join(shown)
        if len(actresses) > 4:
            credit += "、ほか"
        lines.append(credit)
    if maker:
        lines.append("メーカー: " + maker)
    return lines


def _point_items(item: FanzaItem) -> list[str]:
    """見どころは公式ジャンル名。画質タグは後ろに回す。"""
    genres = _item_genres(item)
    primary = [g for g in genres if g not in SKIP_GENRES]
    filler = [g for g in genres if g in SKIP_GENRES]
    points: list[str] = []
    for name in primary:
        if name and name not in points:
            points.append(name)
        if len(points) >= 5:
            return points[:5]
    if len(points) < 3:
        for name in filler:
            if name and name not in points:
                points.append(name)
            if len(points) >= 3:
                break
    hay = _item_hay(item)
    extras = []
    if "8K" in hay and not any("8K" in p for p in points):
        extras.append("8K")
    if "Hカップ" in hay and not any("Hカップ" in p or "巨乳" in p for p in points):
        extras.append("Hカップ")
    for extra in extras:
        if extra not in points:
            points.insert(0, extra)
    if not points:
        points.append("公式ページで確認")
    return points[:5]


def _template_article_html(item: FanzaItem) -> str:
    """API属性の型枠HTML。観たレビューは書かない。"""
    credits = _credit_lines(item)
    credit_html = "".join(f"<p>{html.escape(line)}</p>\n" for line in credits)
    situation = html.escape(_situation_line(item))
    lis = "\n  ".join(f"<li>{html.escape(p)}</li>" for p in _point_items(item))
    return (
        f"<h2>Review</h2>\n"
        f"{credit_html}"
        f"<p>{situation}</p>\n"
        f"<h2>Point</h2>\n"
        f"<ul>\n  {lis}\n</ul>\n"
        f"<h2>Last</h2>\n"
        f"<p>詳細は公式ページへ。</p>\n"
    )


def _fallback_article_html(item: FanzaItem) -> str:
    """互換用。本文は型枠と同じ。"""
    return _template_article_html(item)


def _x_actor_name(item: FanzaItem) -> str:
    actresses = _item_actresses(item)
    if actresses:
        return actresses[0]
    return ""


_HARD_X_DROP = re.compile(
    r"中出し|生ハメ|射精|デカチン|デカマラ|フェラ|パイズリ|"
    r"レ[●\*xXｘＸ]プ|おま[●\*]|"
    r"スワッピング|乱痴気|キメセク|ドピュドピュ|筆おろし|etc\."
)


def _is_keyword_salad(sent: str) -> bool:
    """ジャンルの羅列だけで、話がない文を落とす。"""
    if _HARD_X_DROP.search(sent) and not re.search(
        r"彼女|パーティー|お願い|デビュー|ビーチ|兄嫁|はず",
        sent,
    ):
        return True
    if sent.count(" ") >= 5 and len(sent) < 50:
        return True
    return False


def _cut_official_blurb(item: FanzaItem, *, max_len: int = 90) -> str:
    """公式紹介から、話のある1〜2文を残す。"""
    text = official_intro_text(item).replace("パ―ティー", "パーティー")
    text = re.sub(r"^(?:【[^】]*】)+", "", text).strip()
    chunks = [p.strip() for p in re.split(r"(?<=[。…！？])", text) if p.strip()]
    if not chunks:
        chunks = [text]
    picked: list[str] = []
    for chunk in chunks:
        if _is_keyword_salad(chunk):
            continue
        if re.match(r"^(?:交姦|キメセク)*NTR\b", chunk) and not re.search(
            r"彼女|パーティー|はず",
            chunk,
        ):
            continue
        picked.append(chunk)
        if len(picked) >= 2 or len("".join(picked)) >= 55:
            break
    blurb = "".join(picked) if picked else text
    blurb = re.sub(r"^(?:交姦)?(?:キメセク)?NTR\s*", "", blurb).strip()
    hard = _HARD_X_DROP.search(blurb)
    if hard:
        blurb = blurb[: hard.start()].rstrip(" 、がのを")
    blurb = re.sub(r"\s+", " ", blurb).strip(" 、")
    if blurb and blurb[-1] not in "。…！？」":
        for sep in ("のに", "たら", "で", "が"):
            idx = blurb.rfind(sep)
            if idx >= 12:
                blurb = blurb[: idx + len(sep)]
                break
    if len(blurb) > max_len:
        cut = blurb[:max_len]
        if "、" in cut[20:]:
            cut = cut.rsplit("、", 1)[0]
        blurb = cut.rstrip("、") + "…"
    return blurb or _sanitize_for_x(item.title, max_len=40)


def _fallback_x_hook(item: FanzaItem) -> str:
    """公式紹介を短く切った投稿文。作文しない。"""
    blurb = _cut_official_blurb(item)
    name = _x_actor_name(item)
    if name and name not in blurb:
        return f"{blurb}\n{name}"
    return blurb


def _sale_whisper(item: FanzaItem) -> str:
    if item.discount_percent is None or item.discount_percent < MIN_SALE_DISCOUNT_FOR_COPY:
        return ""
    pct = int(item.discount_percent)
    return random.choice(
        (
            f"いま{pct}%OFF",
            f"今だけ{pct}%引き",
            f"{pct}%OFFになってる",
        )
    )


def _fallback_x_post_text(item: FanzaItem, *, page_url: str) -> str:
    """公式紹介を短く切った X 投稿文。"""
    hook = _fallback_x_hook(item)
    sale = _sale_whisper(item)
    if sale:
        text = f"{hook}\n{sale}\n{page_url}"
    else:
        text = f"{hook}\n{page_url}"
    return _normalize_x_post(text, page_url=page_url)


def _normalize_article_headings(raw_html: str) -> str:
    """古い見出しが残っていたら短い英語に直し、廃止見出しは落とす。"""
    html_body = raw_html or ""
    for old, new in ARTICLE_HEADING_FIXES:
        html_body = html_body.replace(f"<h2>{old}</h2>", f"<h2>{new}</h2>")
        html_body = html_body.replace(f"<h2>{html.escape(old)}</h2>", f"<h2>{new}</h2>")
    html_body = re.sub(
        r"<h2>For you</h2>\s*<p>.*?</p>\s*",
        "",
        html_body,
        flags=re.IGNORECASE | re.DOTALL,
    )
    html_body = re.sub(
        r"<h2>Note</h2>\s*<p>.*?</p>\s*",
        "",
        html_body,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return html_body


def _rewrite_generic_x_hook(text: str, item: FanzaItem) -> str:
    """使い回しの1行目や『刺さる』語尾なら、作品専用のフックに差し替える。"""
    lines = [ln for ln in (text or "").replace("\r\n", "\n").split("\n") if ln.strip()]
    if not lines:
        return text
    replaced = False
    for i, ln in enumerate(lines):
        if ln.startswith("#") or ln.startswith("http") or CTA_LINE in ln:
            continue
        if any(banned in ln for banned in BANNED_X_HOOKS):
            lines[i] = _fallback_x_hook(item) if i == 0 else _sanitize_for_x(
                _situation_line(item), max_len=36
            )
            replaced = True
    if not replaced:
        return text
    return "\n".join(lines)


def _plain_text_from_html(raw_html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw_html or "")
    return re.sub(r"\s+", " ", text).strip()


def extract_card_summary(article_html_body: str, item: FanzaItem) -> str:
    """
    カード用要約。

    API属性の型枠1文。タイトル全文・ジャンル列は出さない。
    """
    _ = article_html_body
    return _situation_line(item)


def generate_article_html(item: FanzaItem, client: genai.Client | None = None) -> str:
    """
    GitHub Pages 用の HTML 本文（fragment）を生成する。

    Gemini は使わない。APIのジャンル・出演・メーカーを型枠に入れる。
    """
    _ = client
    logger.info("記事HTMLを型枠で生成 content_id=%s", item.content_id)
    return _normalize_article_headings(_template_article_html(item))


def _normalize_x_post(text: str, *, page_url: str) -> str:
    """短い紹介文を正規化し、紹介ページURLだけ末尾に固定する。"""
    cleaned = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    cleaned = cleaned.replace(page_url, "").strip()
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    lines = [ln.strip() for ln in cleaned.split("\n") if ln.strip()]
    drop_needles = (
        CTA_LINE,
        "詳しいレビュー",
        "詳細はこちら",
        "この値段なら買い得",
        "今のうちにチェックしておくのが吉",
    )
    lines = [
        ln
        for ln in lines
        if not any(n in ln for n in drop_needles)
        and not ln.startswith("http")
        and "fanza.co.jp" not in ln.lower()
        and "dmm.co.jp" not in ln.lower()
        and "al.fanza.co.jp" not in ln.lower()
    ]

    body_lines: list[str] = []
    tag_tokens: list[str] = []
    for ln in lines:
        parts = ln.split()
        kept: list[str] = []
        for part in parts:
            if part.startswith("#"):
                if part not in DROP_X_TAGS:
                    tag_tokens.append(part)
            else:
                kept.append(part)
        if kept:
            body_lines.append(" ".join(kept))

    body = "\n".join(body_lines).strip()
    unique_tags = list(dict.fromkeys(tag_tokens))[:1]
    tags = " ".join(unique_tags).strip()
    parts = [body, page_url]
    if tags:
        parts.append(tags)
    result = "\n".join(p for p in parts if p).strip()
    if len(result) <= MAX_TWEET_LENGTH:
        return result
    suffix = page_url
    if tags:
        suffix = f"{page_url}\n{tags}"
    allowed = MAX_TWEET_LENGTH - len(suffix) - 1
    if allowed < 20:
        return suffix[:MAX_TWEET_LENGTH]
    trimmed = body[: allowed - 1].rstrip() + "…"
    return f"{trimmed}\n{suffix}".strip()


def generate_x_post_text(
    client: genai.Client,
    item: FanzaItem,
    *,
    cushion_page_url: str,
) -> str:
    """
    X 投稿用テキストを生成する（AVソムリエ口調・切り口1本）。

    置くURLは紹介ページのみ。FANZA直リンクは入れない。
    """
    page_url = cushion_page_url
    actress_name = "、".join(_item_actresses(item)[:3]) or "（表記なし）"
    synopsis = _sanitize_for_x(official_intro_text(item), max_len=280)
    title = _sanitize_for_x(item.title, max_len=120)

    system_prompt = _load_prompt_file("x_post.txt")
    user_prompt = (
        "【作品情報】\n"
        f"タイトル: {title}\n"
        f"女優名: {actress_name}\n"
        f"あらすじ: {synopsis}\n\n"
        f"紹介ページURL: {page_url}\n"
        "このURLを最後の行に置け。投稿文のみ出力。"
    )
    logger.info("Gemini: X 投稿文生成を開始 content_id=%s model=%s", item.content_id, MODEL_NAME)
    text = _generate_text(
        client,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=0.9,
        adult_ok=True,
    )
    if not text.strip():
        logger.warning(
            "Gemini: X 投稿文が空のためテンプレートを使用 content_id=%s",
            item.content_id,
        )
        return _fallback_x_post_text(item, page_url=page_url)
    text = _rewrite_generic_x_hook(text, item)
    return _normalize_x_post(text, page_url=page_url)