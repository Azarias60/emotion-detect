"""Numerical policy from fpy-copy/facephys_metrics.py (open-rppg 0.1.1).

The calculation and units below follow the reference's sealed-video path.
UI, filesystem and device handling belong to the caller.
"""
import logging
import math

MIN_HRV_SECONDS = 30.0


def finite_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def add_display_units(metrics: dict) -> dict:
    """Keep the upstream HRV fields and add explicitly named, display-safe units."""
    result = dict(metrics)
    hrv = metrics.get("hrv") or {}
    frequency = finite_number(hrv.get("breathingrate"))
    frequency = frequency if frequency is not None and frequency > 0 else None
    result["breathing_rate_hz"] = frequency
    result["breathing_rate_per_min"] = frequency * 60 if frequency is not None else None
    pnn50 = finite_number(hrv.get("pnn50"))
    result["hrv_summary"] = {"ibi_ms": finite_number(hrv.get("ibi")),
                             "rmssd_ms": finite_number(hrv.get("rmssd")),
                             "sdnn_ms": finite_number(hrv.get("sdnn")),
                             "pnn50_percent": pnn50 * 100 if pnn50 is not None else None,
                             "nn50": hrv.get("nn50"),
                             "rr_interval_count": hrv.get("rr_interval_count"),
                             "lf_hf_ratio": finite_number(hrv.get("LF/HF"))}
    return result


def measure_vitals(model, hr_window: float | None = 20,
                   hrv_window: float | None = 30) -> dict:
    """Calculate HR first; HRV failures must not suppress a usable HR reading."""
    import numpy as np
    from rppg.main import SQI, get_prv

    base = {"hr": None, "SQI": None, "hrv": {}, "latency": None}
    result = {**base, "hrv_status": "no_signal", "respiration_status": "no_signal",
              "hrv_window_seconds": 0.0, "hrv_signal_quality": None}
    if not model.has_signal or model.now < 2:
        return add_display_units(result)
    hr_start = -hr_window if hr_window else 0
    raw_hr, hr_timestamps = model.bvp(start=hr_start, raw=True)
    if len(hr_timestamps) > 1:
        result["hrv_window_seconds"] = float(hr_timestamps[-1] - hr_timestamps[0])
    # Upstream spectral peak/SQI can return a numeric value for an all-zero signal.
    if not len(raw_hr) or not np.isfinite(raw_hr).all() or np.std(raw_hr) <= 1e-12:
        return add_display_units(result)
    result.update(model.hr(start=hr_start, return_hrv=False) or base)
    result["hrv"] = {}
    if finite_number(result.get("SQI")) is None:
        return add_display_units(result)
    if result["SQI"] <= 0.5:
        result.update(hrv_status="low_signal_quality", respiration_status="low_signal_quality")
        return add_display_units(result)
    result["hrv_window_seconds"] = float(model.now)
    if model.now + 1 / model.fps + 1e-6 < MIN_HRV_SECONDS:
        result.update(hrv_status="collecting", respiration_status="collecting")
        return add_display_units(result)
    try:
        start = -hrv_window if hrv_window else 0
        raw, timestamps = model.bvp(start=start, raw=True)
        duration = float(timestamps[-1] - timestamps[0]) if len(timestamps) > 1 else 0
        result["hrv_window_seconds"] = duration
        if duration + 1 / model.fps + 1e-6 < MIN_HRV_SECONDS:
            result.update(hrv_status="collecting", respiration_status="collecting")
            return add_display_units(result)
        if not len(raw) or not np.isfinite(raw).all() or np.std(raw) <= 1e-12:
            return add_display_units(result)
        quality = finite_number(SQI(raw, sr=model.fps))
        result["hrv_signal_quality"] = quality
        if quality is None or quality <= 0.5:
            result.update(hrv_status="low_signal_quality", respiration_status="low_signal_quality")
            return add_display_units(result)
        bvp, timestamps = model.bvp(start=start)
        result["hrv"] = get_prv(bvp, timestamps, sr=model.fps)
        result = add_display_units(result)
        summary = result["hrv_summary"]
        result["hrv_status"] = "available" if summary["rmssd_ms"] is not None and summary["sdnn_ms"] is not None else "unavailable"
        result["respiration_status"] = "available" if result["breathing_rate_per_min"] is not None else "unavailable"
        return result
    except Exception as error:
        logging.debug("Pulse-interval analysis failed: %s", error, exc_info=True)
        result.update(hrv_status="unavailable", respiration_status="unavailable",
                      hrv_error=f"{type(error).__name__}: {error}")
        return add_display_units(result)
