"""State vocabulary and user-facing error text, shared by the Tk window and the web UI.

These live outside ``gui`` so the web controller never imports tkinter just to name a
state. The window re-exports them, so existing call sites and tests keep working.

The second half of this file is the language rule. The model is already told to answer in
the language it was asked in (``chat.py`` LANGUAGE_RULE, ``prompts.py`` SYSTEM), which covers
its own prose; the sentences *we* generate were English-only. They are chosen here so the
rule has one home rather than a conditional at every call site.
"""
from __future__ import annotations

import re

from .errors import AgentError, Cancelled
from .redaction import redact

# The whole Arabic block, not just its letters: a task written with digits, paths or punctuation
# still counts, and the diacritics are inside the range too. Written as escapes so the intent is
# readable without a bidi-rendering editor.
ARABIC_SCRIPT = re.compile(r"[؀-ۿ]")


def is_arabic(text: str | None) -> bool:
    return bool(ARABIC_SCRIPT.search(text or ""))


def say(arabic: bool, *, en: str, ar: str) -> str:
    """Pick the wording for the language the user asked in.

    ``ar`` keeps code names, paths and identifiers in Latin script: this is the sentence around
    them, not the text inside them.
    """
    return ar if arabic else en


# One entry per engine state. The wording is deliberately a sentence the user can act
# on, not a status code echoed back.
STATES = {
    "DISCOVERING": "Exploring the project",
    "WAITING_APPROVAL": "Changes ready for review",
    "APPLIED_UNVERIFIED": "Applied — project tests have not run",
    "VERIFICATION_BLOCKED": "Verification incomplete",
    "VERIFICATION_FAILED": "Checks need attention",
    "CHECKS_PASSED": "Selected checks passed",
    "ROLLED_BACK": "Changes rolled back",
    "CANCELLED": "Task cancelled",
    "BLOCKED": "Task needs attention",
    "PARTIAL_APPLY": "Partially applied — review before continuing",
    "APPLYING": "Application interrupted — review the task",
}
# The same sentences for a task written in Arabic. Keyed by state, so a state that gains an
# English entry without one here still reads as its own name rather than as nothing.
STATES_AR = {
    "DISCOVERING": "أستكشف المشروع",
    "WAITING_APPROVAL": "التعديلات جاهزة للمراجعة",
    "APPLIED_UNVERIFIED": "تم التطبيق — اختبارات المشروع لم تُشغّل بعد",
    "VERIFICATION_BLOCKED": "التحقق غير مكتمل",
    "VERIFICATION_FAILED": "الفحوص تحتاج مراجعة",
    "CHECKS_PASSED": "الفحوص المختارة نجحت",
    "ROLLED_BACK": "تم التراجع عن التعديلات",
    "CANCELLED": "تم إلغاء المهمة",
    "BLOCKED": "المهمة تحتاج انتباهك",
    "PARTIAL_APPLY": "تم التطبيق جزئيًا — راجع قبل المتابعة",
    "APPLYING": "انقطع التطبيق — راجع المهمة",
}
# How far along the run is, which is a different question from what happened to it: the table above is the
# outcome and decides which buttons work, this one is the position in the workflow and decides what a person
# watching a long job reads. `core.STAGES` owns the codes and the order; this is only the wording.
STAGES = {
    "understand": "Understanding the task",
    "plan": "Planning the change",
    "implement": "Writing the change",
    "impact": "Checking what it touches",
    "review": "Waiting for your review",
    "approve": "Applying what you approved",
    "build_test": "Building and testing",
    "verify": "Verifying the result",
}
STAGES_AR = {
    "understand": "أفهم المهمة",
    "plan": "أخطط للتغيير",
    "implement": "أكتب التغيير",
    "impact": "أفحص ما يلمسه التغيير",
    "review": "في انتظار مراجعتك",
    "approve": "أنفّذ ما وافقت عليه",
    "build_test": "البناء والاختبار",
    "verify": "التحقق من النتيجة",
}

# States whose files exist on disk and can still be checked, run against, or rolled back.
MUTABLE_STATES = {"APPLIED_UNVERIFIED", "VERIFICATION_BLOCKED", "VERIFICATION_FAILED", "CHECKS_PASSED"}
# A task whose files are on disk without a passing command run. Starting the next task
# on top of these is how a half-checked change turns into two.
UNVERIFIED_STATES = {"APPLIED_UNVERIFIED", "VERIFICATION_FAILED", "PARTIAL_APPLY", "APPLYING"}
# An interrupted apply can leave a project that no longer matches any proposal, so the
# next task is worth a deliberate yes rather than a line of text the user might miss.
INTERRUPTED_STATES = {"PARTIAL_APPLY", "APPLYING"}

TONE = {"CHECKS_PASSED": "ok", "WAITING_APPROVAL": "warn", "APPLIED_UNVERIFIED": "warn",
        "VERIFICATION_BLOCKED": "warn", "VERIFICATION_FAILED": "bad", "PARTIAL_APPLY": "bad",
        "APPLYING": "bad", "ROLLED_BACK": "idle", "CANCELLED": "idle", "BLOCKED": "bad",
        "DISCOVERING": "idle"}


def state_label(value: str | None, *, arabic: bool = False) -> str:
    """The card's headline for an engine state.

    A state missing from the Arabic table falls back to its own name rather than to English, so
    a new engine state cannot arrive as a half-translated card.
    """
    if not value:
        return "No task open"
    if arabic:
        return STATES_AR.get(value, value)
    return STATES.get(value, value)


def stage_label(value: str | None, *, arabic: bool = False) -> str:
    """The line a window draws for how far along the run is.

    A code with no wording falls back to the code rather than to an invented sentence, and a run that has
    not recorded one says so — a blank strip reads as "finished" to everybody who sees it.
    """
    if not value:
        return "Nothing recorded yet" if not arabic else "لم يُسجَّل شيء بعد"
    table = STAGES_AR if arabic else STAGES
    return table.get(value, value)


def stage_line(value: str | None, *, arabic: bool = False,
               stage_order: tuple[str, ...] | None = None) -> str:
    """The whole sentence: which step this is, and how many there are.

    The count is part of the sentence rather than a decoration beside it because "Writing the change" alone
    does not tell a person whether four steps are left or none. A code the table does not know — an older
    record, a stage renamed in a later release — answers with the plain label instead of a step number that
    would be wrong.
    """
    # Callers that own the lifecycle pass its ordered codes; STAGES is only the wording map.
    codes = list(stage_order) if stage_order is not None else list(STAGES)
    if not value or value not in codes:
        return stage_label(value, arabic=arabic)
    return note("stage_line", arabic=arabic, number=codes.index(value) + 1, total=len(codes),
                where=stage_label(value, arabic=arabic))


# --------------------------- the write, in the language it was asked for ---------------------------
# These build whole sentences rather than fragments so the two windows and the two controllers
# cannot each invent their own wording for "the files are on disk". Tool text reaches the browser
# as plain text, not markdown, so no `**bold**` here.

def artifact_title(*, arabic: bool, count: int, project: str, written: bool) -> str:
    """The artifact card's title. Past tense once the write happened: this card used to read
    "N proposed file(s)" after an Auto-Apply write, which is the one moment that is untrue."""
    if not count:
        return say(arabic, en="No applicable proposal was created",
                   ar="لم يُنشَأ أي اقتراح قابل للتطبيق")
    if written:
        return say(arabic, en=f"{count} file(s) applied to {project} and saved to disk",
                   ar=f"تم تطبيق {count} ملف(ات) على {project} وحُفظت على القرص")
    return say(arabic, en=f"{count} proposed file(s) in {project}",
               ar=f"{count} ملف(ات) مقترحة في {project} وبانتظار موافقتك")


def artifact_card(state: str | None, *, arabic: bool, count: int, project: str,
                  summary: str = "", written: bool = False, has_project: bool = True,
                  rejected: bool = False) -> dict:
    """The whole right-hand card: headline, tone, title and detail in one language.

    Both controllers build this card, and `--fake` is the window a design is reviewed in, so the
    sentence lives here once rather than twice. `state` is the engine state name; `count` the
    files it touches; `written` says whether they are on disk yet.

    A declined proposal stays WAITING_APPROVAL, with its latest hash-scoped decision recorded
    in events. The shared engine enforces that decision until an explicit reopen for review.
    """
    if not state:
        return {"state": "No task open", "tone": "idle", "written": False,
                "title": say(arabic, en="No proposal yet", ar="لا يوجد اقتراح بعد"),
                "detail": say(arabic,
                              en="Choose a project to turn a request into reviewed file changes.",
                              ar="اختر مشروعًا لتحويل طلبك إلى تعديلات ملفات تراجعها قبل الكتابة.")
                if has_project else say(
                    arabic,
                    en="This chat has no project attached, so nothing is proposed.",
                    ar="هذا الحوار بلا مشروع مرتبط، لذلك لا يُقترح شيء.")}
    card = {"state": state_label(state, arabic=arabic), "tone": TONE.get(state, ""),
            # `written` travels with the card: the same file list reads as "saved to disk" after a
            # click and as "waiting for you" before it, and only this side knows which.
            "written": bool(count) and written,
            "title": artifact_title(arabic=arabic, count=count, project=project, written=written),
            # Stripped before the fallback test, because a model that answers with whitespace alone
            # used to leave the card with an empty detail line rather than the advice.
            "detail": (summary or "").strip()[:240] or say(
                arabic, en="Nothing to apply for this task.", ar="لا يوجد ما يُطبَّق في هذه المهمة.")}
    if rejected and count:
        card["state"] = say(arabic, en="Rejected", ar="رُفض")
        card["tone"] = "idle"
        card["written"] = False
        card["title"] = say(
            arabic,
            en=f"{count} file(s) this proposal would have changed in {project}; none did",
            ar=f"{count} ملف(ات) كان هذا المقترح يغيّرها في {project}؛ لم يتغيّر شيء")
    return card


