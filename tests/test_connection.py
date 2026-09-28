"""The connection spine: one provider table, one endpoint policy, discovery that follows it.

UI 4.2 gave the tool more than Ollama and OpenRouter. Every literal that used to encode a provider
- the Ollama URL in the catalog, the OpenRouter URL in the provider, the two-value provider
whitelist, the loopback rule that refused a URL path - is now one row of a table, and these tests
are what keeps the rows honest.
"""
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import catalog, config
from ai_code_engineer.config import KINDS, Settings, check_endpoint, kind_for, needs_consent, settings_for, validate
from ai_code_engineer.errors import AgentError, PolicyError, ProviderError
from ai_code_engineer.labels import catalog_status_line, friendly_error
from ai_code_engineer.providers import OpenAICompatibleProvider, OllamaProvider, make_provider

ANSWER = {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]}


def catalog_status(live):
    """The same refresh answer, with and without a live list behind it."""
    return catalog_status_line(arabic=False, count=3, model="m", label="Groq", live=live)


class TheProviderTable(unittest.TestCase):
    def test_every_row_is_addressable_by_key_and_by_label(self):
        for row in KINDS:
            self.assertIs(kind_for(row.key), row)
            self.assertIs(kind_for(row.label), row)
        self.assertIsNone(kind_for("anthropic"))
        self.assertIsNone(kind_for(None))

    def test_the_windows_and_the_table_list_the_same_rows(self):
        # Two mode tables was how the web window and Tk disagreed about a provider's name.
        from ai_code_engineer.webapp import controller
        self.assertEqual(controller.MODES, config.MODES)
        self.assertEqual(len(config.MODES), len(KINDS) + 1, "OpenRouter is the one row that splits")

    def test_openrouter_is_the_only_row_split_by_price(self):
        labels = [label for label, row in config.mode_rows()]
        self.assertIn(config.free_mode(KINDS[6]), labels)
        self.assertIn(config.paid_mode(KINDS[6]), labels)
        for row in KINDS:
            if not row.free_only:
                self.assertNotIn(config.paid_mode(row), labels)

    def test_validate_accepts_every_row_and_rejects_an_unknown_one(self):
        for row in KINDS:
            if row.key == "generic":
                continue          # Custom has no default address to validate against
            validate(replace(Settings(), provider=row.key, endpoint=row.base,
                              model="anything"))
        with self.assertRaises(AgentError):
            validate(replace(Settings(), provider="anthropic"))

    def test_a_profile_names_a_variable_and_never_holds_a_key(self):
        with self.assertRaises(AgentError):
            validate(replace(Settings(), api_key_env="sk-or-vl-abcdefghijklmnopqrst"))
        validate(replace(Settings(), api_key_env="GROQ_API_KEY"))


class TheEndpointPolicy(unittest.TestCase):
    def test_a_path_is_allowed_because_it_is_the_shape_of_a_base_url(self):
        # The old loopback rule refused any path, which is exactly what /v1 is.
        self.assertEqual(check_endpoint(kind_for("lmstudio"), "http://localhost:1234/v1"),
                         "http://localhost:1234/v1")
        self.assertEqual(check_endpoint(kind_for("ollama"), "http://127.0.0.1:11434/ollama/"),
                         "http://127.0.0.1:11434/ollama")

    def test_a_local_row_stays_on_this_device(self):
        for key in ("ollama", "lmstudio", "vllm"):
            with self.assertRaises(PolicyError):
                check_endpoint(kind_for(key), "https://elsewhere.example/v1")
            check_endpoint(kind_for(key), kind_for(key).base)

    def test_cleartext_to_another_machine_is_refused_for_everyone(self):
        with self.assertRaises(PolicyError):
            check_endpoint(kind_for("groq"), "http://api.groq.com/openai/v1")
        with self.assertRaises(PolicyError):
            check_endpoint(kind_for("generic"), "http://192.168.1.20:8000/v1")
        check_endpoint(kind_for("generic"), "https://inference.example/v1")

    def test_a_custom_loopback_server_needs_no_cloud_approval(self):
        local = "http://127.0.0.1:8000/v1"
        self.assertFalse(needs_consent(kind_for("generic"), local))
        self.assertTrue(needs_consent(kind_for("generic"), "https://someone.elses.server/v1"))
        self.assertTrue(needs_consent(kind_for("groq"), "https://api.groq.com/openai/v1"))

    def test_the_shapes_that_carry_a_key_or_a_redirect_in_the_url(self):
        for bad in ("ftp://127.0.0.1:11434", "http://user:pass@localhost:11434",
                    "http://localhost:1234/v1?callback=evil", "http://localhost:1234/v1#frag",
                    "http:///v1", ""):
            with self.assertRaises(PolicyError, msg=bad):
                check_endpoint(kind_for("generic"), bad)

    def test_settings_for_pairs_a_row_with_its_own_default(self):
        self.assertEqual(settings_for(kind_for("groq")).endpoint, "https://api.groq.com/openai/v1")
        self.assertEqual(settings_for(kind_for("openrouter"), "https://openrouter.ai/api/v1").endpoint,
                         "https://openrouter.ai/api/v1")
        with self.assertRaises(PolicyError):
            settings_for(kind_for("generic"))


