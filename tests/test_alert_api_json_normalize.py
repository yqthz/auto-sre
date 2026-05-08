from app.api.alert import _normalize_json_object


def test_normalize_json_object_with_dict():
    payload = {"k": "v"}
    assert _normalize_json_object(payload) == payload


def test_normalize_json_object_with_json_string():
    payload = '{"analyzed_at":"2026-05-08T10:00:00Z","parse_error_count":2}'
    assert _normalize_json_object(payload) == {
        "analyzed_at": "2026-05-08T10:00:00Z",
        "parse_error_count": 2,
    }


def test_normalize_json_object_with_invalid_string():
    assert _normalize_json_object("not-json") is None


def test_normalize_json_object_with_non_object_json():
    assert _normalize_json_object("[1,2,3]") is None