def run_warning(*, arabic: bool) -> str:
    """The sentence that says what pressing Run actually does.

    It lived in `runner.py`'s module docstring and in a code comment, which is how a person who never
    opens a source file misses it: the command is the project's own build tool, run with this user's
    permissions in this folder. The argv allowlist and the stripped environment decide *which*
    program runs and what it inherits; they do not stop it doing whatever that program does. A build
    script is code, and code from a repository someone else wrote is being executed here.
    """
    return say(arabic,
               en="\u26a0\ufe0f Run and Check syntax execute this project's own build code, with your "
                  "permissions in this folder. The command is allowlisted and its environment is "
                  "stripped, but that is a limit, not a sandbox.",
               ar="\u26a0\ufe0f زرّا Run وCheck syntax ينفّذان كود البناء الخاص بالمشروع نفسه، "
                  "بصلاحياتك في هذا المجلد. الأمر من قائمة مسموحات والبيئة مُجرَّدة، "
                  "لكن هذا تقييد وليس صندوق معزول.")


def applied_note(*, arabic: bool, count: int) -> str:
    """The line on the card reminding the user that no Apply click was involved in this write.

    It names the disk, because "applied" alone was the wording this round set out to fix: the one
    sentence that follows an unattended write should not need interpreting.
    """
    return say(arabic,
               en=f"\u2705 Applied and saved to disk: {count} file(s), with no Apply click."
                  f" Roll back from this card.",
               ar=f"\u2705 تم التطبيق والحفظ على القرص: {count} ملف(ات) بلا ضغطة Apply."
                  f" التراجع من هذه البطاقة.")


# Who the window is answering, and how long the quotation is allowed to be. The phrases are whole
# noun phrases in both languages so the sentence around them never has to agree with them.
QUOTE_WHO = {
    "user": ("your earlier message", "رسالتك السابقة"),
    "assistant": ("the agent's earlier reply", "رد الوكيل السابق"),
    "tool": ("the tool's earlier notice", "إشعار البرنامج السابق"),
}
QUOTE_CHARS = 160


def quote_reference(arabic: bool, role: str, words: str) -> str:
    """The line that puts a quotation in front of the message that answers it.

    Quoted, attributed and bounded. The block says whose words they are rather than letting a sentence
    the agent wrote arrive in the prompt dressed as an instruction from the operator — the same rule
    the standing notes follow when they are labelled. 160 characters is the whole of it, because the
    reference is a pointer at an earlier row and that row is already in the conversation.
    """
    who = QUOTE_WHO.get(role, QUOTE_WHO["assistant"])[0 if not arabic else 1]
    shown = words[:QUOTE_CHARS] + ("…" if len(words) > QUOTE_CHARS else "")
    return say(arabic, en=f'> [In reference to {who}: "{shown}"]\n\n',
               ar=f'> [بالإشارة إلى {who}: "{shown}"]\n\n')


def asked_of(text: str) -> str:
    """The operator's own words, with any reference block taken back off.

    A display question only: the thread row and the task record keep the whole message the model read,
    and a card title that opens with a hundred and sixty quoted characters has stopped naming the
    request it belongs to.
    """
    lines = str(text or "").split("\n")
    while lines and lines[0].startswith("> [") and lines[0].endswith('"]'):
        lines.pop(0)
    return "\n".join(lines).lstrip()


def rejected_note(*, arabic: bool, count: int) -> str:
    """What the thread says after the operator declines a proposal.

    The work is kept in the task's record even though nothing was written, and the sentence says both
    halves: a refusal that reads like a crash would be the second time this window has made a
    model's authored code look lost.
    """
    return say(arabic,
               en=f"\U0001f6ab Proposal declined: {count} file(s) stay exactly as they are. Nothing "
                  "was written, and the change is kept in this task's record.",
               ar=f"\U0001f6ab رُفض المقترح: {count} ملف(ات) تبقى كما هي تمامًا. لم تُكتَب أي بيانات، "
                  "والتغيير محفوظ في سجل هذه المهمة.")


def write_notice(*, arabic: bool, count: int, summary: str = "", lines: int = 0,
                 total: int = 0, rewrote: bool = False) -> str:
    """What the thread says after Auto-Apply has already written.

    The model's summary rides along, because for an unattended write this is the first place the
    user reads what the model decided to do.

    `lines`/`total` say how much of the file that was already there survived. A task that named one
    line and replaced thirty is the shape that loses a `package` declaration, and the number is the
    only part of it that fits in a row the user actually reads.
    """
    parts = [say(arabic,
                 en=f"\u26a1 Auto-Apply saved {count} file(s) directly to the project, because this"
                    f" folder's switch is on.",
                 ar=f"\u26a1 تم حفظ وتطبيق {count} ملف(ات) مباشرة في المشروع لأن مفتاح هذا المجلد مُفعّل.")]
    if lines:
        if not total:
            parts.append(say(arabic, en=f"\U0001f5d2 {lines} line(s) written.",
                             ar=f"\U0001f5d2 كُتب {lines} سطرًا."))
        elif rewrote:
            parts.append(say(
                arabic,
                en=f"\U0001f5d2 {lines} line(s) changed — every one of the {total} lines the file "
                   f"already had was replaced, not only the lines the task named.",
                ar=f"\U0001f5d2 تم تعديل {lines} سطرًا — استُبدلت الأسطر {total} التي كانت في الملف "
                   f"وليس الأسطر التي ذكرتها المهمة فقط."))
        else:
            parts.append(say(arabic, en=f"\U0001f5d2 {lines} line(s) changed in a file of {total}.",
                             ar=f"\U0001f5d2 تم تعديل {lines} سطرًا في ملف من {total} سطرًا."))
    if (summary or "").strip():
        parts.append(say(arabic, en=f"\U0001f4c4 Task summary: {summary.strip()}",
                         ar=f"\U0001f4c4 ملخص التعديل: {summary.strip()}"))
    parts.append(say(arabic,
                     en="The files are on disk. Click any file above to inspect its diff, or use"
                        " Roll back to undo the whole task.",
                     ar="الملفات محفوظة على القرص. اضغط على أي ملف للمعاينة، أو استخدم التراجع للعودة."))
    return "\n\n".join(parts)


def checkpoint_note(*, arabic: bool, count: int, sha: str) -> str:
    """The git commit that covers a write. `sha` and `--no-verify` stay Latin in both languages."""
    files = say(arabic, en=f"{count} file(s)", ar=f"{count} ملف(ات)")
    return say(arabic,
               en=f"Git checkpoint {sha} covers the {files} this task wrote. The commit was made"
                  f" with --no-verify, so the repository's own hooks did not run; anything else in"
                  f" that folder stayed where it was.",
               ar=f"نقطة الحفظ في git برقم {sha} تغطي {files} التي كتبتها هذه المهمة. تم الالتزام"
                  f" بوسم --no-verify، لذلك لم تُشغَّل خطافات المستودع؛ وبقي كل شيء آخر في هذا"
                  f" المجلد كما هو.")


def no_checkpoint_note(*, arabic: bool, reason: str) -> str:
    """The reason comes from git itself and stays as it arrived — the wrapper is what translates."""
    return say(arabic, en=f"No git checkpoint: {reason}", ar=f"لا توجد نقطة حفظ في git: {reason}")


# Moving HEAD is the one git action this window can be asked to do that changes where *future*
# commits land, so all three sentences say what moved and what did not. A branch name is a code
# name: Latin in both languages, like a path or a command.
def branch_started(*, arabic: bool, branch: str, back: str = "") -> str:
    return say(arabic,
               en=f"🌿 Started git branch {branch}. This task's commits land there, and everything"
                  f" uncommitted in the working tree came with you"
                  + (f". The branch chip offers the way back to {back}." if back else "."),
               ar=f"🌿 تم إنشاء فرع git باسم {branch}. تعليقات هذه المهمة تُحفظ فيه، وكل ما لم"
                  f" يُعلَّق بعد في مجلد العمل انتقل معك"
                  + (f". الشريط يعيدك إلى {back}." if back else "."))


def branch_switched(*, arabic: bool, branch: str) -> str:
    return say(arabic,
               en=f"🌿 Back on git branch {branch}. Nothing was forced: files that were not"
                  f" committed stayed exactly where they were.",
               ar=f"🌿 عدت إلى فرع git باسم {branch}. لم يُجبَر شيء: الملفات التي لم تُعلَّق بقيت"
                  f" كما هي.")


def no_branch_note(*, arabic: bool, reason: str) -> str:
    """git's own refusal line is the useful half, so it is kept verbatim inside a translated wrapper."""
    return say(arabic, en=f"No branch change: {reason}",
               ar=f"لا تغيير في الفروع: {reason}")


# The restore offer only ever appears after the session's own rollback refused, so the sentences say
# what git can still do and what it will overwrite while doing it. A hash is a code name: Latin.
def restore_offer(*, arabic: bool, commit: str, count: int) -> str:
    """One sentence for the refusal *and* its remedy.

    `friendly_error` already answers this PolicyError with "create a new proposal", which is the right
    advice where git has no idea what the task wrote and the wrong advice here — two remedies in the
    thread for one fact is how this project keeps having to undo drift. So the row that appears when a
    git copy exists states the refusal itself rather than repeating the generic one.
    """
    files = say(arabic, en=f"{count} file(s)", ar=f"{count} ملف(ات)")
    return say(arabic,
               en=f"⚠️ Rollback refused: those files changed after this task wrote them. git can"
                  f" still put {files} back to commit {commit}. The button appears beside the branch"
                  f" chip: it replaces only the files this task wrote, and it overwrites whatever is"
                  f" in them now.",
               ar=f"⚠️ رفض التراجع: هذه الملفات تغيّرت بعد أن كتبتها هذه المهمة. ما زال بمقدور git"
                  f" أن يُرجع {files} إلى الكوميت {commit}. الزر يظهر بجوار شارة الفرع: يستبدل ملفات"
                  f" هذه المهمة فقط، ويكتب فوق ما فيها الآن.")


