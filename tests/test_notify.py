from unittest import mock

from signal_engine.edge import Recommendation
from signal_engine.notify import format_recommendation, make_topic, send


def test_topic_is_long_and_unique():
    a, b = make_topic(), make_topic()
    assert a != b and len(a) > 20


def test_dry_run_does_not_touch_network(capsys):
    with mock.patch("urllib.request.urlopen") as u:
        assert send("t", "hello", dry_run=True)
        u.assert_not_called()
    assert "hello" in capsys.readouterr().out


def test_send_posts_to_topic():
    resp = mock.MagicMock(status=200)
    resp.__enter__.return_value = resp
    with mock.patch("urllib.request.urlopen", return_value=resp) as u:
        assert send("mytopic", "body", title="T", priority=4)
    req = u.call_args[0][0]
    assert req.full_url.endswith("/mytopic") and req.data == b"body"
    assert req.get_header("Priority") == "4"


def test_message_has_dollar_amount_and_price_cap():
    rec = Recommendation("YES", 0.14, 24, 0.23, 0.068, 3.84)
    msg = format_recommendation(rec, "BTC at or above $64,800", "12:00 PM ET")
    assert "BUY YES" in msg and "$3.84" in msg and "16c" in msg
