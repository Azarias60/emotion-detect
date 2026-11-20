"""模块装配；只创建、逐项注入并登记关闭，不参与业务判断。

``assemble`` 创建采集内核、各计算域服务与账户/归档支撑，把检测执行
环节（准备、问答、测量、模型）与调度接成一条执行链；``app.py`` 消费
装配结果驱动路由与生命周期。请求执行期间不从装配根或 app.state 重新
查找服务。
"""
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING
from collections.abc import Callable

from server.capture.rounds import CaptureRounds
from server.capture.service import CaptureKernel
from server.analysis.questions import QuestionFlow
from server.detection.service import AsyncioClock, create_detection_execution
from server.detection.flows import create_analyze, create_measure, create_prepare
from server.concurrency import TaskAdmission
from server.presentation.gateway.ports import GatewayPorts

from server.compute.expression.service import ExpressionVideoService
from server.compute.fatigue.service import FatigueObservationService
from server.compute.facephys.service import FacePhysService
from server.compute.fatigue.assembly import create_fatigue_observation_service
from server.compute.facephys.assembly import create_facephys_service
from server.compute.expression.assembly import create_expression_service
from server.compute.speech.service import SpeechService
from server.compute.tongue.service import TongueRecognitionService
from server.compute.tongue.assembly import create_tongue_recognition_service
from server.support.identity import assembly as identity_assembly
from server.support.identity.adapters.sqlite import init_database as init_identity_database
from server.support.reporting import assembly as reporting_assembly
from server.support.reporting.adapters.sqlite import init_database as init_reporting_database

if TYPE_CHECKING:
    from server.support.identity.http_dependencies import IdentityDependencies
    from server.support.reporting.service import ReportingService


@dataclass
class Assembly:
    """启动装配结果：各模块资源所有者与公开入口。

    ``app.py`` 用它构建路由并在 lifespan 中驱动 load/close；请求处理器
    只使用装配时注入的端口，不回读本对象。
    """

    kernel: CaptureKernel
    admission: TaskAdmission
    expression: ExpressionVideoService
    speech: SpeechService
    tongue: TongueRecognitionService
    behavior: FatigueObservationService
    facephys: FacePhysService
    identity: "IdentityDependencies"
    reporting: "ReportingService"
    init_identity_database: Callable[[], None]
    init_reporting_database: Callable[[], None]
    ports: GatewayPorts
    detection: object
    objects: dict
    model_config: object
    productions: dict
    recording_port: object
    contexts: object


def create_gateway_ports(kernel, admission, expression, behavior, speech):
    """Compose the observation Gateway's explicit capability Ports."""
    from server.analysis.fatigue.observation import BehaviorObservationService

    capture = kernel
    return GatewayPorts(
        admission=admission,
        capture=capture,
        expression=expression,
        behavior_observation_flow=BehaviorObservationService(capture=capture, behavior=behavior),
        speech=speech,
    )


def create_detection_execution_service(inputs):
    """接装检测执行链：准备、问答、采集轮次、测量与模型各自注入调度。"""
    from server.analysis.emotion.assembly import (
        create_emotion_analysis_flow_service,
        create_emotion_question_service,
    )
    from server.analysis.fatigue.reporting.flow import FatigueAnalysisFlowService
    from server.analysis.health.assembly import (
        create_health_analysis_flow_service,
        create_health_observation_service,
    )
    from server.compute.fatigue.service import (
        FatigueAdviceService,
        FatigueScoringService,
    )
    from server.compute.tongue.assembly import create_tongue_observation_service

    kernel, admission, expression = inputs.kernel, inputs.admission, inputs.expression
    behavior, speech, facephys = inputs.behavior, inputs.speech, inputs.facephys
    reports = inputs.reporting

    fatigue_scoring = FatigueScoringService()
    fatigue_advice = FatigueAdviceService()
    emotion_flow = create_emotion_analysis_flow_service(
        expression=expression, behavior=behavior,
        facephys_factory=facephys.create_reader, speech=speech,
        fatigue_scoring=fatigue_scoring, fatigue_advice=fatigue_advice,
    )
    health_flow = create_health_analysis_flow_service(
        behavior=behavior, facephys_factory=facephys.create_reader, speech=speech,
        fatigue_scoring=fatigue_scoring, fatigue_advice=fatigue_advice,
    )
    fatigue_flow = FatigueAnalysisFlowService(
        behavior=behavior, fatigue_scoring=fatigue_scoring, fatigue_advice=fatigue_advice,
        facephys_factory=facephys.create_reader,
    )

    rounds = CaptureRounds(kernel)
    questions = QuestionFlow(
        reports=reports, rounds=rounds, speech=speech, admission=admission,
    )
    prepare = create_prepare(
        reports=reports,
        tongue_observation=create_health_observation_service(
            capture=kernel, tongue=create_tongue_observation_service(inputs.tongue),
        ),
        emotion_questions=create_emotion_question_service(),
    )
    measure = create_measure(
        reports=reports, admission=admission,
        fatigue_flow=fatigue_flow, emotion_flow=emotion_flow, health_flow=health_flow,
    )
    analyze = create_analyze(
        reports=reports, emotion_flow=emotion_flow, health_flow=health_flow,
    )
    return create_detection_execution(
        reports=_ReportPort(reports), prepare=prepare, capture=rounds,
        questions=questions, measure=measure, analyze=analyze, clock=AsyncioClock(),
    )