def restore_done(*, arabic: bool, commit: str, restored: list, skipped: list) -> str:
    files = say(arabic, en=f"{len(restored)} file(s)", ar=f"{len(restored)} ملف(ات)")
    text = say(arabic, en=f"⎇ Restored {files} from commit {commit}.",
               ar=f"⎇ تم إرجاع {files} من الكوميت {commit}.")
    if skipped:
        text += say(arabic,
                    en=" git had no copy of: " + ", ".join(skipped) + " — those files are unchanged.",
                    ar=" لم يجد git نسخة من: " + ", ".join(skipped) + " — بقيت كما هي.")
    return text


# One status sentence, one home. Twenty of these were written twice — once in `gui.py` and once in
# `webapp/controller.py` — which is the drift code-review item 16 counted with its "52 against 72".
# The English is byte-identical to what both windows already said, so the only thing a user can
# notice is that the fallback window can now answer in the language it was asked in.
STATUS_TEXTS = {
    "apply_first": ("Apply a reviewed proposal before running project commands.",
                    "طبّق المقترح بعد مراجعته قبل تشغيل أوامر المشروع."),
    "consent_message": ("Approve sending this message to the cloud service before using a cloud model.",
                        "اسمح بإرسال هذه الرسالة إلى الخدمة السحابية قبل استخدام موديل سحابي."),
    "consent_project": ("Approve sending this project to the cloud service before continuing.",
                        "اسمح بإرسال هذا المشروع إلى الخدمة السحابية قبل المتابعة."),
    "applied_rerun": ("Changes applied. Running the command again\u2026",
                      "تم التطبيق. يتم تشغيل الأمر مرة أخرى\u2026"),
    "applied_idle": ("Changes applied. You can check syntax or run the project's own command.",
                     "تم التطبيق. يمكنك فحص الصياغة أو تشغيل أمر المشروع نفسه."),
    "applied_no_command": ("Changes applied. Nothing ran afterwards: this folder has no runnable "
                           "command the tool can use.",
                           "تم التطبيق. لم يعمل شيء بعده: هذا المجلد لا يحتوي على أمر قابل "
                           "للتشغيل يعرفه البرنامج."),
    "need_folder_notes": ("Choose a project folder before saving notes.",
                          "اختر مجلد مشروع قبل حفظ الملاحظات."),
    "need_folder_exists": ("Choose an existing project folder, or clear it to chat without one.",
                           "اختر مجلد مشروع موجود، أو امسحه للتحدث بدون مشروع."),
    "registry_unsaved": ("Could not save the project list.",
                         "تعذّر حفظ قائمة المشاريع."),
    "fix_ready": ("Fix proposal ready. Review it and apply it \u2014 the command then runs again by itself.",
                  "مقترح الإصلاح جاهز. راجعه وطبّقه، وبعدها سيعمل الأمر من تلقاء نفسه."),
    "no_proposal": ("No applicable proposal was created. See the conversation for details.",
                    "لم يُنشأ مقترح صالح. راجع المحادثة للتفاصيل."),
    "ask_expired": ("That question waited too long and was withdrawn. Nothing was changed.",
                    "انتهى انتظار هذا السؤال فسُحب. لم يُغيَّر شيء."),
    "no_recipe": ("No runnable command was detected in this project folder.",
                  "لم يُعثر على أمر قابل للتشغيل داخل هذا المجلد."),
    "prior_unverified": ("Previous task is still unverified \u2014 see the conversation.",
                         "المهمة السابقة لم يتم التحقق منها بعد — راجع المحادثة."),
    "granted": ("Project folder granted: ",
                "تم منح الوصول إلى مجلد المشروع: "),
    "proposal_ready": ("Proposal ready. Review the changes, then apply them if you want.",
                       "المقترح جاهز. راجع التغييرات ثم طبّقها إن أردت."),
    "pick_model": ("Refresh models and select a model from the available list.",
                   "حدّث قائمة الموديلات واختر موديلًا منها."),
    "ledger_needs_look": ("Rolled back, but the plan ledger needs a look: ",
                          "تم التراجع، لكن سجل الخطة يحتاج مراجعة: "),
    "task_opened": ("Saved task opened.",
                    "تم فتح المهمة المحفوظة."),
    "rolled_back": ("Task changes rolled back.",
                    "تم التراجع عن تغييرات المهمة."),
    "command_passed": ("The command passed. ",
                       "نجح الأمر. "),
    "too_long": ("Type your message in up to 4,000 characters and select a model.",
                 "اكتب رسالتك في حدود 4,000 حرف واختر موديلًا."),
}

# The sentences only one window can truthfully say. A web question travels to the browser and the
# browser may simply never answer it, so the wait expires and the ask is withdrawn; Tk's messagebox
# holds the mainloop until it is answered, so it has no such moment to report.
# Sentences that belong to one window by construction, not by drift: the web window's ask has no Tk
# counterpart (a Tk dialog is modal), and neither has Auto-Apply, so the line that follows a write
# nobody clicked for -- and the folder that has no command to check it with -- can only appear there.
SINGLE_WINDOW_STATUS = ("ask_expired", "applied_no_command")


