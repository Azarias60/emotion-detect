"""Prepare health questions from the current tongue observation."""

from server.analysis.health.facts import labeled_tongue_recognition
from server.analysis.health.types import HealthQuestionSet
from server.compute.tongue.types import TongueRecognition


def prepare_health_questions(fact: TongueRecognition | None) -> HealthQuestionSet:
    """Use observed tongue features in a short, fixed consultation."""
    labeled = labeled_tongue_recognition(fact)
    coated = labeled["coated"]["label"] if labeled is not None else ""
    indentation = labeled["indentation"]["label"] if labeled is not None else ""
    questions = [
        (f"本次舌苔识别为{coated}，最近是否经常感觉口干、口黏或口中有异味？"
         if coated else "最近是否经常感觉口干、口黏或口中有异味？"),
        "最近一周的睡眠质量和入睡情况怎么样？",
        "最近是否有胃口变化、腹胀或消化不适？",
        (f"本次舌体识别为{indentation}，近期是否容易感到身体乏力或精神不足？"
         if indentation else "近期是否容易感到身体乏力或精神不足？"),
        "最近的情绪、压力和工作生活节奏有没有明显变化？",
    ]
    return HealthQuestionSet(tuple(questions))
