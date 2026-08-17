import json
from pathlib import Path

import pytest

from affiliate_product import (
    affiliate_comment_text,
    classify_product_type,
    choose_candidate,
    comment_fingerprint,
    extract_video_id,
    load_history_dedup,
    normalize_product,
    parse_price,
)
from worker import (
    affiliate_comment_already_done,
    affiliate_comment_matches,
    attach_product_context,
    build_image_prompt,
    build_shorts_mode_prompt,
    build_visual_chunks,
    flow_for,
)
from app import parse_product_json


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


def test_normalize_product_accepts_real_amazon_product_and_sitestripe_link():
    product = normalize_product(valid_product())
    assert product["asin"] == "B0TEST1234"
    assert product["affiliate_url"] == "https://amzn.to/4abcDEF"


def test_normalize_product_rejects_plain_non_affiliate_url():
    raw = valid_product()
    raw["affiliate_url"] = "https://www.amazon.com/dp/B0TEST1234"
    with pytest.raises(ValueError, match="SiteStripe"):
        normalize_product(raw)


def test_affiliate_comment_is_exactly_product_name_and_link():
    text = affiliate_comment_text(valid_product())
    assert text == "Premium 4K Monitor\nhttps://amzn.to/4abcDEF"


def test_affiliate_comment_shortens_verbose_amazon_title():
    product = valid_product()
    product["name"] = "Dell 32 Monitor S3225QS, 4K UHD VA | 120Hz, Eye Comfort"
    assert affiliate_comment_text(product) == "Dell 32 Monitor S3225QS\nhttps://amzn.to/4abcDEF"


def test_comment_verifier_accepts_the_short_name_that_is_actually_posted():
    product = valid_product()
    product["name"] = "Dell 32 Monitor S3225QS, 4K UHD VA | 120Hz, Eye Comfort"
    posted = "Dell 32 Monitor S3225QS\nhttps://amzn.to/4abcDEF"
    assert affiliate_comment_matches(posted, product) is True


def test_comment_fingerprint_is_stable_per_video_and_link():
    a = comment_fingerprint("abcdefghijk", "https://amzn.to/4abcDEF")
    b = comment_fingerprint("abcdefghijk", "https://amzn.to/4abcDEF")
    c = comment_fingerprint("abcdefghijk", "https://amzn.to/different")
    assert a == b
    assert a != c
    assert len(a) == 64


def test_product_job_adds_comment_stage_only_after_upload():
    assert flow_for({"target_type": "shorts", "product": valid_product()}) == [
        "script", "audio", "video", "upload", "comment"
    ]
    assert flow_for({"target_type": "shorts"}) == ["script", "audio", "video", "upload"]
    assert flow_for({"target_type": "youtube", "product": valid_product()}) == [
        "script", "audio", "video", "upload"
    ]


def test_attach_product_context_preserves_original_news_ending():
    data = {
        "TOPIC": "AI伺服器需求升溫",
        "CONTEXT1": "一",
        "CONTEXT2": "二",
        "CONTEXT3": "最後觀察供應鏈訂單是否落地。",
        "PRODUCT_CONTEXT": "如果你平常同時追多個市場，這台螢幕能讓資訊整理更順手；有需要可以再看看。",
    }
    result = attach_product_context(data)
    assert result["NEWS_CONTEXT3"] == "最後觀察供應鏈訂單是否落地。"
    assert result["CONTEXT3"].startswith(result["NEWS_CONTEXT3"])
    assert result["CONTEXT3"].endswith(result["PRODUCT_CONTEXT"])
    assert result["PRODUCT_CONTEXT"].endswith("請參考留言處商品連結")
    assert "有需要" not in result["PRODUCT_CONTEXT"]


def test_build_visual_chunks_keeps_six_news_cards_and_appends_product_card():
    data = {
        "TOPIC": "AI伺服器需求升溫",
        "CONTEXT1": "甲。乙。",
        "CONTEXT2": "丙。丁。",
        "CONTEXT3": "新聞結尾。風險提醒。商品段不應混入新聞卡。",
        "NEWS_CONTEXT3": "新聞結尾。風險提醒。",
        "PRODUCT_CONTEXT": "自然帶到商品推薦。",
    }
    chunks = build_visual_chunks(data, valid_product())
    assert len(chunks) == 7
    assert [c["kind"] for c in chunks[:6]] == ["news"] * 6
    assert chunks[-1]["kind"] == "product"
    assert chunks[-1]["product_name"] == "Premium 4K Monitor"
    assert chunks[-1]["text"] == "自然帶到商品推薦。"
    assert all("商品段不應混入新聞卡" not in c["text"] for c in chunks[:6])


