"""Wraps onnxruntime.InferenceSession for embedding/face/classifier execution."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

try:
    import onnxruntime as ort
except ImportError:
    ort = None

log = logging.getLogger("omega_studio.inference.onnx_backend")

# VitisAI -> CUDA -> DML -> CPU
DEFAULT_PROVIDERS = [
    "VitisAIExecutionProvider",
    "CUDAExecutionProvider",
    "DirectMLExecutionProvider",
    "CPUExecutionProvider",
]


class ONNXBackend:
    def __init__(self, model_path: str | Path):
        if ort is None:
            raise RuntimeError("onnxruntime is not installed.")
        self.model_path = str(model_path)
        self.session = ort.InferenceSession(self.model_path, providers=DEFAULT_PROVIDERS)
        log.info(
            "loaded ONNX model %s (providers: %s)",
            self.model_path,
            self.session.get_providers(),
        )

    def run(self, input_data: Any) -> Any:
        input_name = self.session.get_inputs()[0].name
        return self.session.run(None, {input_name: input_data})