# The sentences one window used to write for itself. Each of these existed twice — in `gui.py` and in
# `webapp/controller.py` — and seven of them had already drifted: Tk said "click Send" where the web
# window said "press Send", the web note about saved project notes lost the half that explains *why*
# they are safe, and Tk's stop notice promised "no changes will be applied" that the web one stopped
# saying. The wording below is the more informative of the two, in both languages, from one place.
#
# `{field}` is filled by `note()`. A sentence that needs a field the caller forgot raises KeyError,
# because a half-formatted status line is the kind of bug nobody notices until it is on screen.
NOTE_TEMPLATES = {
    "proposal_reject_nothing": (
        "There is no proposal on this screen to decline.",
        "لا يوجد مقترح على هذه الشاشة لرفضه."),
    "proposal_reject_twice": (
        "This proposal is already declined. Nothing was written, and the next one will ask again.",
        "هذا المقترح مرفوض بالفعل. لم تُكتَب أي ملفات، والمقترح التالي سيسأل من جديد."),
    "plan_attached_chained": (
        "Plan attached. Send starts its first unfinished step; each step unlocks the next only after "
        "a command run proves it. The message box is an optional note.",
        "أُرفقت الخطة. الإرسال يبدأ أول خطوة غير منتهية، وكل خطوة تفتح التالية فقط بعد تشغيل أمر "
        "يُثبتها. صندوق الرسالة ملاحظة اختيارية."),
    "plan_attached_plain": ("Plan attached. Describe the phase, then press Send.",
                            "أُرفقت الخطة. صف المرحلة ثم اضغط Send."),
    "notes_saved": ("Project notes saved outside the project folder, so a proposal cannot rewrite them.",
                    "حُفظت ملاحظات المشروع خارج مجلد المشروع، لذا لا يستطيع أي مقترح إعادة كتابتها."),
    "notes_in_request": ("Your saved project notes ({count} characters) are part of this request.",
                         "ملاحظاتك المحفوظة عن المشروع ({count} حرفًا) جزء من هذا الطلب."),
    "new_chat_plain": ("New chat — it answers in prose and reads no project files. Choose a project "
                       "and it can read that folder too.",
                       "محادثة جديدة — تجيب بنثر ولا تقرأ ملفات مشروع. اختر مشروعًا ليُقرأ مجلده أيضًا."),
    "new_chat_project": ("New chat in {project} — Send answers in prose; the badge by Send switches "
                         "to reviewed changes.",
                         "محادثة جديدة في {project} — الإرسال يجيب بنثر، والشارة بجوار Send تحوّل إلى "
                         "التغييرات بعد المراجعة."),
    "sample_ready": ("Sample ready. Choose a model, then press Send.",
                     "المثال جاهز. اختر موديلًا ثم اضغط Send."),
    "chat_reopened_plain": ("Chat reopened — still no project attached.",
                            "أُعيد فتح المحادثة — لا يوجد مشروع مرفق بعد."),
    "chat_reopened_project": ("Chat reopened — it reads {project} as context.",
                              "أُعيد فتح المحادثة — تقرأ {project} كسياق."),
    "stop_requested": ("Stop requested. Waiting for the current model request to finish; no changes "
                       "will be applied.",
                       "طُلب الإيقاف. في انتظار انتهاء طلب الموديل الحالي، ولن تُطبَّق أي تغييرات."),
    "syntax_failed": ("Syntax check found a problem. Open the Checks tab for details.",
                      "فحص الصياغة وجد مشكلة. افتح تبويب Checks للتفاصيل."),
    # Both windows say this after a proposal lands, because both now keep the diff in a viewer of its
    # own rather than in a pane inside the conversation: the sentence has to name where to look.
    "review_here": ("Review the files in the proposal viewer, then press Apply to write them.",
                    "راجع الملفات في نافذة المقترح، ثم اضغط Apply لكتابتها."),
    # The four answers the sandbox switch gives, in the card where the switch sits. Both windows say
    # the same sentence because the same question is being asked at the same moment: right before Run.
    "sandbox_missing": ("Docker is not installed on this machine, so every command runs here.",
                        "Docker غير مثبّت على هذا الجهاز، لذلك تُشغَّل كل الأوامر هنا."),
    "sandbox_off": ("The command runs on this machine, inside the project folder.",
                    "يُشغَّل الأمر على هذا الجهاز داخل مجلد المشروع."),
    "sandbox_unpinned": ("Name a preloaded image as name@sha256:… — a tag can be retagged while a "
                         "build is running.",
                         "اكتب اسم صورة مُحمَّلة مسبقًا بالشكل name@sha256:… لأن الوسم يمكن تغييره "
                         "أثناء تشغيل البناء."),
    "sandbox_on": ("The command runs on a copy of the project, with no network and nothing written "
                   "back to your files.",
                   "يُشغَّل الأمر على نسخة من المشروع، بلا شبكة وبدون كتابة أي شيء في ملفاتك."),
    "syntax_clean": ("Syntax checks finished. Project tests have not run; verification remains "
                     "incomplete.",
                     "انتهى فحص الصياغة. اختبارات المشروع لم تُشغَّل، فالتحقق ما زال ناقصًا."),
    "step_open_detail": ("The command passed, but step {step} is not marked done: {reason}",
                         "نجح الأمر، لكن الخطوة {step} لم تُعلَّم كمنتهية: {reason}"),
    "step_still_open": ("Step {step} still open — see the conversation.",
                        "الخطوة {step} ما زالت مفتوحة — راجع المحادثة."),
    "step_reopened": ("Reopened plan step {step}: the files that passed are gone, so the step must be "
                      "implemented again.",
                      "أُعيد فتح الخطوة {step} من الخطة: الملفات التي نجحت زالت، لذا يجب تنفيذ الخطوة "
                      "من جديد."),
    # The goal tree of a plan is written by a model turn, so its two outcomes are both worth a sentence:
    # what the plan now carries, and why it carries nothing.
    "goal_written": ("The plan now carries its goal and {count} acceptance criteria; each step names the "
                     "criteria it answers.",
                     "الخطة الآن تحمل هدفها و{count} من معايير القبول؛ كل خطوة تذكر معايير القبول التي تجيب عنها."),
    "goal_skipped": ("This plan runs without a goal tree: {reason}",
                     "هذه الخطة تعمل بدون شجرة أهداف: {reason}"),
    # The verify button refuses, and the refusal has to say what is missing rather than repeat a
    # status code: the operator clicked because they believe the step is done.
    "step_no_proof": ("Plan step {step} stays open: {reason}. Run its command and let the result "
                      "prove it, or roll the step back.",
                      "الخطوة {step} من الخطة ستبقى مفتوحة: {reason}. شغّل أمرها ودع النتيجة تثبتها، "
                      "أو تراجع الخطوة."),
    # Said after the operator chooses to close a step the runtime could not prove. The row says so, and
    # the conversation says it in the same words, so nobody reads the ledger as a passing run later.
    "step_unproven": ("Plan step {step} was marked verified by you, not by a command run: the ledger "
                      "records it as unproven.",
                      "الخطوة {step} عُلِّمت كمنتهية بقرارك وليس بتشغيل أمر: السجل يسجّلها بدون إثبات."),
    # The three words the plan card needs to label the goal tree. They are sent in the snapshot rather
    # than written in the client, because the client cannot know which language the task was asked in.
    "plan_goal": ("Goal", "الهدف"),
    "plan_criteria": ("Acceptance criteria", "معايير القبول"),
    "plan_uncovered": ("no step answers this yet", "لا خطوة تجيب على هذا بعد"),
    "plan_unproven": ("marked done without a command run", "مُعلَّم كمنتهي بدون تشغيل أمر"),
    # The three words a computed criterion verdict can answer with, and the two reasons the middle one
    # gives. They are here rather than in the client because the client cannot know which language the task
    # was asked in, and the words are the verdict — a colour alone would leave "unproven" meaning anything.
    "verdict_verified": ("proved", "مثبتة"),
    "verdict_failed": ("failed", "فاشلة"),
    "verdict_unproven": ("not proved", "غير مثبتة"),
    "verdict_clicked": ("closed by a click, not by a run", "أُغلقت بنقرة لا بتشغيل أمر"),
    "verdict_not_run": ("no command run has answered it yet", "لم يُجب عنها أي أمر بعد"),
    "plan_verdicts_line": ("{proved} of {total} acceptance criteria are proved by a command run",
                           "{proved} من {total} من معايير القبول مثبتة بتشغيل أمر"),
    # A task that stopped mid-turn is a fact the operator has to decide about, so it is said and never
    # acted on: nothing here resumes a run by itself, which is the rule the request queue already lives
    # by (`restored: True` and a press of the play button).
    "resume_line": ("{count} task in this folder stopped before it reached a result. "
                    "Nothing resumed itself.",
                    "{count} مهمة في هذا المجلد توقفت قبل أن تصل إلى نتيجة. لم تُستأنف أي مهمة تلقائيًا."),
    "resume_gone": ("That task is no longer here — it was resumed, rolled back, or its folder moved.",
                    "هذه المهمة لم تعد موجودة: إما استُؤنفت أو تراجِع عنها أو نُقل المجلد الخاص بها."),
    "resume_busy": ("A task is running right now. Resume the stopped one after it finishes.",
                    "توجد مهمة قيد التشغيل الآن. استأنف المهمة المتوقفة بعد انتهائها."),
    "resume_started": ("Continuing the task that stopped: {task}",
                       "استكمال المهمة التي توقفت: {task}"),
    # Leading spaces are part of the text: these three are appended to `prior_write`, which ends in a
    # full stop. Keeping the space here is what lets both windows join the same two pieces.
    "apply_rerun_warning": ("\nIt will then run {label} again in that folder, which executes the "
                            "project's own build and test code.",
                            "\nسيُشغَّل {label} مرة أخرى في هذا المجلد بعد ذلك، وهذا ينفّذ كود البناء "
                            "والاختبار الخاص بالمشروع نفسه."),
    "prior_write": ("The last task here ('{task}') left files applied without a passing command run "
                    "({state}).",
                    "آخر مهمة هنا ('{task}') تركت ملفات مطبَّقة بدون تشغيل أمر ناجح ({state})."),
    "prior_continue": ("\n\nContinue with a new task anyway? Rolling back that task first is the "
                       "safer step.",
                       "\n\nهل تضيف مهمة جديدة على أي حال؟ الرجوع عن تلك المهمة أولًا هو الأأمن."),
    "prior_blocked": (" Roll it back or run its command, then start the new task.",
                      " تراجع عنها أو شغّل أمرها، ثم ابدأ المهمة الجديدة."),
    "prior_continued": (" Continuing on this state by your choice.",
                        " نكمل على هذه الحالة باختيارك."),
    "prior_stacked": (" Run its command (or roll it back) before stacking more changes on top of it.",
                      " شغّل أمرها أو تراجع عنها قبل تكديس تغييرات أخرى فوقها."),
    # ---- the change-impact block (#8). `impact.py` computes the structure; these are its sentences.
    "impact_heading": ("What this changes in the rest of the repository:",
                       "ما يغيّره هذا في بقية المستودع:"),
    "impact_removed": ("removes {names}", "يحذف {names}"),
    "impact_changed": ("changes the shape of {names}", "يغيّر شكل {names}"),
    "impact_callers": ("other files name {name} ({count}): {files}",
                       "ملفات أخرى تذكر {name} ({count}): {files}"),
    "impact_tests": ("test files name {name} ({count}): {files}",
                     "ملفات اختبار تذكر {name} ({count}): {files}"),
    "impact_route_gone": ("{route} ({handler}) would answer nothing",
                          "لن يردّ {route} ({handler}) على شيء"),
    "impact_route_new": ("{route} is added, handled by {handler}",
                         "أُضيف {route} ويعالجه {handler}"),
    "impact_modules": ("modules touched: {modules}", "الوحدات المتأثرة: {modules}"),
    "impact_unread": ("is not a language this tool reads as code",
                      "لغة لا تقرأها هذه الأداة ككود"),
    "impact_query": ("query text disappears from this file",
                     "نص استعلام يختفي من هذا الملف"),
    "impact_text_changed": ("query or configuration text changed in a file this tool does not read",
                            "نص استعلام أو إعداد تغيّر في ملف لا تقرأه هذه الأداة"),
    "impact_more": ("+{count} more", "+{count} أخرى"),
    "impact_listed_more": ("({count} more not listed)", "({count} غير مدرجة)"),
    "impact_unknown_heading": ("Not covered: ", "غير مغطى: "),
    "impact_unknown_no_index": ("nothing is indexed for this folder, so no caller could be checked",
                                "لا فهرس لهذا المجلد، لذا لم يتسن فحص أي ملف يستخدمها"),
    "impact_unknown_searched": ("callers were searched in {searched} of the {visible} other indexed files",
                                "تم البحث في {searched} من {visible} من الملفات المفهرسة الأخرى"),
    "impact_unknown_not_indexed": ("{path} is not in the index: its own declarations were compared, "
                                   "nothing that uses them",
                                   "{path} ليس في الفهرس: قورنت تعريفاته نفسها لا ما يستخدمها"),
    "impact_unknown_routes": ("{path} answers more routes than an index lists ({max} per file), "
                              "so a URL may be missing",
                              "{path} يردّ على مسارات أكثر مما يسجله الفهرس ({max} لكل ملف)، "
                              "لذا قد يكون هناك مسار ناقص"),
    "impact_unknown_unread": ("a changed file is in a language this tool does not parse; its content "
                              "was diffed, its users were not",
                              "أحد الملفات المعدلة بلغة لا تحللها هذه الأداة؛ قورن محتواها لا مستخدموها"),
    "impact_unknown_per_file": ("a name is written more than {limit} times in one file, so only its "
                                "first sites were counted",
                                "يُكتب اسم أكثر من {limit} مرة في ملف واحد، لذا حُسبت مواضعه الأولى فقط"),
    "impact_unknown_names": ("a file lost more declarations than are listed here ({max} per kind)",
                             "أحد الملفات فقد تعريفات أكثر مما هو مُدرج هنا ({max} لكل نوع)"),
    "impact_unknown_failed": ("the impact check could not run, so nothing was verified about other files",
                              "لم يستطيع فحص الأثر أن يعمل، لذا لم يُتحقق أي شيء بخصوص الملفات الأخرى"),
    # ---- the policy table (#4). `policy.py` answers with a class and a verdict, `permissions.py`
    # remembers the folder's own word; these are the sentences an operator reads when one of them
    # stands between a click and a command.
    "policy_ask_write_that_runs": ("This change edits {names}, a file this tool reads back as the command "
                                   "to run. Confirm to write it.",
                                   "هذا التعديل يغيّر {names}، وهو ملف تقرأ هذه الأداة منه الأمر الذي "
                                   "تنفّذه. أكّد للكتابة."),
    "policy_deny_write_that_runs": ("This folder was told never to let a change edit the file its commands "
                                    "come from ({names}).",
                                    "هذا المجلد أُمر ألا يعدّل أبدًا الملف الذي تُؤخذ منه أوامره ({names})."),
    "policy_ask_execute_custom": ("This command is not one of the project's own recipes, so it runs on your "
                                  "word alone. Confirm to run it once.",
                                  "هذا الأمر ليس من وصفات المشروع الجاهزة، لذا سينفّذ بناء على كلمتك وحدها. "
                                  "أكّد لتنفيذه مرة واحدة."),
    "policy_deny_execute_custom": ("A model asked for a shell command. Commands here come from you, not from "
                                   "the agent.",
                                   "أحد النماذج طلب أمر نظام. الأوامر هنا صادرة منك، لا من الوكيل."),
    "policy_ask_network": ("This address is outside the local ranges the provider rules already accept. "
                            "Confirm to send the request once.",
                            "هذا العنوان خارج النطاقات المحلية التي تقبلها قواعد المزوّد. أكّد لإرسال الطلب "
                            "مرة واحدة."),
    "policy_deny_network": ("A model asked to reach an address. This tool sends a network request when you "
                            "press the button that makes one, and not otherwise.",
                            "أحد النماذج طلب الوصول إلى عنوان. هذه الأداة ترسل طلب شبكة عندما تضغط الزر الذي "
                            "يرسله، لا أكثر."),
    "policy_lift": ("To answer differently for this folder, set its policy row.",
                    "لتغيير الإجابة لهذا المجلد، اضبط سطر سياسته."),
    "policy_how": ("To answer it differently from here, run:  agent policy --repo \"{folder}\" "
                   "--action {action} --verdict allow|ask|deny",
                   "للإجابة بشكل مختلف من هنا، شغّل:  agent policy --repo \"{folder}\" "
                   "--action {action} --verdict allow|ask|deny"),
    # An address class that is not this machine's own costs more than one yes. These are the two shapes of
    # that refusal, said before any dialog opens: an ask the operator can answer is not the same answer as
    # a rule the folder wrote, and the difference is the whole point of the row.
    "policy_addr_limited": ("The target is {kind}: {host}. A yes on this button does not open a request "
                            "that leaves this machine — the folder has to say so once, as a rule.",
                            "الهدف من نوع {kind}: {host}. الموافقة على هذا الزر لا تفتح طلبًا يخرج من هذا "
                            "الجهاز — لا بد أن يقول المجلد ذلك مرة واحدة، كقاعدة."),
    "policy_addr_unreadable": ("The target is {kind}, and its form is {host}. This tool will not send a "
                               "request to an address it cannot read; confirm once to send it anyway, "
                               "knowing the form it is written in.",
                               "الهدف من نوع {kind}، وصيغته {host}. هذه الأداة لن ترسل طلبًا إلى عنوان لا "
                               "تستطيع قراءته؛ أكّد مرة واحدة لترسله رغم ذلك وأنت تعرف الصيغة الذي كُتب بها."),
    "env_names_unreadable": ("Could not read the environment files to compare them: {error}. The "
                             "missing-key list is left empty rather than guessed.",
                             "تعذّرت قراءة ملفات البيئة لمقارنتها: {error}. تُركت قائمة المفاتيح المفقودة "
                             "فارغة بدل تخمينها."),
    "stage_line": ("{where} — step {number} of {total}",
                   "{where} — الخطوة {number} من {total}"),
    "policy_heading": ("What this folder answers without asking:",
                       "ما يجيبه هذا المجلد من غير سؤال:"),
    "policy_allow_note": ("{count} of {total} action classes are answered by this folder's own rule",
                          "{count} من {total} من أنواع الأفعال تُجيبها قاعدة هذا المجلد نفسها"),
    "policy_managed_read": ("File access is governed by the project path rules.",
                             "الوصول للملفات تحكمه قواعد المسارات داخل المشروع."),
    "policy_managed_write": ("Writes use the proposal review and Auto-Apply safeguards.",
                              "الكتابة تخضع لمراجعة المقترح وقواعد التطبيق التلقائي."),
    "policy_managed_delete": ("Every file deletion requires review, including with Auto-Apply.",
                              "كل حذف لملف يتطلب مراجعة، حتى مع التطبيق التلقائي."),
    "policy_managed_execute_recipe": ("Recipes use the built-in allowlist and run only when you start them.",
                                       "الوصفات تخضع للقائمة المسموح بها ولا تعمل إلا عند تشغيلك لها."),
    "policy_managed_git_local": ("Local Git actions use their own checks; this tool has no push action.",
                                  "عمليات Git المحلية لها فحوصها؛ ولا توجد في الأداة عملية دفع للمستودع."),
}

