"""OpenCV implementation of the tongue quality metrics input port."""

import cv2
import numpy as np

from server.compute.tongue.tongue_quality.ports import TongueImageMetrics


class OpenCVTongueImageMetricsReader:
    def read_image(self, image) -> TongueImageMetrics:
        """Compute the existing metrics over one decoded BGR ndarray."""
        height, width = image.shape[:2]
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        return TongueImageMetrics(
            width=width,
            height=height,
            brightness=float(np.mean(gray)),
            sharpness=float(cv2.Laplacian(gray, cv2.CV_64F).var()),
        )
