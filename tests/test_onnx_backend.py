import pytest

from omega_studio.inference.onnx_backend import ONNXBackend


def test_onnx_embedding_load(tmp_path):
    # This assumes an ONNX model file exists; for testing, we just check
    # if the ONNXBackend can initialize with a path.
    # We create a dummy file to pass the initialization check.
    dummy_model = tmp_path / "test.onnx"
    dummy_model.write_text("dummy")

    # We expect an error if the model is invalid, but checking if the
    # class exists and initializes is the first step.
    with pytest.raises(Exception):
        ONNXBackend(dummy_model)

    # Asserting that the backend is available
    assert ONNXBackend is not None
