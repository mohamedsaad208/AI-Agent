"""The friendly shape of a failure, for the CLI (Release 4 - Task 4.2).

An error has earned its place on screen when a person can read three things off it: what went
wrong, why it went wrong, and what the agent did about it. A traceback answers none of those
for anyone who did not write the code, and burying the three answers inside it is how a failure
turns into a shrug. This module turns a failure into that structure — Problem / Root cause /
Agent action — and keeps the traceback behind ``details``: it is the evidence a bug report
needs, not the first thing an operator has to skip past.

The Problem line is not rewritten here. ``labels.friendly_error`` has carried this tool's
advice sentence for every known failure for several releases, and the web window, the Tk window
and the status strip all read that one voice; a second copy of the sentences here would be a
second voice to drift from the first. What this module adds is the missing halves — the root
cause and the agent's action — chosen by the same marker the advice matched on, so the three
lines always describe one and the same failure.

Errors the core itself broadcasts (``AgentProgressError`` and the ``error`` event) arrive with
those fields already filled, and what they carry wins: where the runtime names its own root
cause, guessing from a marker table here would contradict the component that actually saw the
failure. The traceback is captured only when the exception was really raised — a constructed
``AgentError`` has no frames, and inventing none is the honest answer — and it is redacted
before storage like every other piece of text that reaches a terminal.
"""
from __future__ import annotations

import dataclasses
import traceback as _traceback
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from rich.console import Console, Group
from rich.text import Text

from .cli_view import _ensure_utf8_stdout
from .diff_view import _visible
from .errors import AgentError, Cancelled
from .events import AgentProgressError, Event, EventKind
from .labels import friendly_error, is_arabic, say
from .redaction import redact


# ---------------------------------------------------------------------------
# The three headings, and the hint that points at what is hidden.
# ---------------------------------------------------------------------------

HEADINGS = {
    "root_cause": ("Root cause", "السبب الجذري"),
    "agent_action": ("Agent action", "إجراء الوكيل"),
    "traceback": ("Full traceback", "التتبع الكامل"),
    "details_hint": ("Full detail is hidden — answer `details` (d), or rerun with --details, to see it.",
                     "التفاصيل الكاملة مخفية — أجب `details` (d) أو أعد التشغيل مع --details لعرضها."),
}


def heading(key: str, arabic: bool) -> str:
    english, arabic_text = HEADINGS[key]
    return say(arabic, en=english, ar=arabic_text)


# ---------------------------------------------------------------------------
# The categories. Each row answers the same two questions the advice sentence cannot:
# why did this happen, and what did the agent do about it. The markers are the ones
# `labels.friendly_error` matches on, in its order, so the Problem line and this pair
# are guaranteed to describe the same branch of the same failure.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Category:
    key: str
    markers: tuple[str, ...]
    cause: tuple[str, str]      # (english, arabic)
    action: tuple[str, str]