# `apply_rerun_warning` is the only one with no second-language twin in the other window: Tk has no
# Auto-Apply switch, so nothing else in it can promise a command will run by itself.
NOTE_KEYS = tuple(NOTE_TEMPLATES)


def note(key: str, *, arabic: bool = False, **fields) -> str:
    """One of the sentences both windows owe the user, in the language the task was asked in.

    A key that does not exist raises rather than answering in the wrong language, and a field the
    caller forgot raises too — a status line that prints `{step}` is worse than no status line.
    """
    if key not in NOTE_TEMPLATES:
        raise KeyError("no shared sentence named " + str(key))
    english, arabic_text = NOTE_TEMPLATES[key]
    return say(arabic, en=english, ar=arabic_text).format(**fields)


# The classes that have a sentence. An ALLOW is silence — the operator is not told about a thing the
# tool went ahead with, which is what an approval flow already looks like from the inside.
POLICY_SENTENCES = {
    "write_that_runs": ("policy_ask_write_that_runs", "policy_deny_write_that_runs"),
    "execute_custom": ("policy_ask_execute_custom", "policy_deny_execute_custom"),
    "network": ("policy_ask_network", "policy_deny_network"),
}

# The three answers as words on a button. Action classes stay as codes: they are names of things in the
# table, the way a recipe id is, and translating a name would break the row that says which one it was.
POLICY_VERDICT_AR = {"allow": "سماح", "ask": "اسأل", "deny": "رفض"}


def policy_verdicts(arabic: bool) -> dict:
    """The verdict words, so a control can be labelled in the language the task was asked in."""
    return {code: (POLICY_VERDICT_AR[code] if arabic else code) for code in POLICY_VERDICT_AR}


# The words for `policy.ADDRESS_CLASSES`. They are sentences' material, not codes on a button: an operator
# deciding whether one yes is worth it has to be told in plain language that the target is a metadata
# address rather than the dev server. `address_words` keeps the pair beside the code so a new class cannot
# be added to the module without a word in both languages.
ADDRESS_WORDS = {
    "loopback": ("this machine's own address", "عنوان هذا الجهاز نفسه"),
    "link_local": ("a link-local address — the cloud metadata service answers here",
                   "عنوان رابط محلي — خدمة البيانات الوصفية للسحابة تجيب من هنا"),
    "private": ("a private network address", "عنوان شبكة خاصة"),
    "public": ("a public address", "عنوان عام"),
    "named": ("a name this tool did not look up", "اسم لم تبحث عنه هذه الأداة"),
    "unknown": ("an address form this tool cannot read", "صيغة عنوان لا تستطيع هذه الأداة قراءتها"),
}


def address_words(arabic: bool) -> dict:
    """The address class words, the same way the verdict words are handed to a control."""
    return {code: say(arabic, en=ADDRESS_WORDS[code][0], ar=ADDRESS_WORDS[code][1])
            for code in ADDRESS_WORDS}



def policy_line(arabic: bool, action: str, verdict: str, **fields) -> str:
    """What one action class answers, said in the language the task was asked in.

    The engine and the windows both need this sentence, and neither may write it: a refusal whose words
    live at the call site is a refusal one window can rephrase into something the operator approves. An
    unknown class or an ALLOW answers with the empty string, because the caller's own text is then the
    only thing on screen.
    """
    pair = POLICY_SENTENCES.get(str(action or ""))
    if not pair:
        return ""
    key = pair[0] if str(verdict or "") == "ask" else pair[1] if str(verdict or "") == "deny" else ""
    return note(key, arabic=arabic, **fields) if key else ""


def impact_lines(report: dict, *, arabic: bool = False) -> list[str]:
    """The change-impact block as sentences: one line per proposed file, then what it cannot cover.

    `impact.analyze` keeps the structure — which name went, which file still writes it — and this is
    the only home its wording has, so the web card and the desktop window both answer in the language
    the task was asked in. A file with nothing to say is left out rather than reported as clean: an
    empty answer means "nothing was found", which is not the claim "nothing was looked for".
    """
    lines: list[str] = []
    for entry in (report or {}).get("files") or []:
        said: list[str] = []
        if entry.get("removed"):
            said.append(note("impact_removed", arabic=arabic, names=", ".join(entry["removed"]))
                        + _more(entry, "removed_more", arabic))
        if entry.get("changed"):
            said.append(note("impact_changed", arabic=arabic, names=", ".join(entry["changed"]))
                        + _more(entry, "changed_more", arabic))
        if entry.get("unread"):
            said.append(note("impact_unread", arabic=arabic))
        for row in entry.get("callers") or []:
            said.append(note("impact_callers", arabic=arabic, name=row["name"],
                             count=row["count"], files=", ".join(row["files"]))
                        + _unlisted(row, arabic))
        for route in entry.get("endpoints") or []:
            said.append(note("impact_route_gone" if route.get("gone") else "impact_route_new",
                             arabic=arabic, route=route["route"], handler=route["handler"]))
        for row in entry.get("tests") or []:
            said.append(note("impact_tests", arabic=arabic, name=row["name"], count=row["count"],
                             files=", ".join(row["files"])) + _unlisted(row, arabic))
        for key in entry.get("flags") or []:
            said.append(note(key, arabic=arabic))
        if len(entry.get("modules") or []) > 1:
            said.append(note("impact_modules", arabic=arabic, modules=", ".join(entry["modules"])))
        if said:
            lines.append(redact(str(entry.get("path", "")) + " — " + "; ".join(said))[:600])
    unknown = (report or {}).get("unknown") or []
    if unknown:
        lines.append(redact(note("impact_unknown_heading", arabic=arabic) + "; ".join(
            note(row["key"], arabic=arabic, **{k: v for k, v in row.items() if k != "key"})
            for row in unknown))[:600])
    return lines


