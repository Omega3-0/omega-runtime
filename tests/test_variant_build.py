from omega_studio.inference.backends import get_omega_variant


def test_variant_detection():
    # Verify we can detect variant from env
    assert get_omega_variant() in ["cpu", "cuda", "vulkan", "dml"]


def test_effective_backend_resolution():
    # Placeholder for logic verification once implemented
    # We will expand this as we port _resolve_backend
    pass
