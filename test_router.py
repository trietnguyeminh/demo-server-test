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


def test_brand_query_enables_ocr():
    route = QueryRouter().route(
        RouteRequest(
            query="người đánh trống Yamaha có logo YAMAHA",
            profile=SearchProfile.auto,
        )
    )
    assert route.ocr.enabled is True
    assert "yamaha" in route.ocr.anchors


def test_speech_query_enables_asr():
    route = QueryRouter().route(
        RouteRequest(
            query="người đàn ông nói về thời tiết",
            profile=SearchProfile.auto,
        )
    )
    assert route.asr.enabled is True


def test_manual_override_wins():
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
    assert forced_off.ocr.enabled is False
