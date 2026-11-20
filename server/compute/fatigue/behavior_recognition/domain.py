"""Time-weighted behavior observation, independent of fatigue scoring."""

from server.compute.fatigue.types import BehaviorObservation


def summarize_behavior(
    *, blink_count: int, yawn_count: int, nod_count: int,
    closed_seconds: float, speaking_seconds: float,
    face_seconds: float, video_seconds: float,
) -> BehaviorObservation:
    return BehaviorObservation(
        blink_count=blink_count,
        blink_frequency=60.0 * blink_count / face_seconds if face_seconds else 0.0,
        yawn_count=yawn_count,
        nod_count=nod_count,
        nod_frequency=60.0 * nod_count / face_seconds if face_seconds else 0.0,
        perclos_percent=100.0 * closed_seconds / face_seconds if face_seconds else 0.0,
        speaking_percent=100.0 * speaking_seconds / face_seconds if face_seconds else 0.0,
        face_coverage=face_seconds / video_seconds if video_seconds else 0.0,
    )
