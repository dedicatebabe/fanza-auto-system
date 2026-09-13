# ==========================================
# Version: 2.1.0
# Date: 2026-09-13
# Summary: フック型多行X投稿とレビュー構成プロンプト連携
# ==========================================
"""Google Gemini API を用いたコンテンツ生成モジュール。"""

from __future__ import annotations

import html
import logging
import os
import re
from pathlib import Path

from google import genai
from google.genai import types

from modules.dmm_api import FanzaItem

logger = logging.getLogger(__name__)

MODEL_NAME = "gemini-3.6-flash"
MAX_TWEET_LENGTH = 280
MIN_SALE_DISCOUNT_FOR_COPY = 30.0
CTA_LINE = "👇画像付きの詳しいレビューと動画はこちら"


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


def _sanitize_for_prompt(text: str) -> str:
    """セーフティフィルタ回避のため、露骨な表現をプロンプト用に置換する。"""
    cleaned = text
    replacements = (
        (r"[●○★☆]+", ""),
        (r"レ[●\*xXｘＸ]プ", "過激な展開"),
        (r"ちん[ぽポ]|デカチン|射精|中出し|絶頂|性交|挿入", "…"),
        (r"おっぱい|巨乳|美乳|裸|ヌード|エロ", "…"),
        (r"フェラ|パイズリ|手コキ|脚コキ|おま[●\*]|まん[●\*]", "…"),
        (r"レズ|NTR|キメセク|緊縛|アナル", "ドラマチックな展開"),
    )
    for pattern, repl in replacements:
        cleaned = re.sub(pattern, repl, cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:200] if cleaned else "人気エンタメ作品"


def _build_item_context(item: FanzaItem, *, sanitized: bool = True) -> str:
    """プロンプト用の作品情報テキストを組み立てる。"""
    title = _sanitize_for_prompt(item.title) if sanitized else item.title
    description = _sanitize_for_prompt(item.description) if sanitized else item.description
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


def _safety_settings() -> list[types.SafetySetting]:
    """アダルト紹介でもブロックされにくい安全設定。"""
    categories = (
        types.HarmCategory.HARM_CATEGORY_HARASSMENT,
        types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
    )
    return [
        types.SafetySetting(
            category=category,
            threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
        )
        for category in categories
    ]


