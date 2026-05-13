"""Typed studio configuration merged from registry JSON."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class StudioSettings(BaseModel):
    n_ctx: int = 8192
    temperature: float = 0.7
    top_p: float = 0.95
    n_gpu_layers: int = -1
    batch: int = 512
    threads: int = 8
    max_concurrent_models: int = 15
    lru_eviction_enabled: bool = True
    server_host: str = "127.0.0.1"
    server_port: int = 11434
    per_model_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ModelRecord(BaseModel):
    """Registry row for one weights file; ``ui_overrides`` are GUI loader/sampling defaults."""

    path: str
    format: str = "unknown"
    role: str | None = None
    embedding: bool = False
    accelerator: str = "auto"
    pinned: bool = False
    vram_estimate_mb: int | None = None
    display_name: str | None = None
    ui_overrides: dict[str, Any] = Field(default_factory=dict)


class RegistryFile(BaseModel):
    version: int = 1
    model_folders: list[str] = Field(default_factory=list)
    models: dict[str, ModelRecord] = Field(default_factory=dict)
    settings: StudioSettings = Field(default_factory=StudioSettings)
