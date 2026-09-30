"""The write-intent axis: three positions, and what each one refuses.

`Chat` answers in prose and reads the folder as context. `Read-only` does the same but promises the
stronger thing: nothing is proposed, nothing is written, and a project command runs only after a clear
yes for that command. `Change` proposes a diff for review — and the per-folder Auto-Apply switch sits
on top of it, which is why the switch is not a fourth position here.

Both windows ask this module instead of each holding its own rule. Every sentence is written once, in
two languages: the last time two surfaces worded "this conversation cannot write" separately, one of
them was refusing a different thing than the other.
"""
from __future__ import annotations

from .labels import say

CHAT, READ, CHANGE = "chat", "read", "change"
MODES = (CHAT, READ, CHANGE)

# What the badge and the picker call each position.
LABELS = {CHAT: "Chat", READ: "Read-only", CHANGE: "Change"}


def normalise(value) -> str:
    """The mode this value names, or chat for anything unrecognised.

    A stored preference from an older version, or a payload with a typo, cannot leave a window in a
    state where neither promise holds. Chat is the safe reading: it writes nothing.
    """
    text = str(value or "").strip().lower()
    return text if text in MODES else CHAT


def read_only(mode) -> bool:
    return normalise(mode) == READ


def label(mode) -> str:
    return LABELS[normalise(mode)]


# What the header line of each window prints for the position: the name, then the promise it keeps.
PROMISE = {CHAT: "reads as context", READ: "writes nothing", CHANGE: "reviewed diff"}


def subtitle(mode) -> str:
    """`chat · reads as context` — the mode in the header, in the mode's own words."""
    mode = normalise(mode)
    return LABELS[mode].lower() + " · " + PROMISE[mode]


def needs_folder(mode, *, arabic: bool = False) -> str:
    """Why two of the three positions cannot be chosen over an empty window."""
    mode = normalise(mode)
    if mode == CHANGE:
        return say(arabic,
                   en="Choose a project in the sidebar before asking for reviewed changes.",
                   ar="اختر مشروعا في الشريط الجانبي قبل طلب فروق تراجعها.")
    return say(arabic,
               en="Choose a project in the sidebar before asking for a read-only analysis.",
               ar="اختر مشروعا في الشريط الجانبي قبل طلب فحص للقراءة فقط.")


# ------------------------------------------------------------------ the refusals

def switched(mode, *, arabic: bool = False, project: str = "") -> str:
    """What choosing this position means, said as the window's own next line."""
    suffix = f" ({project})" if project else ""
    mode = normalise(mode)
    if mode == READ:
        return say(arabic,
                   en=("Read-only: I read this folder, search it, map it and explain what I find. I create "
                       "no proposal and write nothing, and your project's own command runs only when you "
                       "say yes to that one command." + suffix),
                   ar=("وضع القراءة فقط: أقرأ هذا المجلد وأفتشه وأبني خريطته وأشرح ما أجد فيه. لا أنشئ أي "
                       "اقتراح ولا أكتب أي شيء، وأمر المشروع نفسه لا يعمل إلا عندما توافق على ذلك الأمر "
                       "بعينه." + suffix))
    if mode == CHANGE:
        return say(arabic,
                   en="Change mode: Send proposes a diff you review before any file is written." + suffix,
                   ar="وضع التعديل: الإرسال يقترح فروقا تراجعها قبل كتابة أي ملف." + suffix)
    return say(arabic,
               en="Chat mode: Send answers in prose and cannot write files.",
               ar="وضع الدردشة: الإرسال يجيب بالنص ولا يكتب ملفات.")


def unchecked(*, arabic: bool = False) -> str:
    """Read-only switched back off, in the window that holds it as a switch rather than a badge."""
    return say(arabic,
               en="Read-only is off. Send proposes a diff you review before any file is written.",
               ar="وضع القراءة فقط مطفأ. الإرسال يقترح فروقا تراجعها قبل كتابة أي ملف.")


def answered(*, arabic: bool = False) -> str:
    """Where a read-only answer ends. Both windows close an analysis with this, because the sentence
    before it told the operator to switch modes after every single question."""
    return say(arabic,
               en="Answered in Read-only mode. Nothing was written.",
               ar="أجبت في وضع القراءة فقط. لم يُكتب شيء.")


def no_proposal(*, arabic: bool = False) -> str:
    """Why a message that asks for a change gets an explanation instead of a diff."""
    return say(arabic,
               en=("This conversation is in Read-only mode, so there is no proposal to build. Ask what is "
                   "wrong, where it is, and what a fix would touch — and switch the badge to Change when you "
                   "want a diff to review."),
               ar=("هذه المحادثة في وضع القراءة فقط، لذلك لا يُبنى أي اقتراح. اسألني ما الخطأ وأين وماذا "
                   "سيلمس الإصلاح، وحوّل الشارة إلى وضع التعديل عندما تريد فروقا تراجعها."))


def no_write(what: str = "Apply", *, arabic: bool = False) -> str:
    """Apply and Roll back both change files, so both stop here."""
    return say(arabic,
               en=(f"{what} writes to the folder, and this conversation is in Read-only mode. Switch to "
                   "Change mode to review and write the diff."),
               ar=(f"{what} يكتب في المجلد، والمحادثة في وضع القراءة فقط. حوّلها إلى وضع التعديل لترى "
                   "الفروق وتكتبها."))


