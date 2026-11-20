"""Public error boundaries retained while redundant internal guards are removed."""
import json

import httpx
import openai
import pytest

from server import settings
from server.capture.webrtc.protocol import JpegRunProtocolError, parse_jpeg_run_marker
from server.analysis.health.reporting.text import HealthReportTextPort
from server.analysis.emotion.reporting.types import EmotionReportFacts
from server.analysis.health.reporting.types import HealthReportFacts
from server.detection_errors import DetectionUnavailableError
from server.analysis.emotion.reporting.text import OpenAIEmotionReportTextReader
from server.analysis.health.reporting.text import OpenAIHealthReportTextReader


def _report_call(kind, error, monkeypatch):
    def factory():
        raise error
    emotion_facts = EmotionReportFacts(
        expression=None, behavior=None, fatigue=None,
        advice={"level": "数据不足", "text": "无数据"}, answers=(), speech=None,
        physiology=None,
    )
    if kind == 'emotion':
        return OpenAIEmotionReportTextReader(factory).result(emotion_facts)
    monkeypatch.setitem(settings.AI_CONFIG, 'api_key', 'test-not-sent')
    return HealthReportTextPort(OpenAIHealthReportTextReader(factory)).result(HealthReportFacts(
        tongue_quality=None, tongue_recognition=None, constitution=None,
        constitution_report=None, answers=(),
        behavior=None, fatigue=None,
        advice={"level": "数据不足", "text": "无数据"}, speech=None,
        physiology=None,
    ))


def _sdk_error(kind):
    request = httpx.Request('POST', 'https://example.invalid')
    if kind == 'connection':
        return openai.APIConnectionError(request=request)
    if kind == 'timeout':
        return openai.APITimeoutError(request=request)
    cls, status = {'rate_limit': (openai.RateLimitError, 429),
                   'server': (openai.InternalServerError, 500),
                   'auth': (openai.AuthenticationError, 401)}[kind]
    return cls('upstream unavailable', response=httpx.Response(status, request=request), body=None)


@pytest.mark.parametrize('port', ['emotion', 'health'])
@pytest.mark.parametrize('kind', ['rate_limit', 'server', 'auth', 'connection', 'timeout'])
def test_public_sdk_failures_become_typed_unavailability(port, kind, monkeypatch):
    error = _sdk_error(kind)
    from server.detection_errors import DetectionUpstreamError, DetectionTimeoutError
    expected = DetectionUnavailableError if kind == "rate_limit" else DetectionTimeoutError if kind == "timeout" else DetectionUpstreamError
    with pytest.raises(expected) as caught:
        _report_call(port, error, monkeypatch)
    assert caught.value.__cause__ is error


@pytest.mark.parametrize('port', ['emotion', 'health'])
def test_programming_errors_are_not_network_errors_because_of_their_name(port, monkeypatch):
    class LocalAPIShapeError(Exception):
        pass
    error = LocalAPIShapeError('programming error')
    with pytest.raises(LocalAPIShapeError) as caught:
        _report_call(port, error, monkeypatch)
    assert caught.value is error


def test_lone_surrogate_remains_a_typed_protocol_error():
    # isinstance(str) does not prove encodability; Python permits isolated surrogates.
    message = json.dumps({'v': 1, 'type': 'jpeg_run', 'run': 1}) + '\ud800'
    with pytest.raises(JpegRunProtocolError) as caught:
        parse_jpeg_run_marker(message)
    assert isinstance(caught.value.__cause__, UnicodeEncodeError)