CATEGORIES = (
    Category("timeout", ("request timeout",),
             ("The model needed longer to answer than this run's request timeout allows.",
              "احتاج النموذج وقتًا أطول من مهلة الطلب المحددة لهذا التشغيل."),
             ("The run stopped before any answer arrived and no file was written. Raise the "
              "timeout in Project & model settings, or ask for a smaller change.",
              "توقف التشغيل قبل وصول أي رد ولم يُكتب أي ملف. ارفع المهلة في إعدادات المشروع "
              "والنموذج، أو اطلب تغييرًا أصغر.")),
    Category("connection", ("connection failed", "timed out"),
             ("The model service never answered: Ollama is not running, the endpoint is wrong, "
              "or the selected model is not installed.",
              "لم يردّ خدمة النموذج إطلاقًا: Ollama لا يعمل، أو العنوان خاطئ، أو الموديل المختار "
              "غير مثبّت."),
             ("No request reached a model, so nothing in the project changed. Start the service, "
              "check the model name, and send the task again.",
              "لم يصل أي طلب إلى نموذج، لذلك لم يتغير شيء في المشروع. شغّل الخدمة وتحقق من اسم "
              "الموديل ثم أرسل المهمة من جديد.")),
    Category("later_edit", ("changed since", "overwrite a later edit"),
             ("The files on disk changed after this proposal was reviewed, and writing it would "
              "have erased the newer edit.",
              "تغيّرت الملفات على القرص بعد مراجعة هذا المقترح، وكتابته كانت ستَمحو التعديل الأحدث."),
             ("The write was refused and the newer edits stand untouched. Ask for a new proposal — "
              "it is built from the files as they are now.",
              "رُفضت الكتابات وتعديلاتك الأحدث باقية كما هي. اطلب مقترحًا جديدًا — يُبنى من الملفات "
              "بصيغتها الحالية.")),
    Category("repetition", ("repeated the same action",),
             ("The model answered identically three times in a row, which almost always means the "
              "change already exists in the files or the task is too vague to act on.",
              "أجاب النموذج بالإجابة نفسها ثلاث مرات متتالية، وهذا يعني غالبًا أن التغيير موجود فعلًا "
              "في الملفات أو أن المهمة غامضة جدًا."),
             ("The runtime stopped the loop before it spent more turns, and nothing further was "
              "written. Open the file to check, or describe one concrete edit.",
              "أوقف المُشغِّل الحلقة قبل إضاعة دورات إضافية ولم يُكتب شيء آخر. افتح الملف للتحقق، "
              "أو صف تعديلًا ملموسًا واحدًا.")),
    Category("unchanged_file", ("unchanged file",),
             ("The message was read in Change mode but holds no file edit, so the model returned the "
              "file exactly as it stands.",
              "قُرئت الرسالة في وضع Change لكنها لا تحتوي تعديلًا، فأعاد النموذج الملف كما هو تمامًا."),
             ("Nothing was proposed and no file was written. Switch the badge next to Send to Chat "
              "mode to ask freely, or name one concrete edit to stay in Change mode.",
              "لم يُقترح شيء ولم تُكتب أي ملفات. حوّل الشارة بجوار Send إلى وضع Chat للسؤال بحرية، "
              "أو سمِّ تعديلًا واحدًا ملموسًا للبقاء في وضع Change.")),
    Category("no_proposal", ("could not produce a proposal",),
             ("The model's answer held no valid proposal — smaller models often lose the task's shape "
              "part-way through the answer.",
              "لم يتضمّن رد النموذج مقترحًا صالحًا — النماذج الأصغر تفقد شكل المهمة غالبًا في منتصف "
              "الإجابة."),
             ("The run stopped at that step with the project files untouched. Try a larger model, or "
              "break the task into smaller steps.",
              "توقف التشغيل عند تلك الخطوة وملفات المشروع كما هي. جرّب موديلًا أكبر، أو قسّم المهمة "
              "إلى خطوات أصغر.")),
    Category("budget", ("without progress", "budget"),
             ("The run spent its whole turn budget before the task reached a finished proposal.",
              "استهلك التشغيل كامل ميزانية الدورات قبل أن تصل المهمة إلى مقترح مكتمل."),
             ("It stopped at the budget line instead of guessing at a finish. Ask for a smaller task "
              "or try another model.",
              "توقف عند حد الميزانية بدل تخمين نهاية. اطلب مهمة أصغر أو جرّب موديلًا آخر.")),
    Category("truncated", ("output truncated",),
             ("The reply stopped at the model's output-token ceiling, so what arrived is half an "
              "answer.",
              "توقف الرد عند سقف الرموز الخارجة من النموذج، فوصل نصف إجابة فقط."),
             ("Nothing was proposed from a cut-off answer. Ask for one file or one step at a time, or "
              "raise the output limit in Settings.",
              "لم يُقترح شيء من إجابة مقطوعة. اطلب ملفًا واحدًا أو خطوة واحدة في كل مرة، أو ارفع حد "
              "الإخراج في الإعدادات.")),
    Category("missing_key", (" in your environment", "API_KEY"),
             ("The provider refused the request because no API key was set where it looks for one.",
              "رفض البروفايدر الطلب لأن مفتاح API غير مضبوط في المكان الذي يبحث فيه عنه."),
             ("No request left this machine. Enter the key in the key field or in the named "
              "environment variable, then send the task again.",
              "لم يخرج أي طلب من هذا الجهاز. اكتب المفتاح في خانة المفتاح أو في متغير البيئة المذكور، "
              "ثم أرسل المهمة من جديد.")),
    Category("denied", ("HTTP 401", "HTTP 403"),
             ("The provider answered, but it would not authorize this key for the request.",
              "أجاب البروفايدر، لكنه لم يسمح لهذا المفتاح بتنفيذ الطلب."),
             ("Nothing was changed. Check the key and its permissions in the provider's own console, "
              "then try again.",
              "لم يتغير شيء. راجع المفتاح وصلاحياته في لوحة تحكم البروفايدر نفسه، ثم أعد المحاولة.")),
    Category("rate_limit", ("HTTP 429",),
             ("The provider serves a fixed number of requests per period, and this one passed the "
              "line.",
              "البروفايدر يخدم عددًا محددًا من الطلبات في الفترة الواحدة، وهذا الطلب تجاوز الحد."),
             ("The run did not retry into the limit. Wait a moment, or select a local model.",
              "لم تُعَد المحاولة فوق الحد. انتظر قليلًا، أو اختر موديلًا محليًا.")),
    Category("cancelled", (),
             ("You asked the run to stop, and it stopped at the next safe boundary.",
              "طلبت إيقاف التشغيل، فتوقف عند أقرب حد آمن."),
             ("No project files were changed after the request. Send the task again when you want "
              "it run.",
              "لم تُغيَّر أي ملفات بعد هذا الطلب. أرسل المهمة مرة أخرى عندما تريد تشغيلها.")),
)