class TheOpenAIShapedProvider(unittest.TestCase):
    def settings(self, key, model="", endpoint=""):
        row = kind_for(key)
        # A hosted row still needs a *free* model name unless the caller has said otherwise: the
        # paid-model refusal belongs to OpenRouter alone, and a test that trips it tests nothing.
        return replace(Settings(), provider=key, endpoint=endpoint or row.base,
                       model=model or ("openrouter/free" if row.free_only else "m"),
                       api_key_env=row.key_env)

    def test_every_row_posts_to_its_configured_base(self):
        for key in ("openai", "groq", "deepseek", "lmstudio", "vllm", "openrouter", "generic"):
            row = kind_for(key)
            base = row.base or "https://inference.example/v1"
            provider = OpenAICompatibleProvider(self.settings(key, endpoint=base), api_key="test-key")
            with patch("ai_code_engineer.providers.request_json", return_value=ANSWER) as request:
                provider.generate([{"role": "user", "content": "x"}])
                self.assertEqual(request.call_args.args[0], base + "/chat/completions", key)

    def test_the_routing_block_is_openrouters_alone(self):
        for key in ("groq", "openrouter"):
            settings = self.settings(key)
            provider = OpenAICompatibleProvider(settings, api_key="test-key")
            with patch("ai_code_engineer.providers.request_json", return_value=ANSWER) as request:
                provider.generate([])
                body = request.call_args.args[1]
                self.assertEqual("provider" in body, key == "openrouter")
                self.assertEqual(body["model"], settings.model)
                self.assertEqual(body["response_format"], {"type": "json_object"})
                self.assertFalse(body["stream"])

    def test_a_key_goes_in_the_header_and_nowhere_else(self):
        provider = OpenAICompatibleProvider(self.settings("groq"),
                                            api_key="sk-ant-abcdefghijklmnopqrstuv")
        with patch("ai_code_engineer.providers.request_json", return_value=ANSWER) as request:
            provider.generate([])
            self.assertEqual(request.call_args.kwargs["key"], "sk-ant-abcdefghijklmnopqrstuv")
            self.assertNotIn("sk-ant-abcdefghijklmnopqrstuv", json.dumps(request.call_args.args[1]))

    def test_a_row_that_needs_a_key_refuses_without_one(self):
        env = {name: "" for name in ("GROQ_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY",
                                     "OPENROUTER_API_KEY")}
        with patch.dict(os.environ, env, clear=False):
            with self.assertRaises(ProviderError) as caught:
                OpenAICompatibleProvider(self.settings("groq"))
        self.assertIn("GROQ_API_KEY", str(caught.exception))
        self.assertIn("GROQ_API_KEY", friendly_error(caught.exception))

    def test_a_local_server_may_be_given_no_key_at_all(self):
        provider = OpenAICompatibleProvider(self.settings("lmstudio"))
        self.assertEqual(provider.key, "")

    def test_the_model_that_answered_is_reported_only_for_openrouter(self):
        for key, expected in (("openrouter", "upstream/actual"), ("groq", "m")):
            provider = OpenAICompatibleProvider(self.settings(key), api_key="k")
            with patch("ai_code_engineer.providers.request_json",
                       return_value={"choices": [{"finish_reason": "stop",
                                                  "message": {"content": "{}"}}],
                                     "model": "upstream/actual"}):
                provider.generate([])
                self.assertEqual(provider.model, expected)

    def test_an_ollama_row_still_uses_the_ollama_wire(self):
        provider = OllamaProvider(Settings())
        with patch("ai_code_engineer.providers.request_json",
                   return_value={"message": {"content": "{}"}}) as request:
            provider.generate([{"role": "user", "content": "x"}])
            self.assertEqual(request.call_args.args[0], "http://127.0.0.1:11434/api/chat")
            self.assertEqual(request.call_args.args[1]["format"], "json")


