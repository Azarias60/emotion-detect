"""无需模型权重、LLM 或硬件的检测业务与 HTTP 回归测试。"""
import asyncio
import os
from pathlib import Path
import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import analysis_pipeline
from services.detection_service import DetectionService, ModelUnavailableError


@pytest.fixture
def service():
    predictor = Mock(emotion_log=[{"emotion": "happy"}, {"emotion": "happy"}, {"emotion": "sad"}])
    device = Mock(is_recording=False)
    device.has_heart_rate_data.return_value = False
    device.has_recorded_video.return_value = False
    device.get_recorded_frames.return_value = []
    device.get_temperature_summary.return_value = {"avg_object_temp": 36.5}
    return DetectionService(
        models=SimpleNamespace(predictor=predictor, voice_model=None), device=device,
        analyze_fatigue=Mock(return_value={"fatigue_score": 12}),
        analyze_heart_rate=Mock(return_value={"avg_hr": 70, "blood_pressure": {"systolic": 110}, "snr_db": 8}),
        transcribe=AsyncMock(return_value=[{"text": "你好", "emotion": "neutral"}]),
        emotion_report=Mock(return_value="情绪报告"), emotion_counts=Mock(return_value={"happy": 2}),
        health_report=Mock(return_value="健康报告"), fatigue_advice=Mock(return_value={"level": "低"}),
    )


def test_hardware_heart_rate_keeps_video_pressure(service):
    service.device.has_heart_rate_data.return_value = True
    service.device.get_heart_rate_summary.return_value = {
        "avg_hr": 82, "latest_hr": 85, "hr_list": [80, 84], "hrv_indices": {"SDNN": 20},
    }
    result = service.heart_rate("video.mp4")
    assert result == {
        "avg_hr": 82, "realtime_hr": 85, "hr_list": [80, 84], "hrv_indices": {"SDNN": 20},
        "blood_pressure": {"systolic": 110}, "signal_quality": "mcu_hardware_sensor", "snr_db": 8,
    }
    service.analyze_heart_rate.reset_mock()
    result = service.heart_rate(None)
    assert result["blood_pressure"] == {"systolic": None, "diastolic": None}
    service.analyze_heart_rate.assert_not_called()


def test_video_heart_rate_and_missing_data(service):
    assert service.heart_rate("video.mp4")["avg_hr"] == 70
    assert service.heart_rate(None) == {}


def test_radar_bpm_preserves_video_hrv(service):
    service.device.has_heart_rate_data.return_value = True
    service.device.get_heart_rate_summary.return_value = {
        "avg_hr": 72, "latest_hr": 72, "hr_list": [72],
        "hrv_indices": {"SDNN": None}, "hrv_status": "unavailable_no_rr_intervals",
    }
    service.analyze_heart_rate.return_value = {"hrv_indices": {"SDNN": 18}}
    assert service.heart_rate("video.mp4")["hrv_indices"] == {"SDNN": 18}
    assert service.heart_rate(None)["hrv_indices"] == {"SDNN": None}


def test_emotion_result_and_report_input(service, tmp_path):
    video = tmp_path / "video.mp4"
    video.touch()
    result = asyncio.run(service.emotion(str(tmp_path), str(video)))
    assert result["face_emotion_stats"] == {"happy": 2, "sad": 1}
    assert result["total_frames"] == 3
    assert result["result"] == "情绪报告"
    assert result["emotion_word_count"] == {"happy": 2}
    report_input = service.emotion_report.call_args.args[0]
    assert set(report_input) == {"face_emotion_stats", "total_frames", "voice_segments", "physiological", "heart_rate", "temperature"}
    service.models.predictor.start_capture.assert_called_once_with(video_file=str(video), interval=10)
    service.transcribe.assert_awaited_once_with(str(tmp_path), voice_model=None, device=service.device)


