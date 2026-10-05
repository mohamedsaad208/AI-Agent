بناءً على الفحص الشامل والدقيق لكافة ملفات المشروع (`src/ai_code_engineer` وواجهة الـ Web والـ CLI الحالية ونظام الاختبارات)، إليك تحليل المطلوب، جدول التدقيق وتقييم كل نقطة مقارنة بالوضع الحالي (Audit Table)، وجدول تفكيك المهام مقسمًا بحسب خطة الإصدارات (Releases).

---

## 1. تحليل المطلوب والقيود المعمارية

### الهدف الأساسي
- تحسين **تجربة المستخدم فقط (User Experience)** عبر طبقتي سطر الأوامر (CLI) وواجهة الويب (Web UI).
- إضافة نظام **ذاكرة للمشروع والمحادثة (Project & Chat Memory)** ليعمل كـ "بوصلة" (Compass) للنموذج عبر الجلسات والطلبات.
- **القيود الصارمة:**
  1. عدم إعادة بناء الـ Core أو تغيير موفري الـ LLM أو إضافة اعتماديات سحابية (الحفاظ على التشغيل المحلي الكامل دون إنترنت مع Ollama).
  2. الفصل التام بين منطق الـ Core وواجهات العرض؛ حيث يُصدر الـ Core أحداثاً مهيكلة فقط (Structured Events)، بينما تشترك واجهات الـ CLI والـ Web ونظام الـ Memory في نفس تدفق الأحداث (Event Stream).
  3. تنفيذ العمل وفق منهجية تدريجية عبر 6 إصدارات (Releases) دون البدء في الكود إلا بعد اعتماد التدقيق.

---

## 2. جدول تقييم ومراجعة الوضع الحالي (Audit: Current State)

