from services.llm import validate_analysis

def test_validates_clip_count_and_timestamps():
    data = {
        "main_reels": [{"start": 0, "end": 40} for _ in range(3)],
        "short_reels": [{"start": 45, "end": 65} for _ in range(2)],
        "encouragement_posts": [{}, {}]
    }
    result = validate_analysis(data, 100)
    assert len(result["main_reels"]) == 3
    assert len(result["short_reels"]) == 2
