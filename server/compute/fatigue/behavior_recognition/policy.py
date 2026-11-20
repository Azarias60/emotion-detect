"""Behavior thresholds. Every time threshold is in capture-clock seconds."""

# Estimate cadence from recent connected intervals. Allow jitter and short
# dropouts while keeping an absolute cap even on sparsely sampled video.
CADENCE_HISTORY_INTERVALS = 9
GAP_CADENCE_MULTIPLIER = 3.0
MIN_GAP_LIMIT_SECONDS = 0.15
MAX_GAP_SECONDS = 0.50

# Reject brief EAR dips and distinguish ordinary blinks from prolonged closures.
EYE_AR_THRESHOLD = 0.23
BLINK_MIN_CLOSED_SECONDS = 0.08
BLINK_MAX_CLOSED_SECONDS = 0.50

# Yawns have sustained mouth opening and require a definite close before rearm.
YAWN_OPEN_MAR = 0.62
YAWN_RELEASE_MAR = 0.55
YAWN_MIN_OPEN_SECONDS = 1.0
YAWN_RECOVERY_SECONDS = 0.20

# A pitch excursion must follow neutral and stay down long enough to count.
NOD_DOWN_DEGREES = 5.0
NOD_NEUTRAL_DEGREES = 3.0
NOD_MIN_DOWN_SECONDS = 0.30
NOD_RECOVERY_SECONDS = 0.15

# Time-constant smoothing, rolling evidence and hold are cadence independent.
SPEECH_MIN_MAR = 0.20
SPEECH_MAX_MAR = 0.58
SPEECH_OPEN_MAR = 0.24
SPEECH_CLOSE_MAR = 0.19
SPEECH_SMOOTH_SECONDS = 0.10
SPEECH_WINDOW_SECONDS = 1.0
SPEECH_MIN_EVIDENCE_SECONDS = 0.18
SPEECH_MIN_RANGE = 0.045
SPEECH_MIN_MAR_PER_SECOND = 0.08
SPEECH_MIN_TRANSITIONS = 2
SPEECH_HOLD_SECONDS = 0.55

# Comparison tolerance for boundaries after subtraction of device timestamps.
TIME_EPSILON_SECONDS = 1e-9