class _DetectionInputs:
    """检测执行链的装配输入；字段与 Assembly 的相关资源一一对应。"""

    def __init__(self, *, kernel, admission, expression, speech, tongue, behavior, facephys, reporting):
        self.kernel = kernel
        self.admission = admission
        self.expression = expression
        self.speech = speech
        self.tongue = tongue
        self.behavior = behavior
        self.facephys = facephys
        self.reporting = reporting


class _ReportPort:
    """调度使用的报告端口：创建/记录/移除/存在观察都归报告能力。"""

    def __init__(self, reporting):
        self._reporting = reporting

    def create(self, owner, mode):
        return self._reporting.create_report(owner, mode)

    def record(self, owner, report_id, facts):
        self._reporting.update_report(owner, report_id, facts)

    def remove(self, owner, report_id):
        self._reporting.delete_report(owner, report_id)

    def observe_exists(self, owner, report_id, listener):
        def on_collection(rows):
            listener(any(row["id"] == report_id for row in rows))
        return self._reporting.observe(owner, on_collection)


def assemble(kernel=None, *, database_path=None) -> Assembly:
    """创建全部模块资源并组合 Gateway 端口；无启动副作用。

    采集内核被动等待设备 WHIP 信令；模型加载在应用 lifespan 中执行，
    关闭动作由 ``app.py`` 统一驱动。``database_path`` 是身份库与报告库
    共用的唯一数据库路径（报告表对用户表有外键，两侧必须同库同源）；
    缺省时回落到 settings.DATABASE_PATH。
    """
    from server import settings
    from server.capture.service import create_kernel

    kernel = kernel if kernel is not None else create_kernel()

    admission = TaskAdmission(capacity=1)
    expression = create_expression_service()
    from server.compute.speech.assembly import create_speech_service
    speech = create_speech_service()
    tongue = create_tongue_recognition_service()
    behavior = create_fatigue_observation_service()
    facephys = create_facephys_service()
    resolved_database_path = database_path or settings.DATABASE_PATH
    identity = identity_assembly.create_identity_dependencies(resolved_database_path)
    reporting = reporting_assembly.create_reporting_service(resolved_database_path)
    ports = create_gateway_ports(kernel, admission, expression, behavior, speech)
    detection = create_detection_execution_service(_DetectionInputs(
        kernel=kernel, admission=admission, expression=expression, speech=speech,
        tongue=tongue, behavior=behavior, facephys=facephys, reporting=reporting,
    ))
    objects, model_config, productions, recording_port, contexts = create_session_support(
        kernel, resolved_database_path)
    return Assembly(
        kernel=kernel, admission=admission, expression=expression, speech=speech,
        tongue=tongue, behavior=behavior, facephys=facephys, identity=identity,
        reporting=reporting,
        init_identity_database=partial(init_identity_database, resolved_database_path),
        init_reporting_database=partial(init_reporting_database, resolved_database_path),
        ports=ports, detection=detection, objects=objects,
        model_config=model_config, productions=productions,
        recording_port=recording_port, contexts=contexts,
    )


def create_session_support(kernel, database_path):
    """装配会话与对象支撑：独立对象、模型配置、制作与录音、会话上下文。

    制作模型与数字人引擎不在当前分支：提交入口以明确的不可用错误呈现，
    能力边界与 HTTP 面已就位，模型接入后替换对应端口即可。
    """
    from server.detection_errors import DetectionUnavailableError
    from server.presentation.production import AccountRecordingPort
    from server.presentation.conversation import AccountContexts
    from server.production.service import create_task
    from server.recording.audio import KernelAudioRecorder
    from server.recording.service import create_recording
    from server.support.model_config.service import create_model_config
    from server.support.resources.service import create_objects

    objects = {kind: create_objects(kind, database_path, cleanup=None)
               for kind in ("configuration", "avatar", "voice", "material")}
    model_config = create_model_config(database_path)

    def _unavailable(label):
        class _UnavailableProduction:
            output = objects["avatar" if label == "avatar" else "voice"]

            @staticmethod
            async def run(input):
                raise DetectionUnavailableError(f"{label}制作模型未接入，请在引擎接入后再试")

        return _UnavailableProduction()

    def _start_production(owner_id, payload):
        kind = "avatar" if "photo" in payload or "materials" in payload else "voice"
        production = _unavailable(kind)
        return create_task(owner_id, kind, payload, production.run, production.output)

    audio = KernelAudioRecorder(kernel)

    def _recording_factory():
        def submit(captured):
            production = _unavailable("voice")
            return create_task(0, "voice", captured, production.run, production.output)
        return create_recording(audio=audio, submit=submit)

    recording_port = AccountRecordingPort(_recording_factory)
    contexts = AccountContexts(model_config=model_config, engine_factory=None)
    return objects, model_config, {"avatar": _start_production, "voice": _start_production}, \
        recording_port, contexts