def _more(entry: dict, key: str, arabic: bool) -> str:
    """` +3 more` when a list was stopped early, and nothing when it was not."""
    left = int(entry.get(key) or 0)
    return " " + note("impact_more", arabic=arabic, count=left) if left else ""


def _unlisted(row: dict, arabic: bool) -> str:
    """The count is the truth and the list is the display; say which of the two was cut."""
    left = int(row.get("count") or 0) - len(row.get("files") or [])
    return " " + note("impact_listed_more", arabic=arabic, count=left) if left > 0 else ""


def status_text(key: str, *, arabic: bool = False, tail: str = "") -> str:
    """One of the shared status sentences, in the language the task was asked in.

    `tail` is for the four that end in a colon or a full stop and then carry what only this moment
    knows — a path, a command's own summary, an error. Those stay Latin, as everywhere else here.
    A key nobody defined raises rather than answering in the wrong language.
    """
    if key not in STATUS_TEXTS:
        raise KeyError("no shared status sentence named " + str(key))
    english, arabic_text = STATUS_TEXTS[key]
    return say(arabic, en=english, ar=arabic_text) + tail


# The step vocabulary the agent's own work is announced with. Paths, commands, file names and the
# emoji stay Latin in both languages, exactly as everywhere else in this file.
STEP_MAX_FILES = 6
# The fields a step record carries. `digest` is the eight leading characters of what was read, and it
# is in the record and not the sentence because "it read the file" and "it read the file as it stood
# before the last write" are different claims — that difference is what a row opens to say. `label` is
# the recipe name a build error came from: the rebuild in `display_session` filters the stored record
# through this tuple, so a field left out of it makes a reopened task say less than the live row did.
STEP_FIELDS = ("path", "query", "count", "names", "reason", "detail", "digest", "label")


def step_has_detail(action: str, fields: dict | None = None) -> bool:
    """Whether a step row has anything behind it.

    Both windows ask this one function, and the client draws its chevron from the answer, because a row
    that opens onto nothing teaches the reader to stop opening rows. A running command is the exception
    and the client knows it: while the job is live the row's detail is the output streaming in, which no
    stored record has yet.
    """
    fields = fields or {}
    if action == "executing":
        return False
    if action == "executed":
        return bool(fields.get("command"))
    if action in {"propose", "applied"}:
        return bool(fields.get("names"))
    if action == "read_file":
        return bool(fields.get("digest"))
    if action == "model_reasoning":
        return bool(fields.get("detail"))
    return action in {"search_code", "list_files"}


# Why the engine picked a file, said in the thread's own words. The engine sends a code and the symbol
# it came from; a reason written at the call site would be a sentence in the engine's voice, which is
# how the two windows ended up disagreeing about a refusal once already.
CONTEXT_REASON = {
    "declares": ("declares {}", "يُعرّف {}"),
    "defines": ("defines {}", "يحوي تعريف {}"),
    "imports": ("imports {}", "يستقدم {}"),
    "module": ("its folder is named in the task", "مجلده مذكور في المهمة"),
    "names": ("the task names this file", "المهمة تسمي هذا الملف"),
    "route": ("serves {}", "يخدم {}"),
    "used_by": ("used by {}", "يستخدمه {}"),
}


def context_reason(arabic: bool, why: str, symbol: str) -> str:
    """The one reason a file was chosen, spoken.

    The engine may qualify the code it sends with a bracketed hint — `declares [controller]` when the
    project index supplied a layer — and that hint belongs to the stored audit, not to the sentence.
    Looking the whole string up as a key would answer nothing, which is how a reason disappears.
    """
    phrase = CONTEXT_REASON.get(str(why or "").split(" [", 1)[0], "")
    if not phrase:
        return ""
    return say(arabic, en=phrase[0], ar=phrase[1]).replace("{}", str(symbol or ""))


def graph_caption(arabic: bool, *, nodes: int, edges: int, cyclic: bool = False,
                  hidden: int = 0) -> str:
    """One line under the module graph: what is drawn, and what the drawing leaves out.

    Server-written for the same reason the setup tally is: the sheet is one surface and the sentence
    about what it omits belongs with the code that did the omitting. A cycle is said out loud because a
    column that is approximate looks exactly like a column that is right.
    """
    parts = [say(arabic, en=f"{nodes} modules, {edges} dependencies",
                 ar=f"{nodes} موديول، {edges} تبعية")]
    if cyclic:
        parts.append(say(arabic, en="a cycle was found, so its column is approximate",
                         ar="لقيت دورة، فالعمود بتاعها تقريبي"))
    if hidden:
        parts.append(say(arabic, en=f"{hidden} smaller modules are not drawn",
                         ar=f"{hidden} موديول أصغر مش رسمانين"))
    return " · ".join(parts)


def step_line(arabic: bool, action: str, *, path: str = "", query: str = "", count: int = 0,
              names: list | None = None, reason: str = "", detail: str = "", digest: str = "",
              label: str = "") -> str:
    """One line for one thing the agent just did.

    These reach the chat as tool rows, so they are plain text by construction. An action this
    function has never heard of still gets a line — a new tool silently vanishing from the thread is
    the failure to avoid here.

    `count` and `digest` are recorded and deliberately not spoken: the row says what happened, and what
    it found is what opening the row answers.
    """
    if action == "read_file":
        return say(arabic, en=f"\U0001f4d6 Reading file: {path}", ar=f"\U0001f4d6 قراءة الملف: {path}")
    if action == "search_code":
        return say(arabic, en=f"\U0001f50d Searching code: {query}", ar=f"\U0001f50d البحث في الكود: {query}")
    if action == "list_files":
        return say(arabic, en="\U0001f4c1 Scanning project files...",
                   ar="\U0001f4c1 فحص ملفات المشروع...")
    if action == "propose":
        listed = [str(name) for name in (names or [])]
        if len(listed) > STEP_MAX_FILES:
            extra = len(listed) - STEP_MAX_FILES
            listed = listed[:STEP_MAX_FILES] + [say(arabic, en=f"+{extra} more", ar=f"+{extra} أخرى")]
        body = say(arabic, en=f"\u270d\ufe0f Proposed changes for {count} file(s)",
                   ar=f"\u270d\ufe0f اقتراح تعديلات على {count} ملف(ات)")
        named = ", ".join(listed)
        return f"{body}: {named}" if named else body
    if action == "find_symbol":
        return say(arabic, en=f"\U0001f9ed Looking up: {query}", ar=f"\U0001f9ed البحث عن الرمز: {query}")
    if action == "find_references":
        return say(arabic, en=f"\U0001f9ed Finding uses of: {query}",
                   ar=f"\U0001f9ed البحث عن استخدامات: {query}")
    if action == "context_files":
        # The reason travels as a code and a symbol, never as a finished sentence: which file was chosen
        # is the engine's decision, and how it is said belongs here with the rest of the thread's words.
        listed = []
        for row in (names or []):
            why = context_reason(arabic, str(row.get("why", "")), str(row.get("symbol", "")))
            listed.append(f"{row.get('path', '')}" + (f" ({why})" if why else ""))
        body = say(arabic, en=f"\U0001f3af Chose {count} file(s) for this task",
                   ar=f"\U0001f3af اختيرت {count} ملف(ات) لهذه المهمة")
        return f"{body}: {', '.join(listed)}" if listed else body
    if action == "model_reasoning":
        # The preview is one line; the whole thought is what the row opens to. A model that thinks out
        # loud gets to be read, but not at the cost of the thread it works in.
        shown = str(detail or "").strip().splitlines()[0][:110] if detail else ""
        body = say(arabic, en=f"\U0001f9ed Thought for {count} characters",
                   ar=f"\U0001f9ed فكر {count} حرف")
        return f"{body}: {shown}…" if shown else body
    if action == "unresolved_error":
        # D42: the same failure, from a task this window is not looking at. Said once at the start of a
        # turn rather than rediscovered by a model that has no memory of the last conversation.
        body = say(arabic, en=f"\U0001f501 {count} earlier task(s) left this build error open",
                   ar=f"\U0001f501 {count} مهمة سابقة سابت خطأ البناء ده من غير حل")
        where = f" ({label})" if label else ""
        return f"{body}{where}: {detail}" if detail else f"{body}{where}"
    if action == "blocked":
        return say(arabic, en=f"\u26d4 Blocked: {reason}", ar=f"\u26d4 توقفت المهمة: {reason}")
    if action == "applied":
        return applied_line(arabic, count=count)
    if action == "executing":
        return executing_line(arabic, command=path)
    return say(arabic, en=f"\u2699\ufe0f {action}" + (f": {detail}" if detail else ""),
               ar=f"\u2699\ufe0f {action}" + (f": {detail}" if detail else ""))


def applied_line(*, arabic: bool, count: int, removed: int = 0) -> str:
    """What the thread says after a write the user approved by hand.

    `applied_note` is the banner on the card, and it names the disk because that write was
    unattended; this one is the sentence for the click, so it says what happened and where to undo it.
    A removal is named apart because "Applied changes to 2 file(s)" over a file that is now gone
    reads as a rewrite of something the folder no longer has.
    """
    tail = say(arabic, en=f", removing {removed} of them", ar=f"، وحُذف {removed} منها") if removed else ""
    return say(arabic,
               en=f"\U0001f4be Applied changes to {count} file(s){tail}. "
                  "Roll back undoes the whole task.",
               ar=f"\U0001f4be تم تطبيق التعديلات على {count} ملف(ات){tail}. "
                  "التراجع يُلغي المهمة كاملة.")


def run_unrecorded_line(*, arabic: bool, project: str) -> str:
    """A project command that ran with no applied task to carry the verdict.

    The run is real and its output is in Activity; what is missing is a session to write the result
    on, because the task before it blocked or rolled back. Left unsaid, that looks like the tool
    invented a pass for a task it never applied.
    """
    return say(arabic,
               en=f"\u23f1 {project} was checked, but no applied task is open to hold the result — "
                  "the output is in Activity.",
               ar=f"\u23f1 تم فحص {project}، لكن لا توجد مهمة مطبَّقة تحمل النتيجة — المُخرج في Activity.")


