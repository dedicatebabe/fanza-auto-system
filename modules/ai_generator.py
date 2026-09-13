# ==========================================
# Version: 2.5.0
# Date: 2026-09-13
# Summary: フォールバックもエロ動画レビューにする
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
        (r"おっぱい|裸|ヌード", "肌の露出"),
        (r"巨乳|爆乳|美乳|Hカップ", "グラマー"),
        (r"NTR|寝取り・寝取られ・NTR|寝取られ", "禁断の三角関係"),
        (r"キメセク", "危険な誘惑"),
        (r"緊縛", "拘束プレイ"),
        (r"アナル", "ディープな展開"),
    )
    for pattern, repl in replacements:
        cleaned = re.sub(pattern, repl, cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:max_len] if cleaned else "人気エンタメ作品"


def _build_item_context(item: FanzaItem, *, for_x: bool = False) -> str:
    """プロンプト用の作品情報テキストを組み立てる。"""
    if for_x:
        title = _sanitize_for_x(item.title, max_len=80)
        description = _sanitize_for_x(item.description, max_len=400)
    else:
        title = (item.title or "").strip()[:180]
        description = (item.description or "").strip()[:800]
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


def _fallback_hook(item: FanzaItem) -> str:
    """作品属性から、何が抜けるかを書く導入文。"""
    blob = f"{item.title}\n{item.description}"
    if any(k in blob for k in ("NTR", "寝取", "内緒", "禁断")):
        return "彼氏や恋人の前で彼女が他の男に抱かれる背徳もの。見られるスリルで抜きたい夜向け。"
    if any(k in blob for k in ("デビュー", "Debut", "専属")):
        return "専属新人の初撮り。顔と身体が初めてカメラの前で乱れるのを観る一本。"
    if any(k in blob for k in ("VR", "8K")):
        return "目の前で密着されるVR。吐息と胸の距離感で抜きたい人向け。"
    return "タイトルのシチュで抜けるかどうか、身体と行為の方向を見て選ぶ一本。"


def _fallback_article_html(item: FanzaItem) -> str:
    """Gemini 失敗時のエロ動画レビュー HTML。価格は見どころに入れない。"""
    blob = f"{item.title}\n{item.description}"
    hook = html.escape(_fallback_hook(item))
    points: list[str] = []
    if any(k in blob for k in ("NTR", "寝取", "内緒")):
        points.append("好きな人の目の前で崩れるNTR")
    if any(k in blob for k in ("中出し", "生ハメ")):
        points.append("生で中までいく展開")
    if any(k in blob for k in ("Hカップ", "巨乳", "爆乳", "美乳")):
        points.append("胸の圧が主軸。寄りの画が強い")
    if any(k in blob for k in ("水着", "ビーチ", "ビキニ")):
        points.append("水着のまま熱が上がる")
    if any(k in blob for k in ("VR", "8K")):
        points.append("目の前のキスと密着。VR向きの距離")
    if any(k in blob for k in ("ベスト", "総集編")):
        points.append("場面を飛ばして、今抜きたいシチュだけ観られる")
    if any(k in blob for k in ("デビュー", "Debut", "専属")):
        points.append("初めて乱れる顔。初物感で抜く")
    if any(k in blob for k in ("潮吹", "3P", "4P")):
        points.append("清楚だけで終わらない。複数や潮吹きの方向もある")
    while len(points) < 3:
        points.append("公式のサンプルで、顔・胸・シチュを確認してからでいい")
    lis = "\n  ".join(f"<li>{html.escape(p)}</li>" for p in points[:5])
    who = "今夜これで抜けるか、シチュと身体で選びたい人。"
    if any(k in blob for k in ("NTR", "寝取")):
        who = "寝取られと生が欲しい夜。純愛はいらない人。"
    elif any(k in blob for k in ("デビュー", "Debut")):
        who = "新人の初々しさで抜きたい人。顔と胸を確認しに行く用。"
    elif "VR" in blob:
        who = "VRで目の前の女に密着して抜きたい人。"
    return f"""<h2>In a nutshell</h2>
<p>{hook}</p>
<h2>Highlights</h2>
<ul>
  {lis}
</ul>
<h2>Who it's for</h2>
<p>{html.escape(who)}</p>
<h2>One caveat</h2>
<p>タイトルが盛ってることもある。サンプルの肌と声を見てからでいい。</p>
<h2>Wrap-up</h2>
<p>抜けるシチュかどうかだけ見て、公式へ。</p>
"""