def test_shorts_prompt_requires_short_natural_product_transition():
    prompt = build_shorts_mode_prompt("AI資料中心新聞", valid_product())
    assert '"PRODUCT_CONTEXT"' in prompt
    assert "自然銜接" in prompt
    assert "7至10秒" in prompt
    assert "Premium 4K Monitor" in prompt
    assert "適合需要同時追蹤多個市場資訊的觀眾" in prompt
    assert "請參考留言處商品連結" in prompt
    assert "不得在這句後面加任何文字" in prompt


def test_parse_product_json_validates_before_saving_job_state():
    raw = json.dumps(valid_product(), ensure_ascii=False)
    assert parse_product_json(raw)["asin"] == "B0TEST1234"
    assert parse_product_json("") is None
    with pytest.raises(ValueError, match="SiteStripe"):
        bad = valid_product()
        bad["affiliate_url"] = bad["amazon_url"]
        parse_product_json(json.dumps(bad))


def test_product_image_prompt_is_a_dedicated_non_fabricating_marketing_visual():
    chunk = build_visual_chunks(
        {
            "CONTEXT1": "甲。乙。", "CONTEXT2": "丙。丁。",
            "CONTEXT3": "戊。己。", "NEWS_CONTEXT3": "戊。己。",
            "PRODUCT_CONTEXT": "自然帶到商品推薦。",
        },
        valid_product(),
    )[-1]
    prompt = build_image_prompt("AI伺服器需求升溫", chunk, 7, "")
    assert "Amazon 商品推薦片尾" in prompt
    assert "Premium 4K Monitor" in prompt
    assert "適合需要同時追蹤多個市場資訊的觀眾" in prompt
    assert "不得捏造" in prompt
    assert "價格、折扣、評價、配件" in prompt
    assert "股市/科技/國際金融視覺元素" not in prompt


def test_video_id_parser_supports_shorts_watch_and_short_urls():
    assert extract_video_id("https://youtube.com/shorts/abcdefghijk") == "abcdefghijk"
    assert extract_video_id("https://www.youtube.com/watch?v=abcdefghijk") == "abcdefghijk"
    assert extract_video_id("https://youtu.be/abcdefghijk") == "abcdefghijk"
    assert extract_video_id("https://example.com/nope") == ""


def test_verified_comment_record_blocks_duplicate_retry():
    product = valid_product()
    fingerprint = comment_fingerprint("abcdefghijk", product["affiliate_url"])
    state = {"affiliate_comment": {"fingerprint": fingerprint, "verified": True}}
    assert affiliate_comment_already_done(state, "abcdefghijk", product) is True
    state["affiliate_comment"]["verified"] = False
    assert affiliate_comment_already_done(state, "abcdefghijk", product) is False


def test_candidate_selector_requires_mid_high_price_popularity_and_dedup():
    candidates = [
        {"asin": "B000000001", "name": "Cheap", "price": "$49.99", "bought": "10K+ bought in past month", "badge": "Best Seller"},
        {"asin": "B000000002", "name": "Popular Monitor", "price": "$399.99", "bought": "2K+ bought in past month", "badge": "Best Seller"},
        {"asin": "B000000003", "name": "Expensive Unknown", "price": "$999.99", "bought": "", "badge": ""},
        {"asin": "B000000004", "name": "Good Alternative", "price": "$299.99", "bought": "500+ bought in past month", "badge": ""},
    ]
    assert parse_price("$1,299.99") == 1299.99
    picked = choose_candidate(candidates, used_asins={"B000000002"}, min_price=150, max_price=2500)
    assert picked["asin"] == "B000000004"
    assert "500+ bought" in picked["popularity_evidence"]
    with pytest.raises(ValueError, match="popular unused"):
        choose_candidate(candidates, used_asins={"B000000002", "B000000004"}, min_price=150, max_price=2500)


def test_product_type_dedup_blocks_different_asin_in_same_broad_category(tmp_path):
    history = tmp_path / "history.jsonl"
    history.write_text(
        json.dumps({
            "asin": "B000000001",
            "product": "Sceptre 34 inch business monitor",
            "product_type": "business_monitor_32_4k",
        }) + "\n",
        encoding="utf-8",
    )
    used_asins, used_types = load_history_dedup(history)
    assert used_asins == {"B000000001"}
    assert classify_product_type("Dell 32-inch 4K display monitor") == "monitor"
    assert used_types == {"monitor"}
    with pytest.raises(ValueError, match="product type already used"):
        choose_candidate(
            [{"asin": "B000000099", "name": "Dell 32 Monitor", "price": "$299.99", "bought": "2K+ bought in past month", "badge": ""}],
            used_asins=used_asins,
            used_product_types=used_types,
            product_type="monitor",
        )