def fix_offers_off_line(*, arabic: bool) -> str:
    """The row that says the batch stopped being asked. Suppression with no sentence reads as the
    tool ceasing to care about the failure, and the failure line above it is the only evidence left."""
    return say(arabic,
               en="\u23ed Fix offers are off for the rest of this batch. Each failure is still "
                  "reported and nothing is fixed on its own.",
               ar="\u23ed إيقاف أسطر الإصلاح لبقية هذه الدفعة. كل فشل ما زال يُعرض ولا يُصلح شيء تلقائيًا.")


def batch_summary_line(*, arabic: bool, tasks: int, files: int, paths: list[str],
                       short: list[str]) -> str:
    """The row that closes a queue batch: what ran, what landed, and what fell short.

    A batch of ten tasks used to leave ten separate write notices and no answer to "what did that
    actually do to my folder" — D36. Paths are basename-only because the row is read in a narrow
    column, and the full paths are on each task's own card.
    """
    listed = ", ".join(str(path).replace("\\", "/").rsplit("/", 1)[-1] for path in paths[:6])
    if len(paths) > 6:
        listed += f", +{len(paths) - 6} more"
    parts = [say(arabic,
                 en=f"\U0001f4e6 Batch finished: {tasks} task(s), {files} file(s) written.",
                 ar=f"\U0001f4e6 انتهت الدفعة: {tasks} مهمة(ات) و{files} ملف(ات).")]
    if listed:
        parts.append("\U0001f4c1 " + listed)
    for task in short[:3]:
        parts.append(say(arabic,
                         en=f"\u26a0 A task asked for a file count it did not deliver: {task}",
                         ar=f"\u26a0 طلبت مهمة عددًا من الملفات لم يُسلَّم: {task}"))
    return "\n".join(parts)


def executing_line(*, arabic: bool, command: str) -> str:
    """The command about to run.

    Naming the argv is the point: a project's own build executes code the repository defines, and
    this is the last line the user reads before it does.
    """
    return say(arabic, en=f"\u2699\ufe0f Executing: {command}", ar=f"\u2699\ufe0f تنفيذ الأمر: {command}")


# What a command came back with, in the short form a step row carries. `runner.summarize` is the
# long sentence for the status strip; these are the words inside "Ran mvn -B test — failed · exit 1".
RUN_WORDS = {"passed": ("passed", "نجح"), "failed": ("failed", "فشل"),
             "timeout": ("timed out", "انتهت مهلته"), "unverified": ("no tests ran", "لم تُشغَّل اختبارات"),
             "unavailable": ("not available", "غير متاح")}


def run_verdict(arabic: bool, run: dict) -> str:
    status, arabic_status = RUN_WORDS.get(str(run.get("status")), RUN_WORDS["unverified"])
    parts = [say(arabic, en=status, ar=arabic_status),
             f"exit {run.get('exit_code')}", f"{run.get('seconds')}s"]
    proof = run.get("proof") or {}
    if proof.get("tests"):
        parts.append(say(arabic, en=f"{proof['tests']} tests", ar=f"{proof['tests']} اختبار"))
    if run.get("truncated"):
        parts.append(say(arabic, en="output trimmed", ar="المُخرج مختصر"))
    return " · ".join(str(part) for part in parts)


def executed_line(*, arabic: bool, command: str, verdict: str) -> str:
    """The same row once the command has answered.

    A thread left holding "Executing mvn -B test" after the build finished is describing a moment
    that has passed, and the reader cannot tell a finished run from one they are still waiting on.
    """
    return say(arabic, en=f"\u2699\ufe0f Ran {command} — {verdict}",
               ar=f"\u2699\ufe0f نُفِّذ {command} — {verdict}")


# The words on the inside of a step row. A row that cannot answer one of these has nothing to open,
# and the client draws no chevron for it (docs/UI41-STEP-ROWS-PLAN.md).
DETAIL_SECTIONS = {
    "command": ("Command", "الأمر"),
    "result": ("Result", "النتيجة"),
    "problems": ("Reported problems", "المشكلات المُبلَّغ عنها"),
    "output": ("End of output", "آخر المُخرج"),
    "files": ("Files in this change", "ملفات هذا التغيير"),
    "file": ("File", "الملف"),
    "read": ("Read by the agent", "قرأها الوكيل"),
    "search": ("Search", "البحث"),
    "reasoning": ("What it thought first", "ما فكر فيه الأول"),
}


def detail_section(arabic: bool, key: str) -> str:
    english, arabic_text = DETAIL_SECTIONS[key]
    return say(arabic, en=english, ar=arabic_text)


def graph_empty_line(*, arabic: bool) -> str:
    """The answer to a click on the graph button when there is no graph.

    A sheet that opens empty reads as a project with no structure; the true sentence is that this window
    has no folder to walk, or none with a source file in it yet.
    """
    return say(arabic,
               en="Nothing to draw yet: this window has no project folder with source files in it.",
               ar="مفيش حاجة ارسمها لحد دلوقتي: النافذة دي ملهاش فولدر بروجيكت فيه ملفات كود.")


def step_missing_line(*, arabic: bool) -> str:
    """The answer to a click on a row this window has no record of.

    A row that opens onto silence is the dead-button failure all over again, and the honest sentence is
    short: the page is behind the task, and reloading fixes it.
    """
    return say(arabic,
               en="This step is not in the task this window has open. Reload to see the current one.",
               ar="هذه الخطوة ليست في المهمة المفتوحة في هذه النافذة. أعد التحميل لرؤية الحالية.")


def log_dropped_line(*, arabic: bool, count: int) -> str:
    """The Activity list is capped so a long build cannot ride every later snapshot. The cap has to
    be visible: a trimmed log reads as a task that did less than it did."""
    return say(arabic,
               en=f"\u2026 {count} earlier line(s) are not kept in this window; the task's own "
                  "record still holds them.",
               ar=f"\u2026 {count} سطر أقدم غير محفوظ في هذه النافذة؛ سجل المهمة نفسه ما زال يحتفظ بها.")


def log_line(arabic: bool, entry: dict) -> str:
    """One stored session event, said as a sentence for the Activity list.

    A reopened task rebuilds its Activity rows from the session's own records, and those records are
    audit rows — `event(session, "tool", name=…, path=…, sha256=…)`. Printed as they are stored they
    read `tool name=read_file path=pom.xml sha256=9f3c2…`, which is the engine's notebook rather than
    something an operator can scan, and a `plan_attached` event carries the plan's whole text in its
    `content` field, so the old form printed a file's worth of body inside one status row. Every kind
    this window can reopen therefore has a sentence here, and it names at most the field a reader
    would act on: a path, a query, a verdict. Hashes belong to the step row that opens onto them.

    A kind this function has never met is still said, by name — a row that comes out blank hides a
    whole phase of a run, which is the failure worth more.
    """
    kind = str(entry.get("kind", ""))
    if kind == "step":
        return step_line(arabic, str(entry.get("action", "")),
                         **{key: entry[key] for key in STEP_FIELDS if key in entry})
    if kind == "run":
        return executed_line(arabic=arabic, command=str(entry.get("recipe", "")),
                             verdict=run_verdict(arabic, entry))
    if kind == "tool":
        name = str(entry.get("name", ""))
        if name == "read_file":
            return say(arabic, en=f"\U0001f4d6 Read {entry.get('path', '')}",
                       ar=f"\U0001f4d6 قراءة {entry.get('path', '')}")
        if name == "list_files":
            return say(arabic, en=f"\U0001f4c1 Listed {entry.get('count', 0)} file(s)",
                       ar=f"\U0001f4c1 حصر {entry.get('count', 0)} ملف(ات)")
        if name == "search_code":
            return say(arabic, en=f"\U0001f50d {entry.get('matches', 0)} match(es) in the project",
                       ar=f"\U0001f50d {entry.get('matches', 0)} نتيجة في المشروع")
        if entry.get("query"):
            return say(arabic, en=f"\U0001f50d {name}: {entry['query']} — {entry.get('count', 0)} hit(s)",
                       ar=f"\U0001f50d {name}: {entry['query']} — {entry.get('count', 0)} نتيجة")
        return say(arabic, en=f"\u2699\ufe0f {name} — {entry.get('count', 0)} result(s)",
                   ar=f"\u2699\ufe0f {name} — {entry.get('count', 0)} نتيجة")
    if kind == "context_file":
        why = context_reason(arabic, str(entry.get("why", "")), str(entry.get("symbol", "")))
        return say(arabic, en=f"\U0001f3af Read {entry.get('path', '')} into the context" +
                              (f" ({why})" if why else ""),
                   ar=f"\U0001f3af قراءة {entry.get('path', '')} في السياق" +
                      (f" ({why})" if why else ""))
    if kind == "context_excerpt":
        # Its own row rather than a share with `context_file`: a block of forty lines that arrived
        # because the whole file would not fit is not a read, and the strip that says it is teaches the
        # operator to trust a context that was never there.
        return say(arabic,
                   en=f"\u2702\ufe0f Sent {entry.get('lines', 0)} lines of {entry.get('path', '')} "
                      f"(partial — this file was not read in full)"
                      + (f" ({entry.get('symbol', '')})" if entry.get("symbol") else ""),
                   ar=f"\u2702\ufe0f أُرسلت {entry.get('lines', 0)} سطرًا من "
                      f"{entry.get('path', '')} (جزئي — لم يُقرأ هذا الملف بالكامل)"
                      + (f" ({entry.get('symbol', '')})" if entry.get("symbol") else ""))
    if kind == "auto_read":
        return say(arabic, en=f"\U0001f4d6 Read {entry.get('path', '')} before it was asked for",
                   ar=f"\U0001f4d6 قراءة {entry.get('path', '')} قبل طلبها")
    if kind == "file_not_found":
        can = say(arabic, en=" — it may be created", ar=" — يمكن إنشاؤه") \
            if entry.get("can_create") else ""
        return say(arabic, en=f"\U0001f50e {entry.get('path', '')} is not in the project{can}",
                   ar=f"\U0001f50e {entry.get('path', '')} ليس في المشروع{can}")
    if kind == "rejected_action":
        return say(arabic, en=f"\U0001f6ab Refused an action: {entry.get('reason', '')}",
                   ar=f"\U0001f6ab رفض إجراء: {entry.get('reason', '')}")
    if kind == "proposal":
        return say(arabic, en="\u270d\ufe0f Proposal recorded", ar="\u270d\ufe0f تسجيل المقترح")
    if kind == "approved":
        return say(arabic, en="\u2705 Approved for writing", ar="\u2705 الموافقة على الكتابة")
    if kind == "proposal_rejected":
        return say(arabic, en="\U0001f6ab Proposal declined — nothing was written",
                   ar="\U0001f6ab رُفض المقترح — لم تُكتَب أي ملفات")
    if kind == "proposal_reopened":
        return say(arabic, en="Proposal reopened for review — nothing was written",
                   ar="أُعيد فتح المقترح للمراجعة — لم تُكتب أي ملفات")
    if kind == "written":
        return say(arabic, en=f"\U0001f4be Wrote {entry.get('path', '')}",
                   ar=f"\U0001f4be كتابة {entry.get('path', '')}")
    if kind == "removed":
        return say(arabic, en=f"\U0001f5d1 Removed {entry.get('path', '')}",
                   ar=f"\U0001f5d1 حذف {entry.get('path', '')}")
    if kind == "block_chosen":
        return say(arabic, en=f"\u2702\ufe0f Took {entry.get('path', '')} from a code block in the answer",
                   ar=f"\u2702\ufe0f أخذ {entry.get('path', '')} من كتلة كود في الرد")
    if kind == "rolled_back_file":
        return say(arabic, en=f"\u21a9 Restored {entry.get('path', '')}",
                   ar=f"\u21a9 استرجاع {entry.get('path', '')}")
    if kind == "rolled_back":
        return status_text("rolled_back", arabic=arabic)
    if kind == "verification":
        status = str(entry.get("status", ""))
        word = RUN_WORDS.get(status, (status, status))
        return say(arabic, en=f"\U0001f6e1 Checks {word[0]}", ar=f"\U0001f6e1 الفحوص {word[1]}")
    if kind == "stopped":
        return say(arabic, en=f"\u26d4 Stopped: {entry.get('reason', '')}",
                   ar=f"\u26d4 توقف: {entry.get('reason', '')}")
    if kind == "plan_attached":
        return say(arabic, en=f"\U0001f4cb Plan attached: {entry.get('path', '')}",
                   ar=f"\U0001f4cb إرفاق الخطة: {entry.get('path', '')}")
    if kind == "memory_attached":
        return say(arabic, en=f"\U0001f9e0 Standing notes sent ({entry.get('characters', 0)} characters)",
                   ar=f"\U0001f9e0 إرسال ملاحظات ثابتة ({entry.get('characters', 0)} حرف)")
    if kind == "evidence_attached":
        return say(arabic, en=f"\U0001f4ce Build evidence sent ({entry.get('characters', 0)} characters)",
                   ar=f"\U0001f4ce إرسال دليل البناء ({entry.get('characters', 0)} حرف)")
    if kind == "impact":
        return say(arabic,
                   en=f"\U0001f50e Checked what this breaks elsewhere "
                      f"({entry.get('files', 0)} files with findings, "
                      f"{entry.get('unknown', 0)} not covered)",
                   ar=f"\U0001f50e فحص ما يفسده هذا في أماكن أخرى "
                      f"({entry.get('files', 0)} ملفات، غير مغطى: {entry.get('unknown', 0)})")
    return say(arabic, en=f"\u2699\ufe0f {kind}", ar=f"\u2699\ufe0f {kind}")


