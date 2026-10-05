"""Unit tests for ModelGateway, ModelProfile, and Fallback Routing."""
import unittest

from ai_code_engineer.errors import AgentError
from ai_code_engineer.model_gateway import (
    GatewayResponse,
    ModelCapability,
    ModelGateway,
    ModelProfile,
    TaskTier,
)


class ModelGatewayTests(unittest.TestCase):
    def setUp(self):
        self.gateway = ModelGateway()

        # Register primary fast model (e.g. Qwen3.8-Flash)
        p1 = ModelProfile(
            model_name="qwen-flash",
            provider_name="local_ollama",
            capabilities={ModelCapability.CHAT, ModelCapability.FAST_INFERENCE, ModelCapability.JSON_MODE},
            is_local=True,
        )
        # Register primary code model
        p2 = ModelProfile(
            model_name="qwen-coder",
            provider_name="local_ollama",
            capabilities={ModelCapability.CHAT, ModelCapability.CODE_GENERATION, ModelCapability.TOOL_CALLING},
            is_local=True,
        )

        self.gateway.register_profile(p1, lambda m, msgs: '{"decision": "fast_summary"}')
        self.gateway.register_profile(p2, lambda m, msgs: "def solution(): return 42")

        self.gateway.set_tier_route(TaskTier.FAST_TRIAGE, "qwen-flash")
        self.gateway.set_tier_route(TaskTier.CODE_ENGINEERING, "qwen-coder")

    def test_tier_based_routing(self):
        resp_triage = self.gateway.generate([{"role": "user", "content": "triage"}], tier=TaskTier.FAST_TRIAGE)
        self.assertEqual(resp_triage.model_used, "qwen-flash")
        self.assertFalse(resp_triage.fallback_occurred)

        resp_code = self.gateway.generate([{"role": "user", "content": "code"}], tier=TaskTier.CODE_ENGINEERING)
        self.assertEqual(resp_code.model_used, "qwen-coder")
        self.assertFalse(resp_code.fallback_occurred)

    def test_transparent_fallback_on_primary_failure(self):
        # Create a failing primary model
        failing_p = ModelProfile(
            model_name="primary-failing",
            provider_name="remote",
            capabilities={ModelCapability.CODE_GENERATION},
            is_local=False,
        )

        def failing_handler(m, msgs):
            raise TimeoutError("Remote server timed out after 30s")

        self.gateway.register_profile(failing_p, failing_handler)
        self.gateway.set_tier_route(TaskTier.CODE_ENGINEERING, "primary-failing")

        # Run with fallback to qwen-coder
        resp = self.gateway.generate(
            [{"role": "user", "content": "code"}],
            tier=TaskTier.CODE_ENGINEERING,
            fallback_models=["qwen-coder"],
        )
        self.assertEqual(resp.model_used, "qwen-coder")
        self.assertTrue(resp.fallback_occurred)
        self.assertIn("def solution()", resp.content)

    def test_capability_filtering(self):
        # Request tool calling which qwen-flash lacks
        resp = self.gateway.generate(
            [{"role": "user", "content": "call tool"}],
            tier=TaskTier.FAST_TRIAGE,  # maps to qwen-flash
            required_capabilities={ModelCapability.TOOL_CALLING},
            fallback_models=["qwen-coder"],
        )
        # Should have routed to qwen-coder because qwen-flash lacks TOOL_CALLING
        self.assertEqual(resp.model_used, "qwen-coder")

    def test_all_candidates_failed_raises_agent_error(self):
        p_bad = ModelProfile("broken", "local", {ModelCapability.CHAT})
        self.gateway.register_profile(p_bad, lambda m, msgs: (_ for _ in ()).throw(RuntimeError("Crash")))
        self.gateway.set_tier_route(TaskTier.COMPLEX_PLANNING, "broken")

        with self.assertRaises(AgentError):
            self.gateway.generate([{"role": "user", "content": "plan"}], tier=TaskTier.COMPLEX_PLANNING)