def test_emotion_analysis_keeps_event_loop_available_for_hardware_polling(service, tmp_path):
    video = tmp_path / "video.mp4"
    video.touch()

    async def run():
        loop = asyncio.get_running_loop()
        checked = []

        def check_stage(name, result):
            def work(*args, **kwargs):
                # 模型等待时，事件循环仍必须能处理硬件轮询等回调。
                responded = threading.Event()
                loop.call_soon_threadsafe(responded.set)
                assert responded.wait(1), f"{name} blocked the event loop"
                checked.append(name)
                return result
            return work

        stages = {
            "face": service.models.predictor.start_capture,
            "fatigue": service.analyze_fatigue,
            "heart_rate": service.analyze_heart_rate,
            "report": service.emotion_report,
            "counts": service.emotion_counts,
        }
        for name, operation in stages.items():
            operation.side_effect = check_stage(name, operation.return_value)
        await service.emotion(str(tmp_path), str(video))
        assert checked == list(stages)

    asyncio.run(run())


def test_missing_emotion_model_does_not_run_other_analysis(service):
    service.models.predictor = None
    with pytest.raises(ModelUnavailableError):
        asyncio.run(service.emotion("unused", None))
    service.analyze_fatigue.assert_not_called()
    service.transcribe.assert_not_awaited()


def test_health_and_fatigue_report_contracts(service):
    tongue, questions, answers = {"color": "red"}, ["睡眠如何"], ["良好"]
    result = asyncio.run(service.health("unused", None, tongue, questions, answers))
    assert result["physiological"] == {}
    assert result["voice_segments"][0]["text"] == "你好"
    assert result["result"] == "健康报告"
    service.health_report.assert_called_once_with(tongue, questions, answers, {}, {}, {"avg_object_temp": 36.5}, voice_segments=[{"text": "你好", "emotion": "neutral"}])
    result = service.fatigue("video.mp4")
    assert result["advice"] == {"level": "低"}
    service.fatigue_advice.assert_called_once_with({"fatigue_score": 12})


def session(lock, device):
    return analysis_pipeline.detection_session(
        lock, "test_session_", "mcu.mp4", "busy", "failed", device=device
    )


def test_busy_session_never_changes_device(service):
    lock = threading.Lock()
    lock.acquire()
    service.device.is_recording = True

    async def run():
        async with session(lock, service.device):
            pytest.fail("busy session must not run")

    try:
        with pytest.raises(HTTPException) as caught:
            asyncio.run(run())
        assert caught.value.status_code == 409
        assert lock.locked()
        service.device.stop_recording.assert_not_called()
        service.device.get_recorded_frames.assert_not_called()
    finally:
        lock.release()