def _generate_text(
    client: genai.Client,
    *,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
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
                safety_settings=_safety_settings(),
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gemini API 呼び出し失敗: %s", exc)
        return ""
    return _extract_response_text(response)


def _fallback_article_html(item: FanzaItem) -> str:
    """Gemini 失敗時のテンプレート HTML。"""
    title = html.escape(item.title)
    price = html.escape(_format_price_yen(item.sale_price))
    discount = ""
    if item.discount_percent is not None:
        discount = f"<li>いま約{int(item.discount_percent)}%OFFで手が届きやすい</li>"
    review = ""
    if item.review_average is not None:
        review = (
            f"<li>レビュー平均 {item.review_average}"
            f"（{item.review_count or 0}件）</li>"
        )
    desc = html.escape(item.description).replace("\n", "<br>")
    return f"""<h2>ひとことで言うと</h2>
<p>「{title}」は、テンポと空気感が印象に残る一本。観ている途中でテンションが上がるタイプです。</p>
<h2>見どころポイント</h2>
<ul>
  <li>導入のテンポが良く、飽きにくい</li>
  <li>シチュエーションの熱量が伝わりやすい</li>
  <li>販売価格: {price}</li>
  {discount}
  {review}
</ul>
<h2>おすすめな人</h2>
<p>話題作をサクッとチェックしたい人、セール中に「当たり」を引きたい人向け。</p>
<h2>ちょっと気になる点</h2>
<p>テイストが合うかどうかは好み次第。まずは公式の紹介と作画・雰囲気を見て判断するのがおすすめです。</p>
<h2>まとめ</h2>
<p>{desc}</p>
"""


def _fallback_x_post_text(item: FanzaItem, *, article_url: str) -> str:
    """Gemini 失敗時のフック型 X 投稿文。"""
    short_title = _sanitize_for_prompt(item.title)[:28]
    if item.discount_percent is not None and item.discount_percent >= MIN_SALE_DISCOUNT_FOR_COPY:
        pct = int(item.discount_percent)
        discount_line = f"いま約{pct}%OFF。この値段なら買い得。"
    else:
        discount_line = "今のうちにチェックしておくのが吉。"
    text = (
        f"マジでこの作品、刺さる人には刺さりすぎてヤバい…\n"
        f"{short_title}、熱量あるシチュ好きなら必見。\n"
        f"{discount_line}\n"
        f"{CTA_LINE}\n"
        f"{article_url}\n"
        f"#FANZAおすすめ"
    )
    return _normalize_x_post(text, article_url=article_url)


def extract_card_summary(article_html_body: str, item: FanzaItem) -> str:
    """記事 HTML からカード用の短い要約を抽出する。"""
    text = re.sub(r"<[^>]+>", " ", article_html_body or "")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return _sanitize_for_prompt(item.title)[:80]
    return text[:110]


def generate_article_html(client: genai.Client, item: FanzaItem) -> str:
    """
    GitHub Pages 用の HTML 本文（fragment）を生成する。

    返却値は section 要素を中心とした HTML 断片（ページテンプレートに埋め込む）。
    """
    context = _build_item_context(item, sanitized=True)
    system_prompt = _load_prompt_file("article.txt")
    user_prompt = (
        "この作品の魅力を、実際に見たかのように感情豊かに、自然な日本語でレビューして。"
        "AIっぽさを減らすため、具体的なシーンや、どんな人にオススメかを盛り込んで。"
        "見出しは指定どおりに、見どころポイントとおすすめな人を分かりやすく整理して。\n\n"
        f"{context}"
    )
    logger.info("Gemini: 記事 HTML 生成を開始 content_id=%s model=%s", item.content_id, MODEL_NAME)
    content = _generate_text(
        client,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=0.85,
    )
    html_body = _strip_code_fence(content)
    if not html_body:
        logger.warning(
            "Gemini: 記事 HTML が空のためリトライ content_id=%s",
            item.content_id,
        )
        retry_prompt = (
            "次の作品を観た人の口調で HTML レビューにして。"
            "見出しは ひとことで言うと / 見どころポイント / おすすめな人 / ちょっと気になる点 / まとめ。"
            "過激表現なし。\n\n"
            f"{context}"
        )
        content = _generate_text(
            client,
            system_prompt=system_prompt,
            user_prompt=retry_prompt,
            temperature=0.5,
        )
        html_body = _strip_code_fence(content)

    if not html_body:
        logger.warning(
            "Gemini: 記事 HTML が空のためテンプレートを使用 content_id=%s",
            item.content_id,
        )
        return _fallback_article_html(item)
    return html_body


def _normalize_x_post(text: str, *, article_url: str) -> str:
    """多行フック投稿を正規化し、CTA＋個別記事URLを末尾に固定する。"""
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    cleaned = cleaned.replace(article_url, "").strip()
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    lines = [ln.strip() for ln in cleaned.split("\n") if ln.strip()]
    lines = [ln for ln in lines if CTA_LINE not in ln and not ln.startswith("http")]

    body_lines: list[str] = []
    tag_tokens: list[str] = []
    for ln in lines:
        parts = ln.split()
        kept: list[str] = []
        for part in parts:
            if part.startswith("#"):
                tag_tokens.append(part)
            else:
                kept.append(part)
        if kept:
            body_lines.append(" ".join(kept))

    body = "\n".join(body_lines).strip()
    unique_tags = list(dict.fromkeys(tag_tokens))[:2]
    tags = " ".join(unique_tags).strip()
    parts = [body, CTA_LINE, article_url]
    if tags:
        parts.append(tags)
    result = "\n".join(p for p in parts if p).strip()
    if len(result) <= MAX_TWEET_LENGTH:
        return result
    return _truncate_multiline_tweet(result, article_url, MAX_TWEET_LENGTH)


def generate_x_post_text(client: genai.Client, item: FanzaItem, *, cushion_page_url: str) -> str:
    """
    X 投稿用テキストを生成する（フック型・多行）。

    cushion_page_url は個別記事のフルURL（トップではなく article_*.html）。
    """
    article_url = cushion_page_url
    context = _build_item_context(item, sanitized=True)
    discount_line = "割引情報がない場合は『今見ておく価値あり』と書いてください。"
    if item.discount_percent is not None:
        pct = int(item.discount_percent)
        discount_line = f"3行目では必ず約{pct}%OFF / セール感を強調してください。"

    system_prompt = _load_prompt_file("x_post.txt")
    user_prompt = (
        "スクロールを止める強力なフック投稿を作って。"
        "構成はプロンプト指定どおり。最終的に個別記事URLを単独行で置くこと。\n"
        f"{discount_line}\n"
        f"記事URL: {article_url}\n\n"
        f"作品情報:\n{context}\n\n"
        f"文字数上限: {MAX_TWEET_LENGTH}（URL含む）。出力は投稿文のみ。"
    )
    logger.info("Gemini: X 投稿文生成を開始 content_id=%s model=%s", item.content_id, MODEL_NAME)
    text = _generate_text(
        client,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=0.95,
    )
    if not text.strip():
        logger.warning(
            "Gemini: X 投稿文が空のためテンプレートを使用 content_id=%s",
            item.content_id,
        )
        return _fallback_x_post_text(item, article_url=article_url)
    return _normalize_x_post(text, article_url=article_url)


def _truncate_multiline_tweet(text: str, url: str, max_len: int) -> str:
    """改行をできるだけ残しつつ max_len 以内に切り詰める。"""
    if len(text) <= max_len:
        return text
    # URL と CTA は必須
    suffix = f"{CTA_LINE}\n{url}"
    # ハッシュタグがあれば末尾に残す
    tags = ""
    for line in reversed(text.split("\n")):
        if line.startswith("#"):
            tags = line
            break
    if tags:
        suffix = f"{suffix}\n{tags}"
    reserved = len(suffix) + 1
    allowed = max_len - reserved
    if allowed < 20:
        return suffix[:max_len]
    body = text
    for token in (url, CTA_LINE, tags):
        if token:
            body = body.replace(token, "")
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    if len(body) > allowed:
        body = body[: allowed - 1].rstrip() + "…"
    return f"{body}\n{suffix}".strip()


def _truncate_tweet(text: str, url: str, max_len: int) -> str:
    """URL を保持したまま max_len 以内に切り詰める（互換用）。"""
    return _truncate_multiline_tweet(text, url, max_len)