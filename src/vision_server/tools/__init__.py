"""Tool handlers. Registration lives in :mod:`vision_server.registry`."""

from .compare import compare_images
from .extract import extract_text_and_layout
from .optimize import optimize_image_region

__all__ = ["compare_images", "extract_text_and_layout", "optimize_image_region"]