@pytest.mark.parametrize("error,status", [
    (None, None), (ValueError("bad"), 500),
    (ModelUnavailableError("missing"), 503), (HTTPException(422, "invalid"), 422),
    (asyncio.CancelledError(), None),
])
def test_session_always_cleans_up(service, error, status):
    lock = threading.Lock()
    paths = []

    async def run():
        async with session(lock, service.device) as (directory, video):
            paths.append(directory)
            assert os.path.isdir(directory)
            assert video is None
            if error is not None:
                raise error

    if error is None:
        asyncio.run(run())
    elif isinstance(error, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(run())
    else:
        with pytest.raises(HTTPException) as caught:
            asyncio.run(run())
        assert caught.value.status_code == status
    assert not lock.locked()
    assert paths and not os.path.exists(paths[0])


def test_session_setup_failure_releases_lock(service, monkeypatch):
    lock = threading.Lock()
    monkeypatch.setattr(analysis_pipeline.tempfile, "mkdtemp", Mock(side_effect=OSError("disk full")))

    async def run():
        async with session(lock, service.device):
            pytest.fail("setup failure must not yield")

    with pytest.raises(HTTPException) as caught:
        asyncio.run(run())
    assert caught.value.status_code == 500
    assert not lock.locked()


def test_session_prefers_device_video(service, monkeypatch):
    monkeypatch.setattr(analysis_pipeline, "write_recorded_video", Mock(return_value="mcu.mp4"))
    service.device.is_recording = True

    async def run():
        async with session(threading.Lock(), service.device) as (_, video):
            assert video == "mcu.mp4"

    asyncio.run(run())
    service.device.stop_recording.assert_called_once()


def test_http_contracts_without_models_or_hardware(service):
    from application import create_app
    from services.dependencies import get_detection_service, get_tongue_analyzer, get_device

    @asynccontextmanager
    async def no_hardware(app):
        yield

    app = create_app(lifespan=no_hardware)
    app.dependency_overrides[get_detection_service] = lambda: service
    import numpy as np
    service.device.get_latest_frame.return_value = np.zeros((32, 32, 3), dtype=np.uint8)
    app.dependency_overrides[get_device] = lambda: service.device
    app.dependency_overrides[get_tongue_analyzer] = lambda: lambda path: {"quality": {"passed": True}}
    with TestClient(app) as client:
        for path in ("/emotion/detect", "/report/detect"):
            response = client.post(path)
            assert response.status_code == 200
            assert response.json()["data"]["result"] == "情绪报告"
            assert set(response.json()) == {"code", "msg", "data"}
        assert client.post("/health/detect", data={"tongue_result": "invalid"}).status_code == 400
        response = client.post("/health/detect", data={"answers": '["良好"]'})
        assert response.status_code == 200
        assert response.json()["data"]["answers"] == ["良好"]
        assert client.post("/fatigue/detect").status_code == 409
        service.device.has_recorded_video.return_value = True
        assert client.post("/fatigue/detect").json()["data"]["advice"] == {"level": "低"}
        service.models.predictor = None
        assert client.post("/emotion/detect").status_code == 503
        response = client.post("/health/tongue/capture")
        assert response.status_code == 200
        assert response.json()["data"]["quality"]["passed"] is True
        assert client.post("/health/tongue/detect", files={"image": ("bad.txt", b"fake")}).status_code == 404
        schema = client.get("/openapi.json").json()
        assert {"/emotion/detect", "/report/detect", "/health/detect", "/fatigue/detect", "/hardware/mode"} <= set(schema["paths"])


def test_importing_application_does_not_load_model_frameworks():
    code = "import sys; from application import create_app; create_app(); assert not {'torch', 'tensorflow', 'dlib', 'funasr'} & set(sys.modules)"
    subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], check=True, capture_output=True)


def test_model_failure_is_isolated_and_shutdown_always_stops_device(monkeypatch):
    from core import lifecycle

    models = SimpleNamespace(predictor=None, voice_model=None, tongue_predictor=None)
    device = Mock()
    voice, tongue = object(), object()
    monkeypatch.setattr(lifecycle, "models", models)
    monkeypatch.setattr(lifecycle, "mcu_client", device)
    database = Mock()
    monkeypatch.setattr(lifecycle, "init_database", database)
    monkeypatch.setitem(sys.modules, "face_detection.face_detection_logic", SimpleNamespace(
        create_predictor=Mock(side_effect=ImportError("optional model dependency unavailable"))
    ))
    monkeypatch.setitem(sys.modules, "funasr", SimpleNamespace(AutoModel=Mock(return_value=voice)))
    monkeypatch.setitem(sys.modules, "tongue_diagnosis.efficient_net.predictor", SimpleNamespace(
        TonguePredictor=Mock(return_value=tongue)
    ))

    async def run():
        async with lifecycle.lifespan(None):
            assert models.predictor is None
            assert models.voice_model is voice
            assert models.tongue_predictor is tongue
            database.assert_called_once()
            device.start.assert_called_once()
            raise RuntimeError("application interrupted")

    with pytest.raises(RuntimeError, match="application interrupted"):
        asyncio.run(run())
    device.stop.assert_called_once()


def test_speech_uses_injected_model_and_hardware_audio(service, tmp_path):
    from services.speech_service import transcribe_recorded_audio

    audio_path = tmp_path / "mcu.wav"
    audio_path.write_bytes(b"0" * 2048)
    service.device.save_recorded_audio.return_value = {"asr_wav": str(audio_path)}
    model = Mock()
    model.generate.return_value = [{"text": "<|zh|><|NEUTRAL|>你好。今天很好！再见"}]
    result = asyncio.run(transcribe_recorded_audio(
        str(tmp_path), voice_model=model, device=service.device
    ))
    assert result == [{"text": "你好。 今天很好！", "emotion": "neutral"}, {"text": "再见", "emotion": "neutral"}]
    model.generate.assert_called_once_with(input=str(audio_path), batch_size_s=300)
    model.generate.side_effect = RuntimeError("inference unavailable")
    assert asyncio.run(transcribe_recorded_audio(str(tmp_path), voice_model=model, device=service.device)) == []


