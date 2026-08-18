import json

from PIL import Image

from run_podcast import _load_contexts
from worker import (
    ENGAGEMENT_CTA,
    attach_engagement_context,
    attach_product_context,
    build_shorts_mode_prompt,
    build_visual_chunks,
    find_font,
    make_engagement_card,
)


def news_data():
    return {
        "TOPIC": "AI伺服器需求升溫",
        "CONTEXT1": "開場。市場重點。",
        "CONTEXT2": "核心事件。產業影響。",
        "CONTEXT3": "最後觀察訂單是否落地。風險仍要留意。",
    }


def valid_product():
    return {
        "asin": "B0TEST1234",
        "name": "Premium 4K Monitor",
        "amazon_url": "https://www.amazon.com/dp/B0TEST1234",
        "affiliate_url": "https://amzn.to/4abcDEF",
        "price": "$399.99",
        "popularity_evidence": "2K+ bought in past month",
        "relevance_reason": "適合需要同時追蹤多個市場資訊的觀眾",
    }


def test_engagement_cta_is_appended_to_context3_for_voai_and_preserves_news_ending():
    original = news_data()
    result = attach_engagement_context(original)

    assert result["NEWS_CONTEXT3"] == original["CONTEXT3"]
    assert result["ENGAGEMENT_CONTEXT"] == ENGAGEMENT_CTA
    assert result["CONTEXT3"].endswith(ENGAGEMENT_CTA)
    assert result["CONTEXT3"].count(ENGAGEMENT_CTA) == 1
    assert all(term in ENGAGEMENT_CTA for term in ("訂閱", "按讚", "小鈴鐺", "分享"))


def test_engagement_context_attachment_is_idempotent():
    once = attach_engagement_context(news_data())
    twice = attach_engagement_context(once)
    assert twice["CONTEXT3"] == once["CONTEXT3"]
    assert twice["CONTEXT3"].count(ENGAGEMENT_CTA) == 1


def test_chatgpt_prompt_reserves_subscription_cta_for_deterministic_postprocessing():
    prompt = build_shorts_mode_prompt("測試市場新聞")
    assert "不要自行加入訂閱、按讚、小鈴鐺或分享 CTA" in prompt
    assert "系統會在最後固定追加" in prompt


def test_voai_loader_receives_engagement_cta_inside_third_context(tmp_path):
    data = attach_engagement_context(news_data())
    json_path = tmp_path / "podcast_output.json"
    json_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    contexts, topic = _load_contexts(json_path)
    assert topic == data["TOPIC"]
    assert list(contexts) == ["CONTEXT1", "CONTEXT2", "CONTEXT3"]
    assert contexts["CONTEXT3"].endswith(ENGAGEMENT_CTA)


def test_normal_shorts_have_six_news_cards_then_one_engagement_card():
    data = attach_engagement_context(news_data())
    chunks = build_visual_chunks(data)

    assert len(chunks) == 7
    assert [chunk["kind"] for chunk in chunks[:6]] == ["news"] * 6
    assert chunks[-1] == {
        "kind": "engagement",
        "context": "ENGAGEMENT_CONTEXT",
        "part": 1,
        "text": ENGAGEMENT_CTA,
        "index": 7,
    }
    assert all(ENGAGEMENT_CTA not in chunk["text"] for chunk in chunks[:6])


def test_affiliate_shorts_keep_product_before_final_engagement_card():
    data = news_data()
    data["PRODUCT_CONTEXT"] = "這台螢幕能協助整理市場資訊。請參考留言處商品連結"
    data = attach_product_context(data)
    data = attach_engagement_context(data)
    chunks = build_visual_chunks(data, valid_product())

    assert len(chunks) == 8
    assert chunks[-2]["kind"] == "product"
    assert chunks[-2]["text"].endswith("請參考留言處商品連結")
    assert chunks[-1]["kind"] == "engagement"
    assert data["CONTEXT3"].endswith(ENGAGEMENT_CTA)


def test_deterministic_engagement_card_is_vertical_and_written_without_chatgpt(tmp_path):
    font = find_font()
    assert font is not None
    assert any(marker in font.lower() for marker in ("msjh", "mingliu", "notosanscjk"))

    output = tmp_path / "engagement.jpg"
    make_engagement_card(output, ENGAGEMENT_CTA, 7, 7)

    assert output.exists()
    with Image.open(output) as image:
        assert image.size == (1080, 1920)
        assert image.mode == "RGB"
