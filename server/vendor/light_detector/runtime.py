"""Legacy checkpoint adaptation for inference only (vendor-local copy)."""


def load_detector_weights(path, name_map):
    import torch

    weights = torch.load(path, map_location="cpu", weights_only=True)
    weights = weights.get("state_dict", weights)
    converted = {}
    for key, value in weights.items():
        key = key.removeprefix("module.")
        first, separator, suffix = key.partition(".")
        converted[name_map.get(first, first) + separator + suffix] = value
    return converted