# The failure no marker claimed. Its advice sentence is friendly_error's last line, and its two
# halves say what they are: an unclassified stop, and where the evidence for a report lives.
UNKNOWN = Category("unknown", (),
                   ("This failure is not one the tool can classify yet; it arrived as "
                    "{error_type}.",
                    "هذا الفشل لا تستطيع الأداة تصنيفه بعد؛ وصل كـ {error_type}."),
                   ("Reopen the task and try again. If it repeats, the traceback behind `details` "
                    "is what a report needs.",
                    "أعد فتح المهمة وحاول مرة أخرى. إذا تكرر الأمر فالتتبع المخفي خلف `details` هو "
                    "ما يحتاجه التقرير."))

_BY_KEY = {row.key: row for row in CATEGORIES}


def classify(exc: BaseException) -> Category:
    """The category whose marker the message matches, in ``friendly_error``'s own order.

    The redacted text is what is matched, exactly as the advice sentence matches it — a secret in
    the message cannot change which branch both halves of the triple come from.
    """
    value = redact(str(exc))
    for row in CATEGORIES:
        if any(marker in value for marker in row.markers):
            return row
    if isinstance(exc, Cancelled):
        return _BY_KEY["cancelled"]
    return UNKNOWN


# ---------------------------------------------------------------------------
# The view model.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FriendlyError:
    """One failure, said in the three parts a reader acts on, plus the evidence behind `details`."""

    problem: str
    root_cause: str = ""
    agent_action: str = ""
    error_type: str = "error"
    recoverable: bool = True
    arabic: bool = False
    trace: str = ""

    @property
    def has_details(self) -> bool:
        """Whether anything is actually behind the hint — a row that opens onto nothing teaches
        the reader to stop opening rows."""
        return bool(self.trace)

    def to_event(self) -> AgentProgressError:
        """The structured ``error`` event this formatter's answer already is in the schema."""
        return AgentProgressError(error_type=self.error_type, message=self.problem,
                                  root_cause=self.root_cause, suggested_action=self.agent_action,
                                  recoverable=self.recoverable)


def capture(exc: BaseException) -> str:
    """The whole traceback of a raised exception, redacted; empty when it was never raised."""
    if exc.__traceback__ is None:
        return ""
    return redact("".join(_traceback.format_exception(type(exc), exc, exc.__traceback__)))


def _pair(row: Category, error_type: str, arabic: bool) -> tuple[str, str]:
    fields = {"error_type": error_type}
    return (say(arabic, en=row.cause[0], ar=row.cause[1]).format(**fields),
            say(arabic, en=row.action[0], ar=row.action[1]).format(**fields))


def _from_exception(exc: BaseException, *, arabic: Optional[bool], with_trace: bool) -> FriendlyError:
    if arabic is None:
        arabic = is_arabic(str(exc))
    row = classify(exc)
    error_type = type(exc).__name__ if row is UNKNOWN else row.key
    cause, action = _pair(row, type(exc).__name__, arabic)
    # The advice sentence, from its one home — including for the unclassified, whose problem line
    # is the exception text itself.
    problem = friendly_error(exc) or str(exc) or error_type
    return FriendlyError(problem=problem, root_cause=cause, agent_action=action,
                         error_type=error_type, arabic=arabic,
                         trace=capture(exc) if with_trace else "")