def no_fix_round(*, arabic: bool = False) -> str:
    """"Run & fix" spends a round on a proposal, which is the one thing this mode will not make."""
    return say(arabic,
               en=("Read-only runs the command when you say yes, but it does not run a fix round: the output "
                   "of a round is a proposal. The failure and its whole output are above — ask what they mean."),
               ar=("وضع القراءة فقط يشغّل الأمر عندما توافق، لكنه لا ينفّذ جولة إصلاح، لأن ناتج الجولة "
                   "اقتراح. رسالة الفشل ومخرجها الكامل بالأعلى؛ اسأل عن معناهما."))


def no_auto_apply(*, arabic: bool = False) -> str:
    return say(arabic,
               en=("A read-only conversation cannot arm Auto-Apply: the switch writes what the model "
                   "proposes without a click. Turn it on while the folder is in Change mode."),
               ar=("لا تستطيع محادثة في وضع القراءة فقط أن تفعّل الكتابة التلقائية: المفتاح يكتب ما "
                   "يقترحه النموذج دون نقرة. فعّله والمجلد في وضع التعديل."))


# ------------------------------------------------------------------ the one permission

def run_ask(command: str, *, arabic: bool = False, project: str = "") -> str:
    """The explicit yes this mode requires before a project's own code executes.

    Named by command, because "run something?" is not a choice anyone can predict the outcome of, and
    this is the sentence an operator reads before a build starts inside a production checkout.
    """
    where = f" في {project}" if project else ""
    return say(arabic,
               en=("This folder is in Read-only mode, which runs nothing until asked. Running this executes "
                   f"the project's own code with your permissions{f' in {project}' if project else ''}:\n\n"
                   f"  {command}\n\nIt cannot write to your files — that stays refused — but it can read "
                   "them, and a build runs whatever its scripts do."),
               ar=("هذا المجلد في وضع القراءة فقط، أي أنه لا يشغّل شيئا قبل أن يُسأل. التشغيل هنا ينفّذ كود "
                   f"المشروع نفسه بصلاحياتك{where}:\n\n  {command}\n\nلن يكتب في ملفاتك، هذا يبقى مرفوضا، "
                   "لكنه قد يقرأها، والبناء ينفّذ أي شيء تفعله سكربتاته."))


def run_declined(*, arabic: bool = False) -> str:
    return say(arabic,
               en="Nothing ran. Read-only starts no command until you say yes to that one.",
               ar="لم يُنفَّذ شيء. وضع القراءة فقط لا يشغّل أمرا إلا بموافقتك عليه.")


# ------------------------------------------------------------------ the durable declaration

# Which surface wrote the declaration. Stored as a code, spoken in either language, because a row that
# reads "the command line" in an Arabic window is half a sentence.
SOURCES = {"web": ("the web window", "نافذة الويب"),
           "gui": ("the desktop window", "نافذة سطح المكتب"),
           "cli": ("the command line", "سطر الأوامر"),
           "saved": ("a window before this one", "نافذة قبل هذه")}


def source(code: str, *, arabic: bool = False) -> str:
    """Who set this folder, in the language the rest of the line is in."""
    pair = SOURCES.get(str(code or ""))
    return (pair[1] if arabic else pair[0]) if pair else str(code or "")


def declared(by: str = "", at: str = "", *, arabic: bool = False, project: str = "") -> str:
    """Why the write stopped at a fact about the folder rather than at this window's badge.

    Names the surface and the minute, because "refused" without a who and a when reads like a bug, and
    the operator's next move is to find whoever set it.
    """
    who, when = source(by, arabic=arabic), str(at or "")
    bits = [part for part in (who, when) if part]
    tail = " (" + " · ".join(bits) + ")" if bits else ""
    where = f" ({project})" if project else ""
    if arabic:
        return ("المجلد" + where + " معلَن أنه للقراءة فقط" + tail +
                ". لا سطح من سطوح هذه الأداة يكتب فيه طالما الإعلان قائم: لا اقتراح، ولا تنفيذ ولا "
                "تراجع، ولا جولة إصلاح، ولا فرع أو استرجاع في git.")
    return ("This folder" + where + " is declared Read-only" + tail +
            ". No surface of this tool writes it while that stands: not a proposal, not Apply or Roll "
            "back, not a fix round, and not a git branch or a restore.")


def lift(folder: str, *, arabic: bool = False) -> str:
    """How to end the declaration, said as the exact line to type.

    A refusal that does not name its own remedy teaches the operator to go looking in a config file for
    a switch that is not there, and the next one gets set by accident.
    """
    return say(arabic,
               en=("To lift it, run:  agent read-only --off \"" + str(folder) + "\"  — or set the badge "
                   "to Change in a window. Lifting is asked for by name on purpose: nothing here lifts "
                   "itself."),
               ar=("للرفع، شغّل:  agent read-only --off \"" + str(folder) +
                   "\"  أو حوّل الشارة إلى وضع التعديل من أي نافذة. الرفع يُطلب بالاسم عمدًا، ولا شيء "
                   "هنا يرفع نفسه."))


def followed(by: str = "", at: str = "", *, arabic: bool = False) -> str:
    """The line a window prints when the folder it is showing was declared elsewhere after it opened."""
    who = source(by, arabic=arabic)
    tail = (" (" + who + " · " + str(at or "") + ")") if at else (" (" + who + ")" if who else "")
    return say(arabic,
               en=("The badge follows the declaration, not the other way round: this folder was set "
                   "Read-only" + tail + " after this window opened it. Every write is refused from here "
                   "on, and choosing Change is what lifts the declaration."),
               ar=("الشارة تتبع الإعلان وليس العكس: المجلد أُعلِن أنه للقراءة فقط" + tail +
                   " بعد أن فتحته هذه النافذة. كل كتابة مرفوضة من هنا فصاعدًا، واختيار وضع التعديل هو "
                   "ما يرفع الإعلان."))