| الخاصية / المكون | التقييم الحالي | ما هو موجود حالياً في الكود | ما هو مفقود أو يحتاج استكمال |
| :--- | :---: | :--- | :--- |
| **هندسة تدفق الأحداث (Event Stream Architecture)** | **PARTIAL** | الـ Engine يُسجل أحداثاً خام كقواميس داخل `session["events"]` (مثل `stage`, `step`, `proposal`)، ويوجد SSE Hub في خادم الويب (`webapp/server.py`). | لا توجد كائنات أحداث مهيكلة موحدة (Dataclasses للأنواع الـ 8 المحددة: `stage_changed`, `step_updated`, `tool_call`, `file_changed`, `test_result`, `approval_requested`, `error`, `final_report`). الـ Core لا يبث هذه الأحداث بشكل منفصل وموحد لجميع الواجهات. |
| **شريط الحالة المرئي وقائمة الفحص (Visible Status & Checklist)** | **PARTIAL** | الويب يمتلك `stagestrip` ومؤشر مراحل بدائي، و`core.py` يحدد المراحل والتحويلات. | الـ CLI لا يملك شريط حالة حي (Persistent Status Line). كلاهما يفتقر إلى قائمة الفحص المصغرة (done / running / pending) الموحدة بحسب المواصفات. |
| **أحداث هندسية بدلاً من ضجيج الشات ومستويات العرض** | **MISSING** | يتم طباعة نصوص سردية عبر `safe_print` مثل `Turn 1/4: asking model...` أو نصوص الشات. | غير موجود نظام تنقية الضجيج لعرض الأحداث الهندسية بتنسيق محدد مثل `[ANALYSIS]` و `[CHANGE]` و `[TEST]`. مستويات العرض (`NORMAL`, `VERBOSE`, `DEBUG`) غير مدعومة. |
| **وضع التخطيط (Plan Mode: `--plan`)** | **PARTIAL** | يوجد أمر فرعي `agent plan <task>` يستكشف المستودع ويحفظ اقتراحاً دون كتابة كود مباشر. | لا يعمل كـ Flag/Mode مرن (`--plan`) يوضح صراحة: (Goal, Affected Files, Steps, Risks, Test Strategy) للقراءة والموافقة المسبقة قبل أي تعديل. |
| **التوجيه اللحظي (Mid-run Steering)** | **MISSING** | لا يمكن مقاطعة حلقة التفكير في `engine.py` أو إدخال تعليمات توجيهية أثناء التنفيذ. | ميزة `steer <instruction>` غير متوفرة تماماً في الـ Engine أو الـ CLI أو الـ Web. |
| **الموافقات الذكية وسياسة المخاطر (RiskPolicy & Smart Approvals)** | **PARTIAL** | يوجد `policy.py` و `policy_engine.py` وفئات مخاطر (`RiskLevel`) لكنها معزولة ولا ترتبط بالـ CLI أو تدفق الأحداث. يتطلب الـ CLI حالياً إدخال الـ SHA-256 hash كاملاً يدويًا للموافقة! | توحيد المنطق في فئة واحدة `RiskPolicy`، والتشغيل التلقائي للمهام الآمنة، وطلب الموافقة للمهام الخطرة بزر واحد: `[y] approve [n] reject [d] details`. |
| **التقرير الختامي وشارات التحقق (Final Report & Verification Badges)** | **PARTIAL** | توجد دالة `review()` وتصدير جلسات في `report.py`. | لا يوجد التنسيق الختامي المطلوب مع الشارات الثلاث الصارمة: `VERIFIED` / `INFERRED` / `NOT CHECKED` وجدول ملخص القرارات والمخاطر المتبقية. |
| **ملخص التغييرات وعرض الفروقات (Changeset & Diff Views)** | **PARTIAL** | يتم حساب الفروقات في `engine.py` ويوجد عارض diff في الويب. | في الـ CLI تطبع الفروقات كنصوص خام دون ألوان Syntax، ولا يوجد أمر مخصص `diff` يعرض الفروق مع 3 أسطر سياق وملخص للملفات. |
| **الأخطاء الودية (Friendly Errors)** | **PARTIAL** | توجد دالة `friendly_error()` في `labels.py` لمعالجة بعض رسائل الويب. | لا يوجد التنسيق الهيكلي الثلاثي: (Problem / Root cause / Agent action) مع إمكانية عرض التتبع الكامل عبر `details`. |
| **الأوامر الموحدة (Unified Commands)** | **MISSING** | أوامر الـ CLI حالياً هي Argparse Subcommands منفصلة (`plan`, `review`, `apply`, `rollback`...). | مجموعة الأوامر التفاعلية الموحدة الـ 12 (`status`, `plan`, `changes`, `diff`, `tests`, `risks`, `stop`, `steer`, `report`, `undo`, `memory`, `help`) غير مدمجة في جلسة واحدة بين الـ CLI والـ Web. |
| **التصميم البصري للـ CLI (Rich & prompt_toolkit)** | **MISSING** | المكتبات مثبتة في بيئة بايثون (`rich`, `prompt_toolkit`)، لكن كود `cli.py` يستخدم `print` و `sys.stdout` التقليدي ورسمة ASCII ثابتة. | لا يوجد استخدام لـ `Rich.Live` لشريط الحالة، أو `Status` للمؤشرات المتحركة (spinners)، أو اختصارات `prompt_toolkit` (`Esc`, `Tab`, `?`)، أو احترام `NO_COLOR` وفحص الـ TTY. |
| **تخطيط واجهة الويب (Web UI Layout)** | **PARTIAL** | تعتمد واجهة الويب الحالية على شريط جانبي + عمود شات/نشاط + عمود سكة أيمن (Rail). | التخطيط المطلوب ثلاثي الأعمدة الصريح: (يسار: قائمة مهام الخطة، وسط: خط زمني للأحداث، يمين: التغييرات والفروقات)، بالإضافة لبطاقات الموافقة والشريط العلوي مع الإيقاف والتوجيه الدائمين. |
| **نظام الذاكرة الثنائي والبوصلة (Project & Chat Memory)** | **PARTIAL** | يوجد `memory.py` لحفظ ملاحظات في `.agent-memory/` كملف نصي وملف auto.json. | النظام المطلوب ثنائي الطبقات بهيكلية محددة: `.agent/memory/project.md` و `.agent/memory/chats/<chat_id>.md`، مع صياغة البوصلة (Compass) بحد أقصى 1500 توكن، وحماية هدف المشروع (Goal Protection) من التعديل الصامت، وأوامر `memory` المخصصة. |

---