def _from_mapping(data: Mapping[str, Any], *, arabic: Optional[bool]) -> FriendlyError:
    """An `error` event, a wire dict, or the AgentProgressError the core broadcast.

    What the core named wins; the marker table only fills the blanks it left.
    """
    message = str(data.get("message") or "")
    if arabic is None:
        arabic = is_arabic(message)
    row = classify(AgentError(message))
    # The schema's own default name is not a diagnosis: where the core only says "error",
    # the marker that classifies the sentence names the failure instead.
    named = str(data.get("error_type") or "")
    error_type = named if named and named != "error" else row.key
    cause = str(data.get("root_cause") or "") or _pair(row, error_type, arabic)[0]
    action = str(data.get("suggested_action") or data.get("next_action") or "") \
        or _pair(row, error_type, arabic)[1]
    problem = friendly_error(AgentError(message)) if message else error_type
    return FriendlyError(problem=problem, root_cause=cause, agent_action=action,
                         error_type=error_type, recoverable=bool(data.get("recoverable", True)),
                         arabic=arabic,
                         trace=redact(str(data.get("traceback") or data.get("trace") or "")))


def format_error(source: Any, *, arabic: Optional[bool] = None,
                 with_traceback: bool = True) -> FriendlyError:
    """The three-part answer, from a raised exception, the core's error event, or raw text."""
    if isinstance(source, FriendlyError):
        return source
    if isinstance(source, BaseException):
        return _from_exception(source, arabic=arabic, with_trace=with_traceback)
    if isinstance(source, AgentProgressError):
        return _from_mapping(dataclasses.asdict(source), arabic=arabic)
    if isinstance(source, Event):
        if source.kind != EventKind.ERROR.value:
            raise TypeError("not an error event: " + source.kind)
        return _from_mapping(source.data, arabic=arabic)
    if isinstance(source, Mapping):
        if str(source.get("kind") or "") == EventKind.ERROR.value or "message" in source:
            return _from_mapping(source, arabic=arabic)
        raise TypeError("no error to format in this mapping")
    if isinstance(source, str):
        return _from_exception(AgentError(source), arabic=arabic, with_trace=False)
    raise TypeError("no error to format in " + type(source).__name__)


# ---------------------------------------------------------------------------
# The renderables.
# ---------------------------------------------------------------------------

def _shown(text: str) -> str:
    # diff_view's sanitizer answers for one line and treats a newline as noise, which would
    # fold a traceback into one long unreadable row; the text here is multi-line by nature,
    # so the rule is applied per line and the breaks are kept.
    return "\n".join(_visible(line) for line in str(text).splitlines())


def renderable(err: FriendlyError, *, details: bool = False) -> Group:
    """The three lines, the hint (or the detail it hides), as one block.

    The first line opens with ``Error:`` because that is what the terminal has printed for a
    refusal since the first release: the tests, and the habit of everyone who reads the
    output, are built on that prefix, and the friendly structure is added underneath it
    rather than traded for it.
    """
    lines: list[Text] = []
    head = Text()
    head.append("Error: ", style="bold red")
    head.append(_shown(err.problem))
    lines.append(head)
    if err.root_cause:
        lines.append(Text.assemble((heading("root_cause", err.arabic) + ": ", "bold"),
                                   _shown(err.root_cause)))
    if err.agent_action:
        lines.append(Text.assemble((heading("agent_action", err.arabic) + ": ", "bold"),
                                   _shown(err.agent_action)))
    if err.has_details:
        if details:
            lines.append(Text(heading("traceback", err.arabic) + ":", style="bold dim"))
            lines.append(Text(_shown(err.trace), style="dim"))
        else:
            lines.append(Text(heading("details_hint", err.arabic), style="dim"))
    return Group(*lines)


def renderables(err: FriendlyError, *, details: bool = False) -> list:
    """The blocks the view prints, in order, without printing them."""
    return [renderable(err, details=details)]


def render(source: Any, console: Optional[Console] = None, *, arabic: Optional[bool] = None,
           details: bool = False) -> FriendlyError:
    """Print the three-part error, with the traceback only when `details` asks for it."""
    err = format_error(source, arabic=arabic)
    if console is None:
        _ensure_utf8_stdout()   # the symbol glyphs need it, as they do for the diff view
    # soft_wrap: an error line is grepped and asserted upon whole, and a wrap that splits
    # "agent read-only --off" across two columns breaks the sentence it was meant to keep.
    out = console or Console(soft_wrap=True)
    for block in renderables(err, details=details):
        out.print(block)
    return err