def queue_notes(arabic: bool, elsewhere: int, asked: bool) -> dict:
    """The sentences on the queue strip.

    They live here because the strip is drawn by two controllers — the real one and `--fake` — and
    because only the server knows what language the queued task was asked in. The client supplies
    the glyphs and the buttons, never the prose.

    `asked` is the difference between a queue that is waiting and a queue that is *stopped*: while a
    question is on screen nothing drains, and a strip saying "when the current task ends" beside a
    dialog that can sit there for half an hour describes a moment that is not coming.
    """
    return {"when": say(
        arabic,
        en="runs when you answer the question on the screen" if asked
           else "runs when the current task ends",
        ar="تُنفَّذ بعد أن تجيب على السؤال المعروض" if asked
           else "تُنفَّذ عند انتهاء المهمة الجارية"),
            "when_detached": say(arabic, en="next, in its own chat",
                                  ar="التالية، في محادثة مستقلة"),
            "held_note": say(arabic, en="These wait until you press Resume.",
                              ar="هذه تنتظر حتى تضغط استئناف."),
            # A queued message that survived a restart is the one row the user did not type in this
            # session, and a change request can be pointed at files that moved on since it was
            # written. It is restored held; the sentence has to say why it is not running.
            "when_restored": say(arabic,
                                 en="typed before this window restarted — press ▶ to run it",
                                 ar="كُتبت قبل إعادة تشغيل هذه النافذة — اضغط ▶ لتنفيذها"),
            "elsewhere_note": say(
                arabic,
                en=f"{elsewhere} waiting in another chat — open it to see them",
                ar=f"{elsewhere} في محادثة أخرى — افتحها لترى ما ينتظر")}


def key_needed_line(*, arabic: bool, env_name: str = "") -> str:
    """Which variable has to hold the key. Naming it is the difference between a fix and a search."""
    if env_name:
        return say(arabic, en=f"Enter your {env_name} first — in the key field, or in that "
                              "environment variable. It is never saved to a file.",
                   ar=f"اكتب {env_name} أولاً — في خانة المفتاح أو في متغير البيئة ده. "
                      "المفتاح ما بيتحفظش في ملف خالص.")
    return say(arabic, en="This provider needs an API key first.", ar="البروفايدر ده محتاج مفتاح API الأول.")


def catalog_status_line(*, arabic: bool, count: int, model: str, label: str,
                        live: bool = True) -> str:
    """What a refresh found, and *where* it came from.

    A built-in fallback list and a live catalog look identical in a dropdown but mean different
    things: one is what the service says it has today, the other is a name this tool shipped with.
    Saying which is the difference between a stale id being a surprise and being a known risk.
    """
    if not count:
        return say(arabic,
                   en=f"No models found for {label}. Check the service or the endpoint, "
                      "or choose another provider.",
                   ar=f"مفيش موديلات اتلقات لـ {label}. اتأكد من الخدمة أو من العنوان، "
                      f"أو اختار بروفايدر تاني.")
    head = say(arabic, en=f"{count} models loaded from {label}. ",
               ar=f"{count} موديل من {label}. ")
    using = (say(arabic, en=f"Using {model}.", ar=f"هيستخدم {model}.") if model else
             say(arabic, en="Choose one from the list.", ar="اختار واحد من القائمة."))
    if live:
        return head + using
    return head + using + " " + say(
        arabic,
        en="These are names this tool ships with — the live list could not be reached, so one may "
           "no longer exist on that service.",
        ar="دي أسماء موجودة في Tool نفسها — قائمة السيرفر الحي ما وصلتش، فممكن واحد منها ما بقاش موجود.")


def friendly_error(exc: Exception) -> str:
    # Redacted once, here, because almost every branch returns a slice of ``value``: the fallthrough
    # hands back the exception text verbatim, and ``engine`` interpolates the model's own refusal
    # reason into an AgentError. Whatever a provider, a command or a model put in that text is then
    # this window's status line, chat row and Activity log.
    value = redact(str(exc))
    if "request timeout" in value:
        return ("The model needed more than the request timeout. Raise it in Project & model settings, "
                "or ask for a smaller change.")
    if "connection failed" in value or "timed out" in value:
        return "Cannot reach the model. Start Ollama, check the selected model, and try again."
    if "changed since" in value or "overwrite a later edit" in value:
        return "Files changed after review. Create a new proposal to preserve your edits."
    if "repeated the same action" in value:
        # The runtime stops a model that answers identically three times. In practice that
        # is almost never a crash: it is a model with nothing left to do, because the
        # change it was asked for already exists or the request is too vague to act on.
        return ("The model kept giving the same answer without making progress. Usually the requested "
                "change is already in the files, or the task is too vague to act on — open the file to "
                "check, or describe one concrete edit.")
    if "unchanged file" in value:
        # A message with no edit in it reaches this in Change mode: the model reads a file,
        # proposes it as it stands, and is refused. The fix is the badge, not another model.
        return ("That message did not ask for a file change, so there was nothing to propose. "
                "Switch the badge next to Send to Chat mode to ask freely, or name one concrete "
                "edit to stay in Change mode.")
    if "could not produce a proposal" in value:
        reason = value.split("could not produce a proposal:", 1)[-1].strip()
        if reason:
            return (f"\u26d4 The model was unable to produce a proposal: {reason} "
                    f"Try using a larger model or breaking the task into smaller steps.")
        return "\u26d4 The model could not complete a valid proposal. Try a larger model or a smaller step."
    if "without progress" in value or "budget" in value:
        return "The model could not complete a valid proposal. Try a smaller task or another model."
    if "output truncated" in value:
        # The reply stopped at the output-token ceiling, so what the user sees is half an answer
        # rather than a wrong one. Both fixes are the user's to make, so name them.
        return ("The model reached its output limit before finishing, so the answer is cut off. "
                "Ask for one file or one step at a time, or raise the output limit in Settings.")
    if " in your environment" in value:
        # Named by the provider that refused, so a Groq key is not reported as an OpenRouter one.
        return "Enter your " + value.split(" in your environment", 1)[0].split("Set ", 1)[-1].strip() + " first."
    if "API_KEY" in value:
        return "Enter your OpenRouter API key first."
    if "HTTP 401" in value or "HTTP 403" in value:
        return "Access denied by the provider. Check your API key and permissions."
    if "HTTP 429" in value:
        return "Rate limit reached. Wait a moment or select a local model."
    if isinstance(exc, Cancelled):
        return "Task cancelled. No project files were changed."
    if isinstance(exc, (AgentError, OSError)):
        return value[:600]
    return "Could not complete the operation. Reopen the task and try again."