## 3. جدول تفكيك المهام وخطة الإصدارات (Task Roadmap)

تم توزيع المهام بدقة وفقًا للإصدارات الستة المحددة في المواصفات، مع الالتزام بإنشاء الاختبارات لكل إصدار:

```
[Release 1] ──► [Release 2] ──► [Release 3] ──► [Release 4] ──► [Release 5] ──► [Release 6]
Event Stream    Plan, Steer,    Web SSE Layout  Diffs, Errors,  Shortcuts,      Project & Chat
& CLI Status    Risk & Approval (3 Columns)     Commands & Undo Banner & TTY    Memory Engine
```

### جدول المهام التفصيلي

| الإصدار | رقم المهمة | اسم المهمة والوصف | الملفات المعنية | مخرجات التحقق والاختبار |
| :--- | :--- | :--- | :--- | :--- |
| **Release 1** | **T1.1** | **بناء نموذج الأحداث الموحد (Event Schema)**<br>إنشاء Dataclasses للأحداث الثمانية مع Serialization ومستويات الأحداث (`NORMAL`, `VERBOSE`, `DEBUG`). | `src/ai_code_engineer/events.py` | `tests/test_event_schema.py` |
| | **T1.2** | **محول الأحداث وربطه بالنواة (Core Event Emitter)**<br>تعديل محول الأحداث في `engine.py` لبث الأحداث دون طباعة مباشرة داخل الـ Core. | `src/ai_code_engineer/engine.py` | `tests/test_events_emission.py` |
| | **T1.3** | **شريط الحالة والأحداث في الـ CLI (Rich Status Line)**<br>بناء عارض أحداث للـ CLI باستخدام `Rich.Live` وشريط حالة متحدث في مكانه مع الرموز المحددة (✓, ✗, ●, ○, !). | `src/ai_code_engineer/cli_view.py`, `src/ai_code_engineer/cli.py` | `tests/test_cli_render.py` |
| | **T1.4** | **التقرير الختامي (Final Report Table)**<br>بناء جدول ختامي عبر `Rich.Table` بشارات التحقق (`VERIFIED` / `INFERRED` / `NOT CHECKED`). | `src/ai_code_engineer/report_view.py` | `tests/test_final_report.py` |
| **Release 2** | **T2.1** | **سياسة تقييم المخاطر الموحدة (RiskPolicy)**<br>دمج وتوحيد منطق تقييم المخاطر في فئة `RiskPolicy` (تصنيف العمليات المنخفضة والعالية الحساسية). | `src/ai_code_engineer/risk_policy.py` | `tests/test_risk_policy.py` |
| | **T2.2** | **وضع التخطيط المنفصل (Plan Mode: `--plan`)**<br>دعم استعراض الخطة كاملاً للقراءة فقط (الأهداف، الملفات المتأثرة، الخطوات، المخاطر، استراتيجية الاختبار). | `src/ai_code_engineer/cli.py`, `engine.py` | `tests/test_plan_mode.py` |
| | **T2.3** | **التوجيه اللحظي (Steering Support)**<br>إتاحة استقبال تعليمات توجيهية أثناء تشغيل الـ Loop وتطبيقها على الخطوات المتبقية دون إعادة التشغيل. | `src/ai_code_engineer/engine.py` | `tests/test_steering.py` |
| | **T2.4** | **نظام الموافقات بزر واحد (One-Key Approvals)**<br>تطبيق نظام الموافقة السريعة `[y]` / `[n]` / `[d]` في الـ CLI وبطاقة الموافقة في الـ Web عبر حدث `approval_requested`. | `src/ai_code_engineer/cli.py`, `webapp/controller.py` | `tests/test_approvals.py` |
| **Release 3** | **T3.1** | **تحديث خادم البث (Web SSE Event Pipeline)**<br>ربط تدفق الأحداث الجديد مباشرة بـ SSE Hub في `server.py` مع دعم إعادة الاتصال. | `src/ai_code_engineer/webapp/server.py` | `tests/test_webapp_sse.py` |
| | **T3.2** | **التخطيط الثلاثي لواجهة الويب (3-Column Layout)**<br>تحديث واجهة الويب لتقسيم الشاشة: يسار (Plan Checklist)، وسط (Event Timeline)، يمين (Changes & Diffs). | `src/ai_code_engineer/webapp/static/index.html`, `app.css` | فحص العرض والتجاوب |
| | **T3.3** | **الشريط العلوي وعناصر التحكم الدائمة (Top Bar & Controls)**<br>إظهار المهمة والمرحلة والوقت المنقضي بشكل دائم، مع مدخل التوجيه (Steer) وزر الإيقاف (Stop). | `src/ai_code_engineer/webapp/static/ui-run.js` | فحص التفاعل اللحظي |
| **Release 4** | **T4.1** | **عارض الفروقات الملون (Colored Diff View)**<br>عرض ملخص التغييرات لكل ملف، مع دعم عارض الفروق بـ 3 أسطر سياق في الـ CLI عبر `Rich.Syntax` وفي الويب. | `src/ai_code_engineer/diff_view.py` | `tests/test_diff_view.py` |
| | **T4.2** | **هيكلة الأخطاء الودية (Friendly Error Formatter)**<br>تنسيق الأخطاء في هيكل (Problem / Root cause / Agent action) مع إخفاء الـ Traceback خلف أمر `details`. | `src/ai_code_engineer/error_fmt.py` | `tests/test_friendly_errors.py` |
| | **T4.3** | **حزمة الأوامر الموحدة (Unified Commands Handler)**<br>تنفيذ الأوامر الـ 12 الموحدة (`status`, `plan`, `changes`, `diff`, `tests`, `risks`, `stop`, `steer`, `report`, `undo`, `memory`, `help`). | `src/ai_code_engineer/commands.py` | `tests/test_commands.py` |
| **Release 5** | **T5.1** | **اختصارات لوحة المفاتيح عبر `prompt_toolkit`**<br>ربط الاختصارات في الـ CLI (`Esc` للإيقاف، `Tab` للتبديل، `?` للمساعدة) والتوافق مع الويب. | `src/ai_code_engineer/cli.py` | `tests/test_cli_shortcuts.py` |
| | **T5.2** | **بيئات الأنابيب و CI واللاألوان (Non-TTY & NO_COLOR)**<br>الكشف التلقائي عن بيئات الأنابيب (CI/Pipes) لتعطيل الـ Spinners واحترام متغير `NO_COLOR` وضيق الشاشة. | `src/ai_code_engineer/terminal.py` | `tests/test_terminal_modes.py` |
| | **T5.3** | **بانر الإطلاق الموحد (Startup Banner)**<br>عرض اسم المشروع، النموذج، والوضع (Plan/Execute) بشكل نظيف وأنيق عند الإطلاق. | `src/ai_code_engineer/cli_view.py` | `tests/test_banner.py` |
| **Release 6** | **T6.1** | **محرك الذاكرة ومخزن الملفات (MemoryStore)**<br>بناء `MemoryStore` لإدارة الملفين: `.agent/memory/project.md` و `.agent/memory/chats/<chat_id>.md`. | `src/ai_code_engineer/memory_store.py` | `tests/test_memory_store.py` |
| | **T6.2** | **حماية الهدف والتلخيص المضغوط (Goal Protection & Summarizer)**<br>منع تعديل الهدف إلا بموافقة المستخدم الصريحة، وتلخيص الخطوات مع سقف 1500 توكن للمحلي. | `src/ai_code_engineer/memory_summarizer.py` | `tests/test_memory_limits.py` |
| | **T6.3** | **حقن البوصلة والربط بالتدفق والأوامر (Compass Injection & UI Panel)**<br>حقن نصوص البوصلة في الـ Prompt، ودعم أوامر `memory`, `memory edit`, `memory reset`، ولوحة الذاكرة بالويب. | `src/ai_code_engineer/compass.py`, `context_builder.py` | `tests/test_compass_injection.py` |

---

> [!IMPORTANT]
> **التزاماً بتعليمات الخطوة الأولى (STEP 1 - AUDIT):**
> لم يتم إجراء أي تغيير على كود المشروع البرمجي حتى الآن.
> **أنا في انتظار موافقتك وإشارتك للبدء في تنفيذ الإصدار الأول (Release 1: Event Schema + CLI Status Line + Colors/Symbols + Final Report).**