"""The first-run audit: what this machine can reach, in the order a new operator needs it.

`agent doctor` already answers some of this and `agent demo` already proves the red line, but each of
them answers one question and neither says what to do next. This module **sequences the probes that
exist** — no second reachability check, no second model list, no second demo — and gives every row the
sentence both windows will print, in the language the operator asked in.

Nothing here writes to a project and nothing here asks a question: the caller decides whether a row is
shown (doctor) or acted on (setup), and the wizard in the CLI and in the windows share these rows.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

from . import catalog, config, git_integration, intent, runner
from .errors import AgentError
from .labels import say

REQUIRED_PYTHON = (3, 11)

# The order a first run needs: what this machine is, what it can run, who answers, what models exist,
# which folder is granted, proof that the red line works, what the tool promises, and what Send may do.
STEP_IDS = ("runtime", "toolchain", "provider", "model", "project", "demo", "policy", "position")

OK, WARN, BAD, INFO = "ok", "warn", "bad", "info"

# The five promises the operator is being asked to accept, in the order they can be checked.
POLICY = (
    ("Nothing is written until you approve a diff you have read.",
     "مفيش كتابة قبل ما توافق على فرق قراتها."),
    ("No code leaves this device unless you approved that provider, for that message.",
     "مفيش كود بيطلع من الجهاز ده غير بموافقتك على البروفايدر ده، للرسالة دي."),
    ("An API key lives in memory only. It is never written to a file or a log.",
     "مفتاح الـ API بيعيش في الرا memory بس. ما بيتكتبش في ملف ولا في سجل."),
    ("An apply inside a git folder gets a checkpoint commit, and the card rolls it back.",
     "التطبيق جوّه مجلد git بياخد commit مرجعي، والكارت بيرجّعه."),
    ("Everything a task did is on disk: agent export-session writes the report.",
     "كل ما عملته المهمة متسجل على القرص: agent export-session بيكتب التقرير."),
)


def row(step: str, status: str, *, arabic: bool, en: str, ar: str,
        advice_en: str = "", advice_ar: str = "") -> dict:
    """One line of the audit. A row without advice is a row nobody can act on."""
    return {"id": step, "status": status, "text": say(arabic, en=en, ar=ar),
            "advice": say(arabic, en=advice_en, ar=advice_ar) if advice_en else ""}


def reach(kind: config.Kind, endpoint: str = "", api_key: str | None = None):
    """Ask one provider what it has. Returns ``(entries, source, error)``.

    Shared by `cli.doctor` and `audit` so the two cannot disagree about whether Ollama answered — and
    so the first run never reports a model as installed on the strength of a fallback list.
    """
    try:
        entries, source = catalog.models_for(kind, endpoint, api_key)
    except (AgentError, OSError) as exc:
        return [], "", str(exc)
    return entries, source, ""


def runtime_row(*, arabic: bool) -> dict:
    version = sys.version_info
    named = "{}.{}.{}".format(version.major, version.minor, version.micro)
    if version < REQUIRED_PYTHON:
        return row("runtime", BAD, arabic=arabic,
                   en=f"Python {named} is too old for this tool.",
                   ar=f"بايثون {named} أقدم من اللي الأداة دي محتاجاها.",
                   advice_en="Install Python {}.{} or newer, then run this again.".format(*REQUIRED_PYTHON),
                   advice_ar="نصّب بايثون {}.{} أو أحدث، وارجع شغّل الأمر ده.".format(*REQUIRED_PYTHON))
    tk = importlib.util.find_spec("tkinter") is not None
    if not tk:
        return row("runtime", WARN, arabic=arabic,
                   en=f"Python {named} is new enough. The desktop window cannot start: Tk is missing.",
                   ar=f"بايثون {named} كفاية. نافذة سطح المكتب مش هتشتغل: مكتبة Tk مش موجودة.",
                   advice_en="The web window needs nothing more. For the desktop one, install the "
                             "tk package for this Python.",
                   advice_ar="نافذة الويب مش محتاجة حاجة تانية. للنافذة دي، نصّب حزمة tk لبايثون ده.")
    return row("runtime", OK, arabic=arabic,
               en=f"Python {named}, standard library only, and the desktop window can start.",
               ar=f"بايثون {named} كفاية، ومن غير أي مكتبة خارجية، ونافذة سطح المكتب تقدر تشتغل.")


def toolchain_row(*, arabic: bool) -> dict:
    installed = runner.available()
    labels = [runner.RECIPES[name]["label"] for name in installed]
    others = [name for name in installed if name not in ("python-unittest", "python-pytest")]
    listed = ", ".join(labels) or "none"
    if not others:
        return row("toolchain", WARN, arabic=arabic,
                   en=f"Commands this machine can run: {listed}.",
                   ar=f"أوامر الجهاز ده يقدر يشغلها: {listed}.",
                   advice_en="A Java, Node, Go or Rust folder will show no command until its own tool "
                             "is on PATH.",
                   advice_ar="مجلد Java أو Node أو Go أو Rust هيفضل من غير أمر لحد ما أداة نفسها تبقى "
                             "على الـ PATH.")
    return row("toolchain", OK, arabic=arabic,
               en=f"Commands this machine can run: {listed}.",
               ar=f"أوامر الجهاز ده يقدر يشغلها: {listed}.")


def provider_rows(kind: config.Kind, endpoint: str, api_key: str | None, model: str,
                  *, arabic: bool, probe: bool = True) -> list[dict]:
    """Whether the provider answers, and whether anything is there to ask.

    The model row reads the *live* list only. A provider that ships fallback names answers "not
    confirmed by a live request", because a first run that promises a model nobody pulled fails on the
    first Send rather than here.

    `probe=False` builds the two rows without touching the network — the card a window shows before the
    operator asks for anything must not be the reason a request left the machine.
    """
    if not probe:
        return [row("provider", INFO, arabic=arabic, en="The provider has not been asked yet.",
                    ar="البروفايدر ما اتسألش لحد دلوقتي."),
                row("model", INFO, arabic=arabic, en="The model list has not been read yet.",
                    ar="قائمة الموديلات ما اتقريش لحد دلوقتي.",
                    advice_en="Run the checks to ask the provider what it has.",
                    advice_ar="شغّل الفحوص عشان تسأل البروفايدر عند إيه.")]
    label = kind.label
    try:
        checked = config.check_endpoint(kind, endpoint or kind.base)
    except AgentError as exc:
        return [row("provider", BAD, arabic=arabic, en=f"{label}: the endpoint is refused.",
                    ar=f"{label}: العنوان مرفوض.", advice_en=str(exc), advice_ar=str(exc)),
                row("model", INFO, arabic=arabic, en="No model was checked.",
                    ar="مفيش موديل اتلقات.")]
    entries, source, error = reach(kind, checked, api_key)
    if error:
        start = f"Cannot reach {label} at {checked}."
        pull_ar = f"مفيش اتصال بـ {label} على {checked}."
        if kind.key == config.OLLAMA.key:
            advice = ("Start it with `ollama serve`, check the port, or choose another provider.")
            advice_ar = "شغّله بـ `ollama serve`، أو اتأكد من البورت، أو اختار بروفايدر تاني."
        else:
            advice = (f"Start {label} and load a model in it, or choose another provider.")
            advice_ar = f"شغّل {label} وحمّل فيه موديل، أو اختار بروفايدر تاني."
        return [row("provider", BAD, arabic=arabic, en=start, ar=pull_ar,
                    advice_en=advice, advice_ar=advice_ar),
                row("model", INFO, arabic=arabic, en="No model list to choose from.",
                    ar="مفيش قائمة موديلات اتلقات منها.")]
    live = source == catalog.LIVE
    local = [entry for entry in entries if not entry.get("cloud")]
    rows = [row("provider", OK, arabic=arabic,
                en=f"{label} answers at {checked}.", ar=f"{label} بيجاوب على {checked}.")]
    if not entries:
        if kind.shape == "ollama":
            advice = ("Pull a model that fits this machine, for example "
                      "`ollama pull qwen2.5-coder:1.5b`.")
            advice_ar = ("حمّل موديل يناسب الجهاز ده، زي "
                         "`ollama pull qwen2.5-coder:1.5b`.")
        else:
            advice = f"Load a model in {label} first."
            advice_ar = f"حمّل موديل في {label} الأول."
        return rows + [row("model", BAD, arabic=arabic,
                           en=f"{label} is reachable but has no models.",
                           ar=f"{label} بيجاوب بس مفيهوش موديلات.",
                           advice_en=advice, advice_ar=advice_ar)]
    if not live:
        return rows + [row("model", WARN, arabic=arabic,
                           en=(f"{len(entries)} names came from this tool, not from {label}: the live "
                               "list could not be reached."),
                           ar=(f"{len(entries)} اسم من الأداة نفسها، مش من {label}: القائمة الحية "
                               "ما وصلتش."),
                       advice_en="Treat the first request as the test; a name may no longer exist.",
                       advice_ar="اعتبر أول طلب هو الاختبار؛ من الممكن اسم منها ما بقاش موجود.")]
    if not local:
        return rows + [row("model", WARN, arabic=arabic,
                           en=f"{len(entries)} models, and every one of them is billed or remote.",
                           ar=f"{len(entries)} موديل، وكلها مدفوعة أو على جهاز تاني.",
                           advice_en="A local model keeps the code on this device. Choosing a remote "
                                     "one needs the policy step approved first.",
                           advice_ar="الموديل المحلي بيخلي الكود على الجهاز ده. اختيار واحد بعيد محتاج "
                                     "موافقة على الخطوات الأول.")]
    chosen = model if any(entry["id"] == model for entry in entries) else local[0]["id"]
    return rows + [row("model", OK, arabic=arabic,
                       en=(f"{len(entries)} models ({len(local)} of them local). Using {chosen}."),
                       ar=f"{len(entries)} موديل ({len(local)} منهم محليين). هيستخدم {chosen}.")]


def project_row(repo: str, *, arabic: bool) -> dict:
    if not str(repo or "").strip():
        return row("project", INFO, arabic=arabic,
                   en="No folder has been granted yet.",
                   ar="مفيش مجلد اتفتح لحد دلوقتي.",
                   advice_en="Open one from the sidebar, or pass --repo here. Granting a folder is what "
                             "lets a task read it — nothing is read until you ask for a change.",
                   advice_ar="افتح واحد من الشريط الجانبي، أو حدد --repo هنا. منح المجلد هو اللي بيسيب "
                             "المهمة تقرأه — مفيش قراءة قبل ما تطلب تغيير.")
    path = Path(str(repo).strip())
    if not path.is_dir():
        return row("project", BAD, arabic=arabic, en=f"{path.name} is not a folder I can read.",
                   ar=f"{path.name} مش مجلد أقدر أقراه.",
                   advice_en="Choose an existing folder, or clear it to work without a project.",
                   advice_ar="اختار مجلد موجود، أو امسحه عشان تشتغل من غير مشروع.")
    try:
        targets = runner.targets(path)
    except (AgentError, OSError) as exc:
        return row("project", BAD, arabic=arabic, en=f"{path.name} could not be scanned.",
                   ar=f"{path.name} ما اتقريش.", advice_en=str(exc)[:160], advice_ar=str(exc)[:160])
    commands = "; ".join(f"{runner.RECIPES[name]['label']} in {row_['label']}"
                         for row_ in targets for name in row_["recipes"])
    git = git_integration.status(str(path))["repo"]
    if not targets:
        return row("project", WARN, arabic=arabic,
                   en=f"{path.name} is granted, but no runnable command was found in it.",
                   ar=f"{path.name} اتفتح، بس مفيش أمر قابل للتشغيل فيه.",
                   advice_en="The Checks card stays hidden and a run will be refused. Add the tool for "
                             "this kind of project, or use the static check.",
                   advice_ar="كارت الفحوص هيفضل مخفي والتشغيل هيترفض. ضيف أداة النوع ده من المشاريع، أو "
                             "استخدم الفحص الساكن.")
    return row("project", OK, arabic=arabic,
               en=f"{path.name}: {commands}" + (" · under git" if git else " · not a git folder"),
               ar=f"{path.name}: {commands}" + (" · تحت git" if git else " · مش مجلد git"))


def demo_row(result: dict, *, arabic: bool) -> dict:
    passed = result.get("proposal_apply_rollback") == "passed"
    return row("demo", OK if passed else BAD, arabic=arabic,
               en=("A proposal was applied, checked and rolled back in a temporary folder — no model "
                   "was asked and none of your files were touched." if passed else
                   "That proof did not complete: " + str(result.get("note", ""))[:120]),
               ar=("مقترح اتطبّق واتفحص واتراجع في مجلد مؤقت — مفيش موديل اتسأل ومفيش ملف من ملفاتك "
                   "اتلمس." if passed else
                   "الدليل ده ما كملش: " + str(result.get("note", ""))[:120]),
               advice_en="" if passed else "Report this as a bug; the tool refuses to write when its own "
                                          "proof does not hold.",
               advice_ar="" if passed else "بلّغ عن ده كبيغ؛ الأداة بترفض تكتب لما دليلها نفسه ما يمشيش.")


def policy_row(*, arabic: bool) -> dict:
    """The five promises, numbered so an operator can be asked which one they did not read.

    Each language reads its own half of `POLICY`: the pairs are (English, Arabic), and a row that
    interpolated the pair itself would print both languages in one line.
    """
    side = 1 if arabic else 0
    return row("policy", INFO, arabic=arabic,
               en="What this tool promises: " + " ".join(
                   f"({i + 1}) {line[0]}" for i, line in enumerate(POLICY)),
               ar="اللي الأداة دي بتلتزم بيه: " + " ".join(
                   f"({i + 1}) {line[side]}" for i, line in enumerate(POLICY)))


def position_row(*, arabic: bool) -> dict:
    names = " / ".join(intent.label(mode) for mode in intent.MODES)
    return row("position", INFO, arabic=arabic,
               en=(f"Choose what Send may become — {names}. Chat answers in prose, Read-only explains "
                   "a folder and builds no proposal, Change proposes a diff you review. Auto-Apply is a "
                   "switch on top of Change, not a fourth choice."),
               ar=(f"اختار الـ Send يقدر يبقى إيه — {names}. الدردشة تجيب بالنص، وضع القراءة فقط يشرح "
                   "المجلد وما يبنیش اقتراح، ووضع التعديل يقترح فروقا تراجعها. الكتابة التلقائية مفتاح "
                   "فوق وضع التعديل ومش اختيار رابع."))


def audit(*, repo: str = "", provider: str = "", endpoint: str = "", api_key: str | None = None,
          model: str = "", arabic: bool = False, demo: dict | None = None,
          probe: bool = True) -> list[dict]:
    """Every row a first run needs, in order.

    `demo` is passed in rather than run here: a report that creates a temporary project and rolls it
    back is the wizard's job when the operator asks for it, not a side effect of asking what is
    installed. `probe` decides whether the provider is asked, for the same reason.
    """
    kind = config.kind_for(provider) or config.DEFAULT_KIND
    rows = [runtime_row(arabic=arabic), toolchain_row(arabic=arabic)]
    rows += provider_rows(kind, endpoint, api_key, model, arabic=arabic, probe=probe)
    rows.append(project_row(repo, arabic=arabic))
    rows.append(demo_row(demo, arabic=arabic) if demo else
                row("demo", INFO, arabic=arabic,
                    en="The offline proof has not been run yet.",
                    ar="دليل الشغل من غير نت ما اتلقاش لحد دلوقتي.",
                    advice_en="Run it from the setup step: it writes only to a temporary folder.",
                    advice_ar="شغّله من خطوة الإعداد: بيكتب في مجلد مؤقت بس."))
    rows.append(policy_row(arabic=arabic))
    rows.append(position_row(arabic=arabic))
    return rows


def run_demo() -> dict:
    """The offline proof: propose, apply, check, roll back — in a folder that is not the user's.

    It lives here because the wizard in the terminal and the card in the window both have to be able to
    offer it, and neither should import the other. The engine is imported inside the call so asking
    "what is installed" does not load the planning machinery.
    """
    import json as _json
    import tempfile

    from .engine import apply_proposal, load_session, plan, rollback
    from .verification import verify
    from .workspace import Workspace

    class DemoProvider:
        model = "deterministic-demo-no-llm"
        turn = 0

        def generate(self, messages):
            self.turn += 1
            if self.turn == 1:
                return _json.dumps({"action": "read_file", "path": "calculator.py"})
            return _json.dumps({"action": "propose", "summary": "Fix addition in a synthetic fixture.",
                                "checks": ["Run addition tests in an isolated worker."],
                                "changes": [{"path": "calculator.py",
                                             "content": "def add(a, b):\n    return a + b\n"}]})

    with tempfile.TemporaryDirectory(prefix="ai-agent-demo-") as temp:
        root = Path(temp) / "repo"
        root.mkdir()
        (root / "calculator.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        settings = config.Settings()
        session_path = plan(Workspace(root), "Fix add", DemoProvider(), settings, Path(temp) / "runs",
                           progress=lambda _: None)
        proposal = load_session(session_path)
        apply_proposal(session_path, proposal["proposal_hash"])
        result = verify(session_path)
        changed = (root / "calculator.py").read_text() == "def add(a, b):\n    return a + b\n"
        rollback(session_path, proposal["proposal_hash"])
        restored = (root / "calculator.py").read_text() == "def add(a, b):\n    return a - b\n"
        return {"proposal_apply_rollback": "passed" if changed and restored else "failed",
                "static_checks": result, "llm_used": False,
                "note": "Synthetic demo only. No project code executed. No build/test verification claimed."}


# A status that only exists as a colour is invisible in a terminal and unreadable to a screen reader,
# so every rendering of a row carries the word as well.
MARK = {OK: "[ok]", WARN: "[!]", BAD: "[x]", INFO: "[i]"}


def render(rows: list[dict]) -> str:
    """The audit as text one line per fact, advice indented under the row it belongs to."""
    out = []
    for item in rows:
        out.append(f"{MARK.get(item['status'], '[?]')} {item['text']}")
        if item["advice"]:
            out.append("     -> " + item["advice"])
    return "\n".join(out)


def counts(rows: list[dict]) -> dict:
    """How many rows say what. A first run that ends in three red lines is not a success."""
    out = {status: 0 for status in (OK, WARN, BAD, INFO)}
    for row_ in rows:
        out[row_["status"]] = out.get(row_["status"], 0) + 1
    return out


def first_run(app_dir: Path) -> bool:
    """Whether this machine has ever granted a folder.

    Deliberately about the registry and nothing else: a person who has projects listed does not want a
    wizard, and one who has never named a folder has no way to know where to start.
    """
    try:
        import json
        data = json.loads((Path(app_dir) / ".agent-projects.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    return not data.get("projects")


def needs_policy(rows: list[dict]) -> bool:
    """Whether the policy step has to be answered before a remote model may be chosen."""
    return any(item["id"] == "model" and item["status"] == WARN and "remote" in item["text"]
               for item in rows)
