"""One-off: migrate the catalog patches in tests/test_controller.py to controller.models_for.

Discovery used to be one function per provider; it is now a single ``models_for(kind, endpoint, key)``
that answers ``(entries, source)``. Every test that patched the old pair patches the helper instead.
"""
from pathlib import Path

path = Path("tests/test_controller.py")
text = path.read_text(encoding="utf-8")

A = ('        started = (patch("ai_code_engineer.webapp.controller.ollama_models", return_value=[OLLAMA_ENTRY]),\n'
     '                   patch("ai_code_engineer.webapp.controller.openrouter_models", return_value=[FREE_ENTRY]))\n')
B = ('        for patcher in (patch("ai_code_engineer.webapp.controller.ollama_models", return_value=[OLLAMA_ENTRY]),\n'
     '                        patch("ai_code_engineer.webapp.controller.openrouter_models", return_value=[FREE_ENTRY]),\n')
C = ('        for patcher in (patch("ai_code_engineer.webapp.controller.ollama_models",\n'
     '                              return_value=[OLLAMA_ENTRY]),\n'
     '                        patch("ai_code_engineer.webapp.controller.openrouter_models",\n'
     '                              return_value=[FREE_ENTRY]),\n')
D = ('        for patcher in (patch("ai_code_engineer.webapp.controller.ollama_models",\n'
     '                              return_value=[OLLAMA_ENTRY]),\n'
     '                        patch("ai_code_engineer.webapp.controller.openrouter_models",\n'
     '                              return_value=[FREE_ENTRY])):\n')
E = ('        for patcher in (patch("ai_code_engineer.webapp.controller.ollama_models", return_value=[OLLAMA_ENTRY]),\n'
     '                        patch("ai_code_engineer.webapp.controller.openrouter_models", return_value=[FREE_ENTRY])):\n')
F = '                   patch("ai_code_engineer.webapp.controller.ollama_models", return_value=[OLLAMA_ENTRY]),\n'
G = '                   patch("ai_code_engineer.webapp.controller.ollama_models", return_value=[OLLAMA_ENTRY])]\n'
H = '                        patch("ai_code_engineer.webapp.controller.ollama_models", return_value=[OLLAMA_ENTRY])):\n'

for source, replacement in ((A, '        started = (patched_catalog(),)\n'),
                            (B, '        for patcher in (patched_catalog(),\n'),
                            (C, '        for patcher in (patched_catalog(),\n'),
                            (D, '        for patcher in (patched_catalog(),):\n'),
                            (E, '        for patcher in (patched_catalog(),):\n'),
                            (F, '                   patched_catalog(),\n'),
                            (G, '                   patched_catalog()]\n'),
                            (H, '                        patched_catalog()):\n')):
    print(text.count(source), repr(source[:60]))
    text = text.replace(source, replacement)

path.write_text(text, encoding="utf-8", newline="\n")
print("ollama_models left:", text.count("ollama_models"), "openrouter_models left:", text.count("openrouter_models"))
