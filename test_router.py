from server.models import ModalityMode, RouteRequest, SearchProfile
from server.router import QueryRouter


def test_object_query_fast_is_visual_only():
    route = QueryRouter().route(
        RouteRequest(
            query="người mặc áo đỏ đứng cạnh ô tô",
            profile=SearchProfile.fast,
        )
    )
    assert route.visual.enabled is True
    assert route.ocr.enabled is False
    assert route.asr.enabled is False


def test_auto_runs_ocr_parallel_without_claiming_high_score():
    route = QueryRouter().route(
        RouteRequest(
            query="người mặc áo đỏ đứng cạnh ô tô",
            profile=SearchProfile.auto,
        )
    )
    assert route.ocr.enabled is True
    assert route.ocr.execution_state == "auto_parallel"
    assert route.ocr.routing_score < 0.40


def test_brand_query_enables_ocr():
    route = QueryRouter().route(
        RouteRequest(
            query="người đánh trống Yamaha có logo YAMAHA",
            profile=SearchProfile.auto,
        )
    )
    assert route.ocr.enabled is True
    assert "yamaha" in route.ocr.anchors
    assert route.ocr.routing_score >= 0.70


def test_printed_phrase_query_is_split_for_visual_and_ocr():
    route = QueryRouter().route(
        RouteRequest(
            query="túi vải liên kết cùng phát triển",
            profile=SearchProfile.auto,
        )
    )
    assert route.visual_query == "túi vải"
    assert "liên kết cùng phát triển" in route.ocr.anchors
    assert route.ocr.routing_score >= 0.80
    assert route.ocr.enabled is True
    assert route.asr.enabled is False


def test_phat_trien_does_not_match_hat():
    route = QueryRouter().route(
        RouteRequest(
            query="túi vải liên kết cùng phát triển",
            profile=SearchProfile.auto,
        )
    )
    assert route.asr.routing_score == 0.05
    assert route.asr.enabled is False
    assert "hát" not in route.asr.reason


def test_speech_query_enables_asr():
    route = QueryRouter().route(
        RouteRequest(
            query="người đàn ông nói về thời tiết",
            profile=SearchProfile.auto,
        )
    )
    assert route.asr.enabled is True
    assert route.asr.execution_state == "auto_on"


def test_manual_override_wins_and_state_is_explicit():
    router = QueryRouter()
    forced_on = router.route(
        RouteRequest(
            query="người mặc áo đỏ",
            profile=SearchProfile.fast,
            ocr=ModalityMode.on,
        )
    )
    forced_off = router.route(
        RouteRequest(
            query="biển số 59A-123.45",
            profile=SearchProfile.accurate,
            ocr=ModalityMode.off,
        )
    )
    assert forced_on.ocr.enabled is True
    assert forced_on.ocr.execution_state == "forced_on"
    assert forced_off.ocr.enabled is False
    assert forced_off.ocr.execution_state == "forced_off"