class ConsentAtTheFactory(unittest.TestCase):
    def test_a_cloud_row_refuses_before_any_request(self):
        for key in ("openai", "groq", "deepseek", "openrouter"):
            row = kind_for(key)
            settings = replace(Settings(), provider=key, model="m", endpoint=row.base)
            with patch("ai_code_engineer.providers.request_json") as request:
                with self.assertRaises(PolicyError, msg=key):
                    make_provider(settings, allow_cloud=False, data_class="public")
                with self.assertRaises(PolicyError, msg=key):
                    make_provider(settings, allow_cloud=True, data_class="restricted")
                request.assert_not_called()

    def test_a_custom_endpoint_is_judged_by_its_host(self):
        generic = replace(Settings(), provider="generic", model="m",
                          endpoint="http://127.0.0.1:8000/v1")
        with patch("ai_code_engineer.providers.request_json", return_value=ANSWER):
            provider = make_provider(generic, allow_cloud=False, data_class="restricted")
        self.assertEqual(provider.kind.key, "generic")
        remote = replace(generic, endpoint="https://inference.example/v1")
        with self.assertRaises(PolicyError):
            make_provider(remote, allow_cloud=False, data_class="restricted")

    def test_a_local_row_needs_no_consent(self):
        lmstudio = replace(Settings(), provider="lmstudio", model="m",
                           endpoint="http://localhost:1234/v1")
        with patch("ai_code_engineer.providers.request_json", return_value=ANSWER):
            provider = make_provider(lmstudio, allow_cloud=False, data_class="restricted")
        self.assertEqual(provider.kind.key, "lmstudio")


class DiscoveryFollowsTheEndpoint(unittest.TestCase):
    def test_ollama_is_queried_where_it_was_told_it_lives(self):
        # The regression this whole round starts from: discovery read a literal while generation
        # read settings, so a relocated Ollama reported "no models found".
        with patch("ai_code_engineer.catalog.request_json", return_value={"models": []}) as request:
            catalog.ollama_models("http://127.0.0.1:19999/ollama")
            self.assertEqual(request.call_args.args[0], "http://127.0.0.1:19999/ollama/api/tags")

    def test_openrouter_is_queried_at_its_configured_base(self):
        with patch("ai_code_engineer.catalog.request_json", return_value={"data": []}) as request:
            catalog.openrouter_models(None, "https://openrouter.ai/api/v1")
            self.assertEqual(request.call_args.args[0], "https://openrouter.ai/api/v1/models")

    def test_v1_models_is_parsed_in_both_shapes(self):
        for payload in ({"data": [{"id": "a"}, {"id": "b"}]}, {"models": ["a", "b"]}):
            with patch("ai_code_engineer.catalog.request_json", return_value=payload):
                found = catalog.openai_models("http://localhost:1234/v1", cloud=False)
            self.assertEqual([entry["id"] for entry in found], ["a", "b"])
            self.assertEqual([entry["cloud"] for entry in found], [False, False])

    def test_the_openai_list_does_not_claim_a_price_it_cannot_see(self):
        with patch("ai_code_engineer.catalog.request_json", return_value={"data": [{"id": "m"}]}):
            entry = catalog.openai_models("https://api.example/v1")[0]
        self.assertIn("Pricing is not reported", entry["description"])

    def test_a_failure_falls_back_only_where_names_are_actually_known(self):
        with patch("ai_code_engineer.catalog.request_json", side_effect=ProviderError("down")):
            entries, source = catalog.models_for(kind_for("groq"))
            self.assertEqual(source, catalog.BUILT_IN)
            self.assertIn("llama-3.3-70b-versatile", [entry["id"] for entry in entries])
            with self.assertRaises(ProviderError):
                catalog.models_for(kind_for("lmstudio"))      # nothing verified, nothing guessed

    def test_a_live_answer_says_so(self):
        with patch("ai_code_engineer.catalog.request_json", return_value={"data": [{"id": "m"}]}):
            entries, source = catalog.models_for(kind_for("openai"), "https://api.openai.com/v1", "k")
        self.assertEqual(source, catalog.LIVE)
        self.assertEqual(entries[0]["cloud"], True)

    def test_the_two_answers_are_described_differently_to_the_user(self):
        self.assertIn("ships with", catalog_status(live=False))
        self.assertNotIn("ships with", catalog_status(live=True))
        self.assertIn("Groq", catalog_status(live=True))


