"""Planning-mode prompts and deterministic completeness checks for prose plans."""
from __future__ import annotations

import re

MAX_PLANNING_TOKENS = 1800


_PLAN_WORDS = re.compile(
    r"\b(plan|planning|roadmap|break\s+.*\s+into\s+tasks|implementation\s+steps|task\s+list)\b"
    r"|\b(خطة|بلان|خطوات|مهام|تاسكات|تقسيم\s+المشروع)\b", re.IGNORECASE)


def requests_plan(text: str) -> bool:
    return bool(_PLAN_WORDS.search(str(text or "")))


def planning_instruction() -> str:
    return (
        "Plan mode: return a concise, ordered future-work plan only; no code or file edits. "
        "Separate verified project facts (cite paths) from goals and assumptions; never invent paths "
        "or results. For each task include ID, affected paths, dependencies, done criteria, verification, "
        "difficulty, effort range, brief rationale, and confidence. Cover implementation and tests. "
        "End with requirement coverage and risks; state uncertainty or incompleteness."
    )


def quality_issues(answer: str) -> list[str]:
    """Flag structural gaps without pretending a heuristic can prove semantic correctness."""
    text = str(answer or "")
    issues: list[str] = []
    coverage_match = re.search(
        r"(?im)^\s*#{0,6}\s*(?:requirement.?to.?task coverage|coverage|خريطة تغطية المتطلبات)\b.*$",
        text)
    tasks_text = text[:coverage_match.start()] if coverage_match else text
    task_heads = list(re.finditer(
        r"(?im)^\s*(?:#{1,6}\s*)?(?:task\s*)?(\d{1,2})\s*[.):\-–—]\s*\S|"
        r"^\s*(?:#{1,6}\s*)?Task\s+[A-Z0-9_-]+\s*[:.)\-–—]", tasks_text))
    if not task_heads:
        issues.append("No clearly numbered task list was found.")
    segments = [tasks_text[m.start(): task_heads[i + 1].start()
                            if i + 1 < len(task_heads) else len(tasks_text)]
                for i, m in enumerate(task_heads)]
    for index, segment in enumerate(segments, 1):
        checks = {
            "affected paths": r"affected paths?|files?[/ ]modules?|المسارات|الملفات المتأثرة",
            "dependencies": r"depend|prerequisite|اعتماد|متطلب سابق",
            "acceptance criteria": r"done when|acceptance|complete when|معيار|يكتمل عندما",
            "verification evidence": r"verify|verification|evidence|command|تحقق|دليل",
            "difficulty": r"difficulty|easy|medium|hard|الصعوبة|سهل|متوسط|صعب",
            "effort estimate": r"effort|estimate|hours?|days?|جهد|تقدير|ساعة|ساعات|يوم|الوقت|المدة",
            "estimate rationale": r"rationale|reason|سبب التقدير|مبرر",
            "confidence": r"confidence|ثقة",
        }
        missing = [label for label, pattern in checks.items()
                   if not re.search(pattern, segment, re.IGNORECASE)]
        if missing:
            issues.append(f"Task {index} is missing: {', '.join(missing)}.")
    if segments and not re.search(
            r"(?i)implement|create|add|build|configure|write|scaffold|إنشاء|تنفيذ|إضافة|بناء|تكوين",
            " ".join(segments)):
        issues.append("The task list appears limited to inspection/review/verification; add implementation work.")
    if segments and not re.search(r"(?i)test|mockmvc|unit|integration|اختبار", " ".join(segments)):
        issues.append("No test creation or test coverage task was detected.")
    if not coverage_match:
        issues.append("No requirement-to-task coverage map was found.")
    if not re.search(r"(?i)risk|assumption|unknown|open question|مخاطر|افتراض|غير محسوم", text):
        issues.append("Risks, assumptions, or open questions are not clearly listed.")

    # Dependency references must point to a task declared in the answer. This is a structural
    # check only; ordering/semantic coverage still need a human review.
    declared: list[str] = []
    for match in task_heads:
        heading = tasks_text[match.start():tasks_text.find("\n", match.start())
                             if "\n" in tasks_text[match.start():] else len(tasks_text)]
        found = re.search(r"(?i)\bTask\s+([A-Z0-9_-]+)\b|^\s*(\d{1,2})\s*[.):\-–—]", heading)
        if found:
            declared.append(re.sub(r"(?i)^T(?=\d+$)", "", found.group(1) or found.group(2)).upper())
    known = set(declared)
    if coverage_match and known:
        coverage = text[coverage_match.start():]
        mapped = {value.upper() for value in re.findall(r"\b(?:T\d+|Task\s+\d+|\d+)\b", coverage,
                                                        re.IGNORECASE)}
        mapped = {re.sub(r"(?i)^Task\s+|^T", "", value) for value in mapped}
        if not known.issubset(mapped):
            issues.append("The coverage map does not reference every declared task ID.")
    for index, segment in enumerate(segments):
        ref_match = re.search(
            r"(?im)^\s*(?:dependencies|depends on(?: task IDs?)?|prerequisites?(?: task IDs?)?|"
            r"يعتمد على|الاعتماديات)\s*[:：]?\s*([^\n]+)",
            segment)
        if not ref_match:
            continue
        refline = ref_match.group(1)
        for ref in re.findall(r"\b(?:T\d+|Task\s+\d+|\d+)\b", refline, re.IGNORECASE):
            key = re.sub(r"(?i)^Task\s+|^T", "", ref).strip().upper()
            if key not in known:
                issues.append(f"Dependency reference {ref!r} does not match a declared task ID.")
            elif key in declared and declared.index(key) >= index:
                issues.append(f"Dependency {ref!r} must precede the task that depends on it.")
    return issues


def quality_note(issues: list[str], *, arabic: bool = False) -> str:
    if not issues:
        return ("Plan structure check: required task fields and closing sections were detected. "
                "This check cannot prove semantic completeness; review requirement coverage.")
    title = "مراجعة اكتمال الخطة" if arabic else "Plan completeness check"
    intro = ("لم تجتز الخطة الفحص البنيوي التالي، لذا اعتبرها غير مكتملة:" if arabic else
             "The plan did not pass these structural checks; treat it as incomplete:")
    return title + ": " + intro + "\n" + "\n".join("• " + issue for issue in issues)