def test_missing_voice_model_does_not_consume_audio(service):
    from services.speech_service import transcribe_recorded_audio

    assert asyncio.run(transcribe_recorded_audio("unused", voice_model=None, device=service.device)) == []
    service.device.save_recorded_audio.assert_not_called()


def test_hardware_audio_never_falls_back_to_upload(service, tmp_path):
    from services.media_service import resolve_session_audio

    service.device.save_recorded_audio.return_value = {}
    assert resolve_session_audio(str(tmp_path), device=service.device) is None


@pytest.mark.parametrize("path", ["/emotion/detect", "/report/detect", "/health/detect", "/fatigue/detect"])
def test_all_detection_endpoints_reject_non_hardware_inputs(service, path):
    from application import create_app
    from services.dependencies import get_detection_service

    app = create_app()
    app.dependency_overrides[get_detection_service] = lambda: service
    client = TestClient(app)
    for source in ("browser", "auto"):
        assert client.post(path, data={"capture_source": source}).status_code == 422
    for field in ("audio", "video"):
        assert client.post(path, files={field: ("upload.webm", b"data")}).status_code == 400
    service.transcribe.assert_not_awaited()
    service.device.stop_recording.assert_not_called()


def test_source_and_consultation_shape_are_validated_before_analysis(service):
    from application import create_app
    from services.dependencies import get_detection_service

    app = create_app()
    app.dependency_overrides[get_detection_service] = lambda: service
    # 不进入 TestClient lifespan，不启动硬件或模型。
    client = TestClient(app)
    assert client.post("/emotion/detect", data={"capture_source": "invalid"}).status_code == 422
    assert client.post("/health/detect", data={"questions": '{"invalid":"object"}'}).status_code == 400
    assert client.post("/fatigue/detect", data={"capture_source": "browser"}).status_code == 422
    assert client.post(
        "/fatigue/detect", data={"capture_source": "hardware"},
        files={"video": ("browser.webm", b"must not satisfy hardware capture")},
    ).status_code == 400
    service.device.stop_recording.assert_not_called()
    service.analyze_fatigue.assert_not_called()


def test_hardware_http_contracts_with_an_injected_device():
    from fastapi import FastAPI
    from routers.hardware import router
    from services.dependencies import get_hardware_service
    from services.hardware_service import HardwareService

    device = Mock(is_connected=True, is_recording=False)
    device.get_transport_info.return_value = {"type": "wifi"}
    device.get_recorded_frames.return_value = [1, 2]
    device.get_audio_summary.return_value = {"chunks_count": 3}
    device.get_heart_rate_summary.return_value = {"hr_list": [80]}
    device.get_temperature_summary.return_value = {"ambient_temps": [20, 21]}
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_hardware_service] = lambda: HardwareService(device)
    client = TestClient(app)
    assert client.post("/hardware/start", json={"record_duration": 30}).status_code == 200
    device.start_recording.assert_called_once_with(duration=30, capture_video=True)
    assert client.post("/serial/stop").json()["data"] == {
        "recorded_frames": 2, "audio_chunks": 3, "hr_count": 1, "temp_count": 2,
    }
    assert client.post("/hardware/start", json={"record_duration": -1}).status_code == 422
    assert client.post("/hardware/mode", json={"mode": "wifi", "tcp_port": 99999}).status_code == 422
    assert client.post("/hardware/mode", json={"mode": " bluetooth "}).status_code == 400
    assert client.post("/hardware/mode", json={"mode": " WIFI ", "tcp_port": 8888}).status_code == 200
    device.switch_to_wifi.assert_called_once_with(host=None, tcp_port=8888, udp_port=None)