def _fallback_x_post_text(item: FanzaItem, *, article_url: str) -> str:
    """Gemini 失敗時のフック型 X 投稿文。"""
    short_title = _sanitize_for_x(item.title)[:28]
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


def _plain_text_from_html(raw_html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw_html or "")
    return re.sub(r"\s+", " ", text).strip()


def _summary_from_signals(item: FanzaItem, blob: str) -> str:
    """カード用の短い編集要約。タイトルやジャンル列は出さない。"""
    hay = f"{item.title}\n{item.description}\n{blob}"
    if any(k in hay for k in ("NTR", "寝取", "内緒", "禁断", "好きピ")):
        return "彼氏の前で生のNTR。見られながら中出しまでいく背徳もの。"
    if any(k in hay for k in ("デビュー", "Debut", "専属")):
        if any(k in hay for k in ("Hカップ", "巨乳", "爆乳", "グラマー")):
            return "Hカップ新人の初撮り。清楚顔が乱れるのを観る一本。"
        return "専属新人の初撮り。顔と身体の初物感で抜く一本。"
    if "VR" in hay or "vr" in hay.lower():
        if any(k in hay for k in ("ベスト", "18時間", "8時間", "総集編")):
            return "8K VRベスト。目の前で密着して、場面を選んで抜く用。"
        return "目の前で密着するVR。吐息と胸の距離で抜きたい夜向け。"
    if any(k in hay for k in ("ベスト", "総集編")):
        return "長回しで選んで観られるベスト。気分で場面を変えたい夜向け。"
    return "公式の肌感と声を見てから選びたい一本。"


def extract_card_summary(article_html_body: str, item: FanzaItem) -> str:
    """
    カード用要約。

    HTMLの切り出しは使わず、作品信号から短い一文を作る。
    タイトル全文・ジャンル列・空欄を出さない。
    """
    blob = _plain_text_from_html(article_html_body)
    return _summary_from_signals(item, blob)


def generate_article_html(client: genai.Client, item: FanzaItem) -> str:
    """
    GitHub Pages 用の HTML 本文（fragment）を生成する。

    返却値は section 要素を中心とした HTML 断片（ページテンプレートに埋め込む）。
    """
    context = _build_item_context(item, for_x=False)
    system_prompt = _load_prompt_file("article.txt")
    user_prompt = (
        "エロ動画レビューとして、何が抜けるかを具体的に書いて。"
        "タイトル全文は繰り返さない。価格を見どころに入れるな。"
        "見出しは指定どおり。\n\n"
        f"{context}"
    )
    logger.info("Gemini: 記事 HTML 生成を開始 content_id=%s model=%s", item.content_id, MODEL_NAME)
    content = _generate_text(
        client,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=0.85,
        adult_ok=True,
    )
    html_body = _strip_code_fence(content)
    if not html_body:
        logger.warning(
            "Gemini: 記事 HTML が空のためリトライ content_id=%s",
            item.content_id,
        )
        retry_prompt = (
            "次の作品を観た人の口調で、エロ寄りの HTML レビューにして。"
            "見出しは In a nutshell / Highlights / Who it's for / One caveat / Wrap-up。"
            "未成年連想は禁止。タイトル全文は繰り返さない。\n\n"
            f"{context}"
        )
        content = _generate_text(
            client,
            system_prompt=system_prompt,
            user_prompt=retry_prompt,
            temperature=0.5,
            adult_ok=True,
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
    context = _build_item_context(item, for_x=True)
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