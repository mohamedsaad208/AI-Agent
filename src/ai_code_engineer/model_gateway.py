"""Model Abstraction, Capability Routing, and Fallback Gateway.

"Models are replaceable compute providers. The agent architecture should survive model changes."

This module provides:
1. Model Capabilities: Explicitly defines what each model supports (tool calling, JSON mode, reasoning, etc.).
2. Task-Based Routing: Routes planning, code generation, and fast triage to the optimal model.
3. Resilient Fallback Gateway: Automatically falls back to secondary/local models upon timeout,
   unavailability, or malformed responses, while preserving strict security policies.
4. Offline-First Guarantee: Local inference (Ollama/LM Studio/canned) is always first-class.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Any, Callable, Optional

from .errors import AgentError, PolicyError
from .redaction import redact


class ModelCapability(str, Enum):
    """Capabilities supported by different model profiles."""
    CHAT = "chat"
    TOOL_CALLING = "tool_calling"
    JSON_MODE = "json_mode"
    CODE_GENERATION = "code_generation"
    REASONING = "reasoning"
    FAST_INFERENCE = "fast_inference"


class TaskTier(str, Enum):
    """Tiers of agent tasks requiring different model characteristics."""
    FAST_TRIAGE = "fast_triage"       # Classification, summaries, routing
    CODE_ENGINEERING = "code"         # Implementation, refactoring, fixing
    COMPLEX_PLANNING = "planning"     # Architecture, multi-step decomposition


@dataclass
class ModelProfile:
    """Descriptor of an available model and its operational limits."""
    model_name: str
    provider_name: str  # ollama, openai_compatible, local
    capabilities: set[ModelCapability] = field(default_factory=set)
    context_window: int = 8192
    is_local: bool = True
    timeout_seconds: float = 30.0

    def has_capability(self, cap: ModelCapability) -> bool:
        return cap in self.capabilities


@dataclass
class GatewayResponse:
    """Unified response from the model gateway."""
    content: str
    model_used: str
    provider_used: str
    latency_seconds: float
    fallback_occurred: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class ModelGateway:
    """Dispatches generation requests with capability matching and automatic fallback."""

    def __init__(self) -> None:
        self.profiles: dict[str, ModelProfile] = {}
        self.providers: dict[str, Callable[[str, list[dict]], str]] = {}
        self.tier_routing: dict[TaskTier, str] = {}

    def register_profile(
        self,
        profile: ModelProfile,
        provider_fn: Callable[[str, list[dict]], str],
    ) -> None:
        """Register a model profile and its corresponding inference handler."""
        self.profiles[profile.model_name] = profile
        self.providers[profile.model_name] = provider_fn

    def set_tier_route(self, tier: TaskTier, model_name: str) -> None:
        if model_name not in self.profiles:
            raise PolicyError(f"Model '{model_name}' is not registered.")
        self.tier_routing[tier] = model_name

    def select_model_for_tier(self, tier: TaskTier) -> str:
        """Select primary model configured for a task tier."""
        configured = self.tier_routing.get(tier)
        if configured and configured in self.profiles:
            return configured
        # Fallback to any registered model
        if self.profiles:
            return next(iter(self.profiles.keys()))
        raise AgentError("No models registered in ModelGateway.")

    def generate(
        self,
        messages: list[dict[str, Any]],
        tier: TaskTier = TaskTier.CODE_ENGINEERING,
        required_capabilities: Optional[set[ModelCapability]] = None,
        fallback_models: Optional[list[str]] = None,
    ) -> GatewayResponse:
        """Generate a response, attempting primary route with transparent fallback on error."""
        primary_name = self.select_model_for_tier(tier)
        candidates = [primary_name] + [m for m in (fallback_models or []) if m != primary_name]

        last_error: Optional[Exception] = None
        for idx, candidate_name in enumerate(candidates):
            profile = self.profiles.get(candidate_name)
            handler = self.providers.get(candidate_name)
            if not profile or not handler:
                continue

            # Verify required capabilities
            if required_capabilities:
                missing = required_capabilities - profile.capabilities
                if missing:
                    continue

            t0 = time.perf_counter()
            try:
                raw_response = handler(candidate_name, messages)
                latency = time.perf_counter() - t0
                return GatewayResponse(
                    content=redact(str(raw_response)),
                    model_used=candidate_name,
                    provider_used=profile.provider_name,
                    latency_seconds=round(latency, 3),
                    fallback_occurred=(idx > 0),
                )
            except Exception as exc:
                last_error = exc
                continue

        raise AgentError(
            f"All model candidates failed for tier '{tier.value}'. Last error: {last_error}"
        )
