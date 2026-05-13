import json

from omega_studio.config import ModelRecord, RegistryFile
from omega_studio.registry import merge_scan_into_registry, scan_folders


def test_scan_folders_temp(tmp_path):
    d = tmp_path / "m"
    d.mkdir()
    f = d / "tiny.gguf"
    f.write_bytes(b"x")
    found = scan_folders([str(d)])
    assert len(found) >= 1


def test_merge_scan(tmp_path):
    reg = RegistryFile(model_folders=[str(tmp_path)], models={})
    (tmp_path / "a.gguf").write_bytes(b"x")
    reg2, added = merge_scan_into_registry(reg)
    assert added >= 1
    assert reg2.models


def test_model_record_json_roundtrip():
    m = ModelRecord(path="C:/x.gguf", format="gguf", accelerator="openvino")
    data = json.loads(m.model_dump_json())
    assert data["path"].endswith("x.gguf")