class Profiles(unittest.TestCase):
    def test_every_shipped_profile_loads_onto_a_known_row(self):
        names = config.profile_names()
        self.assertIn("local", names)
        for name in names:
            settings = config.load_profile(name)
            validate(settings)
            self.assertIsNotNone(kind_for(settings.provider), name)

    def test_the_hosted_rows_ship_with_their_variable_name(self):
        for name, variable in (("groq", "GROQ_API_KEY"), ("openai", "OPENAI_API_KEY"),
                               ("deepseek", "DEEPSEEK_API_KEY"), ("cloud-free", "OPENROUTER_API_KEY")):
            self.assertEqual(config.load_profile(name).api_key_env, variable)

    def test_a_profile_label_is_a_name_not_a_path(self):
        for bad in ("../secrets", "a/b", "", "nope", "x" * 60, "local.toml"):
            with self.assertRaises(AgentError, msg=bad):
                config.load_profile(bad)

    def test_a_directory_of_profiles_is_read_only_when_it_exists(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(config.profile_names(Path(folder)), [])
            Path(folder, "ok.toml").write_text('[model]\nprovider = "ollama"\nname = "m"\n',
                                               encoding="utf-8")
            Path(folder, "..weird.toml").write_text("", encoding="utf-8")
            self.assertEqual(config.profile_names(Path(folder)), ["ok"])


class AKeyNeverReachesTheWindows(unittest.TestCase):
    def test_the_detail_of_a_failure_is_redacted_before_it_is_stored(self):
        secret = "sk-or-vl-abcdefghijklmnopqrstuvwxyz123456"
        self.assertNotIn(secret, friendly_error(AgentError("Provider said: " + secret)))
        self.assertNotIn(secret, friendly_error(ProviderError(secret)))

    def test_a_model_reason_is_redacted_too(self):
        # engine.py interpolates the model's own blocked reason into an AgentError, and the
        # fallthrough of friendly_error hands that text back verbatim.
        secret = "password = hunter2hunter2"
        result = friendly_error(AgentError("could not produce a proposal: " + secret))
        self.assertNotIn("hunter2hunter2", result)


LOCAL_ENTRY = {"id": "test-local", "name": "Test Local", "cloud": False,
               "description": "Synthetic local model for tests"}
CLOUD_ENTRY = {"id": "llama3.1:70b-cloud", "name": "Llama 3.1 70B cloud", "cloud": True,
               "description": "Runs in the cloud"}
FREE = {"id": "x:free", "name": "X Free", "free": True, "cloud": True, "description": "free"}


class TheConnectionRowInAWindow(unittest.TestCase):
    """The window's side of the spine: what a typed endpoint does, and what a profile moves."""

    def setUp(self):
        from ai_code_engineer.webapp.controller import AgentController
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app_dir = Path(self.temp.name)
        self.events = []

        def models_for(kind, endpoint="", api_key=None):
            self.asked = [kind.key, endpoint]
            return ([dict(LOCAL_ENTRY)], catalog.LIVE) if not kind.free_only \
                else ([dict(FREE), dict(CLOUD_ENTRY)], catalog.LIVE)

        patcher = patch("ai_code_engineer.webapp.controller.models_for", side_effect=models_for)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.controller = AgentController(self.app_dir)
        self.controller._emit = self.events.append
        self.controller.join()

    def settle(self):
        self.controller.join()

    def registry(self):
        return json.loads((self.app_dir / ".agent-projects.json").read_text(encoding="utf-8"))

    def test_the_snapshot_sends_every_field_the_drawer_draws(self):
        row = self.controller.snapshot()["connection"]
        for key in ("kind", "label", "endpoint", "default_endpoint", "cloud", "shape", "needs_key",
                    "key_env", "consent", "paid", "profile", "profiles", "source", "key_present"):
            self.assertIn(key, row)
        self.assertEqual(row["kind"], "ollama")
        self.assertEqual(row["endpoint"], "http://127.0.0.1:11434")
        self.assertEqual(row["consent"], False)

    def test_a_cleartext_endpoint_for_a_cloud_row_is_refused_not_stored(self):
        self.controller.set_mode("Groq")
        self.settle()
        before = self.controller.endpoint_for()
        self.controller.set_endpoint("http://api.groq.com/openai/v1")
        self.assertEqual(self.controller.endpoint_for(), before,
                         "a refused endpoint must not half-apply")
        self.assertIn("https", self.controller.status)

    def test_a_valid_endpoint_lands_and_drops_the_list_it_invalidated(self):
        self.controller.set_mode("LM Studio")
        self.settle()
        self.controller.catalogs["LM Studio"] = [dict(LOCAL_ENTRY)]
        self.controller.set_endpoint("http://localhost:1235/v1")
        self.settle()
        self.assertEqual(self.controller.endpoint_for(), "http://localhost:1235/v1")
        self.assertEqual(self.asked[1], "http://localhost:1235/v1",
                         "discovery must re-read the new address, not keep the old list")
        self.assertEqual(self.registry()["ui"]["endpoints"]["lmstudio"], "http://localhost:1235/v1")

    def test_a_trailing_slash_is_normalised_once_and_stored_that_way(self):
        self.controller.set_endpoint("http://127.0.0.1:11434/ollama/")
        self.settle()
        self.assertEqual(self.controller.endpoint_for(), "http://127.0.0.1:11434/ollama")

    def test_each_row_keeps_its_own_address_when_the_rows_are_switched(self):
        self.controller.set_mode("vLLM")
        self.settle()
        self.controller.set_endpoint("http://localhost:9000/v1")
        self.settle()
        self.controller.set_mode("Ollama")
        self.assertEqual(self.controller.endpoint_for(), "http://127.0.0.1:11434")
        self.controller.set_mode("vLLM")
        self.assertEqual(self.controller.endpoint_for(), "http://localhost:9000/v1")

    def test_a_profile_moves_the_row_the_endpoint_and_the_model_with_it(self):
        self.controller.set_profile("groq")
        self.settle()
        self.assertEqual(self.controller.mode, "Groq")
        self.assertEqual(self.controller.endpoint_for(), "https://api.groq.com/openai/v1")
        self.assertEqual(self.controller.model, "llama-3.3-70b-versatile")
        self.assertEqual(self.registry()["ui"]["profile"], "groq")

    def test_a_profile_names_its_variable_and_leaves_the_key_alone(self):
        self.controller.set_key("sk-or-vl-abcdefghijklmnopqrstuvwxyz123456")
        self.controller.set_profile("openai")
        self.settle()
        self.assertEqual(self.controller.snapshot()["connection"]["key_env"], "OPENAI_API_KEY")
        disk = json.dumps(self.registry())
        self.assertNotIn("sk-or-vl-abcdefghijklmnopqrstuvwxyz123456", disk)
        self.assertNotIn("api_key", disk.replace("api_key_env", ""),
                         "a profile's variable name is the only key-shaped thing on disk")

    def test_a_bogus_profile_label_reads_no_file(self):
        self.controller.set_mode("Ollama")
        self.settle()
        for label in ("../cloud-free", "a/b", "nope"):
            self.controller.set_profile(label)
            self.assertEqual(self.controller.profile, "", label)
            self.assertEqual(self.controller.mode, "Ollama", label)

    def test_a_custom_row_with_no_address_builds_no_settings(self):
        # Custom is the one row with no default: an empty field must not become a request, and the
        # refusal has to say what is missing rather than fail inside a worker thread.
        self.controller.set_mode("Custom endpoint")
        self.settle()
        self.assertEqual(self.controller.endpoint_for(), "")
        self.assertIsNone(self.controller.task_settings(False))
        self.assertIn("endpoint", self.controller.status.casefold())
        with patch("ai_code_engineer.webapp.controller.make_provider") as factory:
            self.controller.set_repo(str(self.app_dir))
            self.controller.catalogs["Custom endpoint"] = [dict(LOCAL_ENTRY)]
            self.controller.model = LOCAL_ENTRY["id"]
            self.controller.cloud_ok = True
            self.controller.start_plan("Fix add")
            self.settle()
        factory.assert_not_called()

    def test_a_local_custom_endpoint_needs_no_cloud_approval(self):
        self.controller.set_mode("Custom endpoint")
        self.settle()
        self.controller.set_endpoint("http://127.0.0.1:8000/v1")
        self.settle()
        self.assertEqual(self.controller.cloud_choice(), (False, False))

    def test_a_remote_custom_endpoint_is_treated_exactly_like_a_cloud_row(self):
        self.controller.set_mode("Custom endpoint")
        self.settle()
        self.controller.set_endpoint("https://inference.example/v1")
        self.settle()
        self.assertEqual(self.controller.cloud_choice()[0], True)
        self.assertTrue(self.controller.snapshot()["connection"]["consent"])

    def test_a_cloud_model_on_a_local_row_still_asks(self):
        # An Ollama model tagged "cloud" answers over the internet from a loopback URL: the row is
        # local, the model is not, and the approval has to follow the model.
        self.controller.catalogs["Ollama"] = [dict(CLOUD_ENTRY)]
        self.controller.model = CLOUD_ENTRY["id"]
        self.assertEqual(self.controller.cloud_choice()[0], True)

    def test_the_free_and_paid_lists_come_from_one_refresh(self):
        self.controller.set_mode(config.free_mode(kind_for("openrouter")))
        self.settle()
        self.assertEqual([entry["id"] for entry in self.controller.catalogs[
            config.free_mode(kind_for("openrouter"))]], ["x:free"])
        self.assertEqual([entry["id"] for entry in self.controller.catalogs[
            config.paid_mode(kind_for("openrouter"))]], ["llama3.1:70b-cloud"])

    def test_the_refresh_sentence_says_where_the_list_came_from(self):
        with patch("ai_code_engineer.webapp.controller.models_for",
                   return_value=([dict(LOCAL_ENTRY)], catalog.BUILT_IN)):
            self.controller.check_setup()
            self.controller.join()
        self.assertIn("ships with", self.controller.status)

    def test_a_failure_keeps_its_reason_and_loses_its_credentials(self):
        secret = "sk-or-vl-abcdefghijklmnopqrstuvwxyz123456"
        with patch("ai_code_engineer.webapp.controller.models_for",
                   side_effect=ProviderError("Provider HTTP 401: Authorization: " + secret)):
            self.controller.check_setup()
            self.controller.join()
        stored = json.dumps(self.controller.snapshot()["log"])
        self.assertNotIn(secret, stored)
        self.assertIn("[redacted]", stored)
        self.assertIn("HTTP 401", stored, "the reason is what the user has to act on")
