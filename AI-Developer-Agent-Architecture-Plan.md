# AI Developer Agent — خطة المعمارية والتنفيذ

**الجمهور:** فريق Java/Spring يعمل في بيئة بنكية.  
**آخر تحديث:** 23 سبتمبر 2026 — واجهة UI 2.1 وحالة التنفيذ الفعلية.  
**آخر قرار معتمد:** Python لتنفيذ الـAgent بهدف تسريع النسخة الأولى، مع Ollama Local + OpenRouter. المستودعات المستهدفة تظل Java/Spring.  
**الهدف:** بناء Agent للمطورين يستطيع فهم المستودع، اقتراح خطة، تعديل الكود، وتشغيل التحقق، بنفس منطق التشغيل سواء استخدم نموذجًا محليًا أو خدمة سحابية.

هذه الوثيقة تجمع محاور محادثة «ترحيب ودي» وتحوّلها إلى خطة تنفيذ. القسم التالي يصف ما نُفذ فعلًا؛ بقية المعمارية تصف الهدف الكامل، ولا تعني أن كل مكون أصبح جاهزًا. أمثلة العقود وPython وYAML في أقسام التصميم توضيحية ما لم يذكر سجل التنفيذ خلاف ذلك.

## 0. سجل التنفيذ والقرارات المعتمدة — 23 سبتمبر 2026

### تحديث UI 2.3 — مشروعات وشاتات بسياق منفصل

- الشريط الجانبي أصبح شجرة **Projects & chats**: لكل مشروع شاتاته، ويمكن فتح/طي المشروع واختيار الشات أو إنشاء **New chat** تحته.
- هوية المشروع هي المسار المطلق بعد تسويته، مع تجاهل اختلاف حالة الأحرف على Windows؛ الاسم وحده ليس هوية، ولذلك لا تُدمج مجلدات مختلفة متشابهة الاسم.
- لكل شات `chat_id` مستقل، ولكل طلب جلسة تنفيذ مستقلة محفوظة مع جذر المشروع وهوية الشات. هوية الشات تدخل في بصمة الاقتراح الجديد.
- متابعة شات قائم تضيف إلى طلب الموديل ملخص آخر أدواره فقط (حتى 6 أدوار، و4000 حرف أو سدس ميزانية السياق أيهما أصغر). يشترط تطابق **جذر المشروع وهوية الشات معًا**. لا يجري جمع تاريخ كل شاتات المشروع أو أي مشروع آخر.
- سجل السياق يضم الطلب والحالة والملخص فقط؛ لا يعيد إرسال snapshots أو diffs أو الملفات القديمة. يقرأ الوكيل الملفات الحالية، ويميّز الاقتراح غير المطبق عن التغييرات المطبقة. يسجل معرفات الجلسات المستخدمة في `context_session_ids`.
- تغيير المشروع يمسح مسودة الطلب والخطة المرفقة والمحادثة والنشاط والموافقة السحابية وحالة المراجعة. New chat يبدأ تاريخًا جديدًا داخل المشروع الحالي. فتح شات محفوظ يعيد مشروعه وخطته وتاريخه.
- الشاتات القديمة تُجمع حسب جذر مشروعها، وتُعامل الجلسة القديمة كشات مستقل بمعرف الجلسة دون تعديل ملفاتها أو بصماتها.
- تُحفظ قائمة مسارات المشروعات في `.agent-projects.json`، ويُحظر هذا الملف على أدوات الموديل مثل `.agent-runs`. الجلسات تظل في مخزن التطبيق المحلي الحالي مع فصل منطقي بالسجل؛ لم نضف عزل مستخدمي نظام التشغيل أو قاعدة بيانات منفصلة لكل مشروع.
- فُحصت بيانات الطلب الفعلية عبر مزود اختباري: لم يدخل تاريخ مشروع آخر أو شات آخر في السياق، حتى عند تشابه معرف الشات بين مشروعين. نجحت فحوص الشجرة والتبديل ومحو السياق القديم والتوافق مع الجلسات القديمة، ونجحت 28 حالة اختبار للنواة. لم يُشغّل موديل حقيقي أو تطبيق Spring.
- هذا التحديث يحل محل وصف UI 2.1 الذي قال إن كل إرسال بلا تاريخ؛ الآن الاستمرار داخل الشات يستخدم التاريخ المحدود المذكور أعلاه. النسخة الحالية في عنوان النافذة **UI 2.3**.

### تحديث UI 2.2 — النسخ واللصق

- استبدال نص الرسائل المرسوم بعناصر نصية للقراءة فقط تدعم تحديد النص بالماوس وCtrl+A ونسخه بـCtrl+C، مع الإبقاء على شكل الفقاعات.
- إضافة قائمة كليك يمين للرسائل: Copy / Select all، ولصندوق الكتابة: Cut / Copy / Paste / Select all. اللصق في صندوق الطلب، وليس داخل الردود المعروضة.
- منع القطع واللصق في صندوق الطلب أثناء تعطيله خلال العمل. تم إعادة فحوص تخطيط الواجهة والتنقل وحالات الانشغال بنجاح دون توليد أو تغيير ملفات المشروع التجريبي.
- عنوان النسخة الأحدث **AI Code Engineer · UI 2.2**؛ قسم UI 2.1 أدناه يصف التصميم الذي بُني عليه هذا التحديث.

### مكان التشغيل والنسخة الحالية

- المشروع الفعلي: `D:\AI\AI-Agent`، والتشغيل من `Run-Agent.bat` الموجود داخله.
- الواجهة الحالية: **UI 2.1**؛ رقمها ظاهر في عنوان النافذة للتمييز عن النسخ المفتوحة القديمة. يجب إغلاق النافذة القديمة وإعادة تشغيل الباتش بعد تحديث الملفات.
- `Run-Agent-CLI.bat` يفتح القائمة النصية. لا توجد حاجة إلى تثبيت مكتبات واجهة خارجية؛ الواجهة الحالية Python/Tkinter.
- المستودع التجريبي الحالي: `examples/demo2` والخطة المرجعية له `examples/demo2/plan.md`. هذه خطة تطبيق Spring منفصلة، وليست خطة تطوير الـAgent نفسه، ولم تُعدّل ضمن تحديث الواجهة.

### ما نُفذ بالفعل

| المحور | التنفيذ الحالي |
|---|---|
| اللغة | Python 3.11+ لتسريع النسخة الأولى، مع استمرار استهداف مشروعات Java/Spring. Python اختيار تنفيذي وليست شرطًا معماريًا. |
| المزودون | Ollama محليًا وOpenRouter للسحابة، من خلال طبقة مزود مستقلة عن واجهة المستخدم. |
| اختيار الموديل | قراءة قائمة Ollama عند بدء التشغيل وعند Refresh؛ المستخدم يختار من كل العناصر التي تعيدها الخدمة. قائمة OpenRouter حية، مع أوضاع Free/Paid والبحث بالاسم ومعلومات السياق والسعر المتاحة. |
| ضوابط السحابة | موافقة صريحة على إرسال كود عام/تجريبي؛ نماذج Ollama السحابية تخضع لنفس الضابط. الوضع المدفوع اختيار صريح ولا يوجد انتقال تلقائي إليه. مفتاح الواجهة لا يُحفظ على القرص. |
| حلقة الوكيل | إجراءات JSON محددة، قراءة/بحث/قائمة ملفات، اقتراح أو إعلان تعذر التنفيذ؛ حدود لعدد الأدوار ومحاولات الإجراءات غير الصالحة. لا توجد صلاحية shell للموديل. |
| السياق | Repo map مع بحث نصي وقراءة مقيدة بالسياسة؛ لا يوجد AST/LSP أو vector index بعد. |
| الملفات الجديدة | قراءة ملف غير موجود ترجع `not_found` وإشارة سياسة لإمكانية إنشائه بدل إفشال المهمة. الإنشاء يُقترح أولًا ويُطبق بعد الموافقة. |
| إرفاق الخطة | Attach/View/Clear لملف Markdown أو نص داخل المشروع؛ المحتوى يدخل سياق الموديل منفصلًا عن حد وصف المهمة (4000 حرف)، وبحد مستقل لحجم المرجع. طلب المستخدم الحالي يحدد المرحلة ويتقدم على تعليمات مرحلة قديمة داخل الخطة. |
| حماية المرجع | خطة مرفقة للقراءة فقط، يُسجل مسارها وبصمتها في الجلسة، ولا يسمح للاقتراح بتعديلها، وتُراجع البصمة قبل التطبيق. CLI يدعم `--plan-file`. |
| المراجعة | اقتراح محفوظ في `.agent-runs/<id>/session.json`، ملخص وDiff وBefore/After؛ موافقة مرتبطة ببصمة الاقتراح ثم فحص الملفات قبل الكتابة. الحد الحالي 8 ملفات في الاقتراح الواحد. |
| التراجع | استعادة التغييرات الخاصة بالجلسة مع حماية تعديلات المستخدم اللاحقة. حفظ حالات التطبيق الجزئي أو المنقطع للمراجعة. |
| التحقق | فحص صياغة اختياري ووصفات build/test ثابتة عبر Docker فقط؛ لا تُدّعى نتائج اختبارات مشروع لم تُنفذ. غياب العزل المطلوب يمنع تشغيل الوصفات. |
| التشغيل الخلفي | العمل الطويل في thread وخانة نشاط؛ إيقاف التخطيط عند حد آمن بعد اكتمال الطلب الجاري. |

### الواجهة الحالية UI 2.1

الواجهة باللغة الإنجليزية، والتصميم مستوحى من الصورة التي اختارها المستخدم:

- شريط جانبي فاتح للمشروع والمهام الحديثة وفتح مهمة محفوظة.
- مساحة محادثة برسائل منفصلة وفقاعات زرقاء للطلب؛ صندوق كتابة منخفض بحواف دائرية في الأسفل.
- زر إرفاق الخطة واختيار الموديل بجانب الكتابة، بدل عرض نموذج إعدادات طويل طوال الوقت.
- الضغط على اسم الموديل أو **Project & model settings** يفتح إعدادات المشروع والمزود والموديل والبحث والمفتاح والموافقة السحابية.
- بطاقة **Outputs / Sources** على اليمين لعرض الملفات المقترحة والخطة. تُخفى في النوافذ الضيقة لإبقاء صندوق الكتابة قابلًا للاستخدام؛ الإعدادات والإرفاق يظلان متاحين من الأسفل.
- تنقل خفيف **Chat / Changes / Activity** بدل تبويبات كبيرة محاطة بحدود. مراجعة الملفات والتطبيق والتراجع داخل Changes.
- **Ctrl+Enter** يرسل طلب اقتراح، وEnter يضيف سطرًا. زر Stop يظهر فقط أثناء عملية يمكن إيقافها.
- هذه واجهة مهام واقتراحات وليست محادثة عامة ذات ذاكرة ممتدة: المشروع والخطة يظلان في النموذج، لكن كل إرسال يبدأ اقتراحًا جديدًا بناءً على الطلب والملفات الحالية.

### سير عمل التطبيق التجريبي

1. افتح `Run-Agent.bat` وتأكد من ظهور **UI 2.1** في العنوان.
2. اختر `D:\AI\AI-Agent\examples\demo2` ثم أرفق `plan.md` الموجود فيه.
3. اختر موديلًا من قائمة Ollama أو أحد أوضاع OpenRouter حسب تصنيف البيانات والميزانية.
4. اكتب المرحلة المطلوبة صراحة، مثل: `Read the attached plan and inspect the current project. Implement Phase 1 only. Preserve existing work. Do not run builds, tests, or the application.`
5. أرسل الطلب، وافتح Changes لمراجعة الملفات؛ ضع علامة الموافقة ثم Apply.
6. جرّب التطبيق يدويًا أو شغّل تحققًا مأذونًا به، ثم اطلب المرحلة التالية. نجاح إنشاء الاقتراح لا يعني نجاح تشغيل التطبيق.

### ما جرى التحقق منه وحدوده

- كانت هناك نتائج تحقق سابقة للنواة، لكنها لا تثبت سلامة كل تحديث لاحق. تحديثات اختيار الموديلات والملفات الجديدة وإرفاق الخطة نُفذت أولًا دون اختبارات بناءً على طلب المستخدم وقتها.
- المستخدم سمح لاحقًا بتشغيل وتجربة الواجهة. في UI 2.1 نجح فحص صياغة الملف وتشغيل Tk وفحص تخطيط فعلي عند 1500×880 و1000×650 و1850×950.
- نجح فحص فتح الإعدادات، عرض اختيار موديل ببيانات اختبار محلية، حالات busy/Stop، الانتقال إلى Changes، بدء مهمة جديدة، وقراءة `demo2/plan.md` دون تعديله.
- جرى تشغيل نافذة معاينة؛ **لم تكتمل معاينة صورة النافذة**: أداة Windows أعادت مهلة إذن ثم `no screenshot targets found`. لا يُعد فحص المقاسات موافقة بصرية نهائية.
- لم يُرسل طلب توليد للتحقق من هذا التحديث، ولم تُطبق تغييرات على تطبيق Spring، ولم تُشغّل build أو اختبارات المشروع التجريبي.

### العمل المتبقي وأولوية المرحلة التالية

1. إكمال معاينة الواجهة بصريًا على جهاز المستخدم وتجربة المسار الكامل: Attach → اختيار الموديل → اقتراح → مراجعة → تطبيق.
2. تجربة Spring authentication على مراحل صغيرة وفق `examples/demo2/plan.md`، وعدم اعتماد التطبيق قبل تحقق تشغيل فعلي.
3. إثبات عزل Docker على جهاز مجهز وإضافة تقارير تحقق قابلة للقراءة آليًا؛ لا تزال فحوص Gradle بحاجة إلى استكمال.
4. إضافة دورة إصلاح محدودة وموافقات جديدة للاقتراحات البديلة، وتحسين قدرات الأدوات والسياق حسب القياسات.
5. إضافة workspace locks/worktrees واستعادة أكثر متانة للجلسات، ثم Skills وMCP والتكامل مع IDE.
6. استكمال متطلبات الإنتاج البنكي: SSO، تشفير واحتفاظ وتدقيق للجلسات، سياسة خروج بيانات مركزية، تقييمات للموديلات والمهام، واختبارات عزل وأمن. النسخة الحالية MVP وليست منتجًا بنكيًا جاهزًا للإنتاج.

**قاعدة توثيق:** عند التعارض مع وصف مستقبلي في الأقسام التالية، هذا السجل هو مرجع ما نُفذ بالفعل. لا يُعلّم أي بند إنتاجي مكتملًا دون دليل تحقق مناسب.

## 1. القرار المعماري

نبني **Agent Runtime مستقلًا عن مزود الموديل**، ونبدأ بـAgent واحد له قدرات تخطيط وتنفيذ واختبار ومراجعة. نفصل المكونات داخليًا، دون البدء بمنظومة microservices أو عدة agents.

الموديل يقترح الخطوة؛ الـRuntime يتحقق من صحتها وصلاحيتها وينفذها ويراقب نتيجتها. جودة كتابة الكود وحدها لا تكفي: النجاح هو تغيير قابل للمراجعة، بأدلة تحقق، داخل حدود الأمان والتكلفة.

```text
Developer → CLI / Web / IDE
                  │
          Agent Orchestrator ───── Session / Event Store
                  │
       ┌──────────┼───────────────┐
       │          │               │
 Context Engine  Planner       Skills Router
       │          │               │
 Repo Map       Plan + Scope    Versioned Skills
 Search           │
       └──────────┼────────────────┘
                  │
             Model Gateway
        ┌─────────┴──────────┐
   Local Provider       Cloud Provider
        └─────────┬──────────┘
            Proposed tool call
                  │
        Schema Validation + Policy Engine
                  │
          Approval, if required
                  │
              Tool Runtime
       ┌──────────┴───────────┐
 Built-in Tools          MCP Adapter
       └──────────┬───────────┘
          Isolated Worker / Sandbox
                  │
       Observe → Verify → Fix / Report
```

كل مسارات التنفيذ، بما فيها MCP وhooks وscripts، تمر عبر السياسة. استدعاءات الموديل نفسها تمر عبر ضوابط تصنيف البيانات وخروجها. الـSandbox حد أمني فعلي؛ Git worktree وسيلة عزل للتغييرات فقط.

## 2. لماذا Python؟ وهل هي مطلوبة؟

**Python ليست شرطًا.** استخدامها في الهيكل السابق كان مثالًا لتسريع التجريب، وليس اعتمادًا معماريًا. تشغيل inference في خدمة منفصلة يسمح بكتابة الـAgent بأي لغة مناسبة.

| الجانب | Python | Java | Kotlin |
|---|---|---|---|
| التجريب وبحث AI | مناسبة جدًا وتجاربها عادة مختصرة | مناسبة، مع إعداد أكثر أحيانًا | كود مختصر مع مكتبات JVM |
| الملاءمة لفريق Java/Spring | لغة وأدوات تشغيل إضافية | الأقرب لمهارات الفريق وإجراءاته | جيدة إذا كانت مستخدمة بالفعل |
| العقود وإعادة الهيكلة | typing واختبارات وأدوات فحص ضرورية | static typing وعقود واضحة | static typing وnull safety على مستوى اللغة |
| التكامل المؤسسي | ممكن، لكنه يحتاج توحيدًا مع منصة البنك | مناسب لمنظومة Spring الحالية | تكامل JVM/Spring مع حاجة لخبرة Kotlin |
| التشغيل والصيانة | إدارة بيئة وحزم Python | نفس أدوات JVM والمراقبة المعتادة | نفس منصة JVM مع اعتبارات توافق المكتبات |
| تدريب النماذج والتجارب المتخصصة | غالبًا الاختيار الأسهل | استهلاك خدمات inference غالبًا أنسب | مثل Java في هذا الجانب |

**القرار المعتمد من المستخدم:** Python لتنفيذ الـAgent وتسريع بناء النسخة الأولى. كانت Java/Spring توصية سابقة لتقليل اختلاف تقنيات التشغيل، لكنها أصبحت بديلًا للمقارنة وليست خطة التنفيذ. نستخدم typing وعقودًا واضحة واختبارات وحدات وتكامل، ونثبت dependencies لضمان قابلية الصيانة. لا يلزم أن تطابق لغة الـAgent لغة المستودعات التي يعدلها؛ سيستمر في خدمة Java/Spring وتشغيل Maven/Gradle داخل worker معزول.

نبدأ بحزمة Python واحدة ذات modules واضحة، وCLI وprovider adapters منفصلة، دون اشتراط framework للـAgents. نستخدم مكتبات HTTP/validation معتمدة عند التنفيذ، ونحتفظ بمنطق orchestration والسياسة داخل المشروع. Spring AI مرجع للبديل JVM فقط، وليس dependency في التنفيذ المعتمد. [مرجع Spring AI](https://docs.spring.io/spring-ai/reference/).

لغة الـRuntime ليست معيار اختيار الموديل. نقيس الموديل على مستودعات Java/Kotlin الفعلية، ودقة الأدوات والتعديلات، والزمن، والذاكرة، والتكلفة؛ لا نفترض أن نموذجًا محليًا صغيرًا يعادل نموذجًا سحابيًا.

## 3. حدود المكونات ومسؤولياتها

| المكوّن | مسؤوليته | ما لا يملكه |
|---|---|---|
| Orchestrator | حالة المهمة، الانتقالات، الميزانيات، الإلغاء | تنفيذ shell مباشرة |
| Planner | هدف قابل للاختبار، ملفات متوقعة، مخاطر وتحقق | منح صلاحيات |
| Context Engine | بحث وفهرسة وانتقاء سياق موثق | رفع كامل المستودع تلقائيًا |
| Model Gateway | توحيد الطلبات والنتائج والقدرات | تجاوز سياسة خروج البيانات |
| Policy Engine | Allow / Deny / RequireApproval | الاعتماد على وعد الموديل بالأمان |
| Tool Runtime | validation، execution، timeouts، نتائج منظمة | توسيع نطاق الطلب |
| Verifier | تشغيل checks وتقييم الأدلة | اعتبار كلام الموديل دليل نجاح |
| Session Store | checkpoints وقرارات وأحداث قابلة للاستئناف | تخزين أسرار أو تفكير داخلي خام |

ابدأ بتطبيق modular monolith للتحكم وworker منفصل للعمل غير الموثوق. لا تشغّل build المشروع داخل عملية الخدمة التي تحمل credentials الإدارة.

## 4. Model abstraction والعقود

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ModelCapabilities:
    tool_calling: bool
    structured_output: bool
    streaming: bool
    context_window_tokens: int


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, object]


class ModelProvider(Protocol):
    def capabilities(self) -> ModelCapabilities: ...

    async def generate(
        self, request: ModelRequest, cancellation: CancellationToken
    ) -> ModelTurn: ...
```

تعريفات `ModelRequest` و`ModelTurn` و`CancellationToken` جزء من implementation وليست أنواعًا جاهزة في Python. هذا مثال لعقد typing وليس تطبيقًا مستقلًا قابلًا للتشغيل. يشمل الطلب الرسائل، schemas للأدوات المسموحة، deadline وميزانية المخرجات. تشمل النتيجة النص وطلبات الأدوات وسبب التوقف وusage إن توفر. نتحقق من المدخلات والنتائج وقت التشغيل؛ type hints وحدها لا تفرض صحة البيانات.

المحولات المعتمدة للنسخة الأولى: `OllamaProvider` و`OpenRouterProvider` خلف نفس `ModelProvider`. يمكن إعادة استخدام transport متوافق مع OpenAI داخل محول OpenRouter، مع إبقاء إعدادات routing والخصوصية خاصة به. نؤجل `OpenAiProvider` المباشر وGemini/Groq/Azure إلى وجود حاجة فعلية. ويمكن إضافة محول لخوادم محلية مثل نشر vLLM مختار لاحقًا. تشابه API لا يضمن تطابق capabilities أو token accounting أو أخطاء streaming.

قواعد التنفيذ:

- اختر provider/model بإعدادات معتمدة؛ لا تنشر أسماء موديلات ثابتة في منطق الـAgent.
- نفّذ contract tests لكل model + server version، تشمل tool calls متعددة، JSON غير صالح، timeout، truncation والإلغاء.
- اجمع tool arguments كاملة ثم تحقق منها؛ لا تنفذ أجزاء streaming.
- ارفض tool name غير معروف أو arguments خارج schema. اسمح بمحاولة تصحيح محدودة للرد غير الصالح، دون استخراج shell من نص حر.
- إذا كان الموديل لا يجتاز اختبارات الأدوات، اجعله للاستشارة فقط أو استخدم structured action schema مثبت الاختبار؛ لا تمنحه تنفيذًا عامًا.
- أخطاء الاتصال تستعمل backoff محدودًا. إعادة توليد رد قد تكلف مالًا؛ إعادة تنفيذ أداة ذات أثر جانبي تحتاج مصالحة للحالة قبل أي retry.
- لا يوجد fallback صامت من local إلى cloud. تغيير جهة معالجة البيانات يحتاج سياسة صريحة وصلاحية مناسبة.

في function calling ينتج الموديل طلب الأداة ويقوم التطبيق بتنفيذها ثم يعيد النتيجة المرتبطة بمعرّف الطلب. صلاحية JSON لا تعني أن العملية مصرح بها. [مرجع OpenAI](https://developers.openai.com/api/docs/guides/function-calling). وOllama يوفر مسار tool calling للنماذج الداعمة؛ يجب اختبار النموذج المختار نفسه. [مرجع Ollama](https://docs.ollama.com/capabilities/tool-calling).

## 5. Agent loop وحالة المهمة

```text
CREATED → DISCOVERING → PLANNING → [WAITING_APPROVAL]
        → EXECUTING → VERIFYING → COMPLETED
                         │
                         └→ REPAIRING → VERIFYING

Any active state → CANCELLED / FAILED / BLOCKED
```

1. سجّل هوية المستخدم والمستودع والتصنيف والطلب ومعايير القبول.
2. التقط baseline: commit، حالة الملفات والتغييرات الموجودة، وإصدارات build/model/policy.
3. اجمع السياق، واقترح خطة محددة بالنطاق.
4. احصل على الموافقة إن تطلبت السياسة؛ احفظها بمدة ونطاق واضحين.
5. في كل دورة: ابنِ السياق → اطلب خطوة → تحقق من tool call → قيّم السياسة → نفّذ → سجّل النتيجة.
6. نفذ التحقق عند اكتمال التعديلات، وأصلح ضمن حدود المحاولات.
7. أنشئ تقريرًا صادقًا بالتغييرات ونتائج checks وما لم يتم التحقق منه.

```text
while task is active and budget remains:
    proposed = model.generate(context.allowedView())
    if proposed requests completion:
        run required verification and evaluate acceptance criteria
    else:
        validate schema and resolve canonical targets
        decision = policy.evaluate(identity, scope, targets, action)
        enforce decision and any bound approval
        persist execution intent
        execute once in sandbox; record observation and checkpoint
```

قيم بداية مقترحة، تضبط بالتجربة: 30 model turns، 60 tool calls، 3 repair attempts، و20 دقيقة للمهمة، مع timeouts منفصلة للبناء. اكتشف تكرار نفس الفشل ونفس patch وتوقف برسالة واضحة. نفاد الميزانية لا يتحول إلى نجاح.

خزّن `taskId`, `toolCallId`, state, baseline, plan version, approvals, policy version, model version, artifact hashes ونتائج الأدوات. عند الانقطاع استأنف من checkpoint؛ reconcile أي عملية غير مؤكدة بدل تكرار push أو إنشاء طلب خارجي. استخدم idempotency keys حيث يدعم النظام المستهدف ذلك.

## 6. Context engineering وRepo Map

ابدأ بـripgrep + Git metadata + build metadata، ثم parser للرموز. لا تجعل vector database شرطًا للنسخة الأولى.

```text
Repo snapshot → File inventory → Symbol index → Repo map
Task terms → lexical search → related symbols/tests → selected excerpts
```

لكل symbol احفظ: الاسم، النوع، التوقيع، الملف ونطاق الأسطر، module، hash الملف، وروابط محتملة للمراجع والاختبارات. Repo Map ملخص للهيكل، وليس نسخة مضغوطة من كل الكود. استخدام خرائط الرموز والسياق المحدود له مثال عملي في [Aider repository map](https://aider.chat/docs/repomap.html).

لفريق Spring:

- اكتشف Maven/Gradle modules وJava/Kotlin versions وtest source sets.
- اربط controller endpoint بـDTO وservice وrepository والاختبارات المرتبطة قدر الإمكان.
- Tree-sitter مناسب للاستخراج البنيوي المتعدد اللغات؛ JavaParser خيار Java، وLSP يفيد لاحقًا في references والدلالات.
- لا تعتبر العلاقات المكتشفة يقينية مع reflection وSpring injection وgenerated code؛ ارجع إلى المصدر والاختبارات.
- أعد فهرسة الملفات المعدلة بالـhash، وافصل caches لكل repo/branch/task، وأعد قراءة المصدر قبل patch.
- استبعد `.git`، build outputs، binaries، credentials، وملفات البيانات الحساسة قبل الفهرسة والإرسال. طبّق قيود القراءة حتى على الملفات غير المتتبعة وsymlinks.
- أضف embeddings محلية فقط إذا أثبتت evals أن البحث المعجمي والرموز غير كافيين. تعامل معها كبيانات مشتقة حساسة لها صلاحيات وretention.

مثال repo map:

```text
payments-api
  TransferController.transfer(TransferRequest)
    → TransferService.executeTransfer(...)
  TransferRequest(amount, fromAccount, toAccount)
  TransferServiceTest
```

احجز من context window مساحة للمخرجات ونتائج الأدوات المقبلة. مثال مبدئي من المساحة المتاحة للمدخلات: 15% سياسات وعقد المهمة، 15% خطة وملخص، 50% كود واختبارات ذات صلة، 20% نتائج حديثة. النسب تجريبية. لخّص المخرجات الطويلة مع مرجع للملف الكامل؛ لا تحذف الأخطاء الحاسمة ولا تخزن summaries باعتبارها مصدر الحقيقة.

## 7. الأدوات وTool Registry

| المجموعة | أدوات البداية | الضوابط |
|---|---|---|
| قراءة | list_files, read_file, search_code, find_symbol | مسارات مصرح بها وحدود حجم |
| تعديل | create_file, apply_patch | expected hash، حدود diff، كتابة atomic |
| Git | git_status, git_diff | repo محدد وقراءة metadata اللازمة |
| تحقق | compile_project, run_tests, run_test, run_lint | build recipes معتمدة داخل sandbox |
| تبعيات | read_build_file, inspect_dependency | cache/mirror معتمد، لا تنزيل خفي |
| Shell | restricted_shell | مغلق افتراضيًا، executable وargv محددان |

كل Tool Definition يتضمن JSON Schema، وصفًا دقيقًا، أثرًا جانبيًا، required permissions، timeout، output limit، وإمكانية idempotency. النتيجة تشمل `status`, `exitCode`, `duration`, `stdout/stderr` المنقحين، `artifacts`, `changedPaths`, `truncated` ومعرّف الخطأ.

`apply_patch` يتحقق من محتوى الملف المتوقع، ويمنع overwrite عند تغيّر الملف من المستخدم أو أداة أخرى. نفّذ التعديلات متسلسلة لكل workspace. يمكن تشغيل قراءات مستقلة بالتوازي بعد ضبط snapshot consistency.

شغّل الأوامر بصيغة executable + argument array، دون تركيب shell string من كلام الموديل. allowlist لكلمة `mvn` أو `gradle` وحدها غير كافية: build plugins وtests وwrappers تشغّل كودًا تعسفيًا. العزل مطلوب حتى مع أدوات build المسموح بها.

## 8. Permissions وPolicy Engine

السياسة كود حتمي خارج الموديل، وقراراتها `ALLOW`, `DENY`, `REQUIRE_APPROVAL`. كل deny له سبب قابل للعرض؛ أي خطأ في السياسة أو إعداد غير معروف يؤدي إلى fail closed.

| المستوى | أمثلة | القرار الافتراضي المقترح |
|---|---|---|
| Read | قراءة كود مسموح، بحث، diff | تلقائي داخل نطاق المهمة |
| Write | patch/create | داخل خطة مصرح بها وworkspace معزول |
| Execute | compile/tests | worker معزول ووصفة معتمدة |
| External effect | push، إرسال بيانات، تعديل issue | موافقة محددة على العملية والمحتوى والوجهة |
| Production | نشر production، قواعد بيانات حقيقية، أسرار تشغيل | ممنوع على الـAgent في النطاق الأول |

ترتيب الثقة المقترح: سياسة المؤسسة → إعدادات المشروع الموثوقة → نطاق المستخدم المصرح → تعليمات repo/skills المراجعة → مخرجات النموذج والمحتوى الخارجي غير الموثوق. `AGENTS.md` يصف قواعد العمل لكنه لا يستطيع توسيع صلاحيات المؤسسة. أي طلب أعلى صلاحية يحتاج قناة إدارية مستقلة.

حماية عملية:

- حل canonical path وافحص traversal وsymlinks وWindows junctions؛ لا تعتمد على string prefix فقط.
- أعد فحص الهدف عند فتحه وتنفيذه لتقليل race conditions؛ امنع mount أو رابط خارج workspace.
- افصل صلاحية قراءة secret عن صلاحية إرسال محتوى إلى model provider.
- قيّد CPU/RAM/processes/disk/time، امنع privilege escalation وhost mounts وDocker socket.
- عامل scripts وMCP responses وREADME وتعليقات الكود كبيانات قد تحتوي prompt injection.
- استخدم هويات قصيرة العمر محدودة الصلاحيات؛ لا ترث environment المستخدم بالكامل.

## 9. التخطيط والموافقة

الخطة كيان محفوظ وليست فقرة دردشة فقط. تحتوي الهدف، معايير القبول، الملفات المتوقعة، الأدوات، البيانات الخارجة، الاختبارات، المخاطر، الاستثناءات وrollback.

```yaml
planVersion: 1
objective: Validate transfer amount before service invocation
expectedFiles:
  - payments-api/src/main/java/example/TransferRequest.java
  - payments-api/src/test/java/example/TransferControllerTest.java
acceptance:
  - Zero and negative amounts receive the existing validation error contract
  - Valid requests preserve current behavior
checks:
  - compile
  - focused-unit-tests
  - api-contract-tests
risk: medium
```

في الوضع البنكي الأول: قراءة وتحليل تلقائيان، ثم موافقة خطة قبل التعديل. لاحقًا يمكن اعتماد auto-edit للتغييرات الصغيرة المحددة بسياسة الفريق. لا نطلب نفس الموافقة مرارًا داخل النطاق؛ تغيّر ملفات محمية أو وجهة بيانات أو أثر خارجي يعيد التقييم.

اربط approval بهوية المستخدم، task، plan hash، نوع الفعل، args/targets، صلاحية زمنية وpolicy version. الموافقة على خطة تعديل لا تعني الإذن بالنشر أو push. العمليات الخارجية تعرض المحتوى والوجهة النهائية قبل تنفيذها.

## 10. Skills وتعليمات المستودع وHooks

Skills حزم معرفة إجرائية صغيرة، تُحمّل حسب الحاجة بدل system prompt ضخم:

```text
skills/
  task-intake/SKILL.md
  java/SKILL.md
  kotlin/SKILL.md
  spring/SKILL.md
  testing/SKILL.md
  security/SKILL.md
  code-review/SKILL.md
  api-compatibility/SKILL.md
  debugging/SKILL.md
```

لكل skill: وصف ونطاق انطباق، version/hash، خطوات، references، ومعايير تحقق. سجّل سبب اختيارها. مثال: تعديل login response يحمّل Spring وsecurity وAPI compatibility، دون تحميل Kotlin إذا لم يكن له صلة.

راجِع مصدر skill وأي scripts مرفقة بها، وثبّت إصدارات الحزم المعتمدة. Skills تقدم إرشادات؛ لا تمنح صلاحيات. اكتشاف تعليمات repo يكون من المسار الموثوق للأعلى/للملف حسب قواعد ثابتة ومعلنة، مع إظهار التعارض.

Hooks مثل `pre_tool`, `post_edit`, `pre_report` قد تنفذ فحصًا إضافيًا أو تحجب إجراءً. لا تسمح لـhook بتجاوز policy أو تنفيذ كود خارج sandbox. فشل hook أمني مطلوب يمنع الإكمال.

## 11. Verification loop

```text
Patch → Compile → Lint / Static checks → Unit tests
      → Relevant integration / contract tests → Security checks
      → Diff review → Acceptance decision
                      │
                      └─ Failure → Diagnose → Bounded fix → Recheck
```

حدد checks حسب نوع التغيير: تعديل DTO يحتاج API/serialization validation، وتعديل persistence يحتاج اختبارات معاملات أو تكامل ذات صلة. لا تستخدم production database؛ استخدم fixtures صناعية وبيئة اختبار معزولة.

- التقط baseline checks ذات الصلة قبل التعديل حيث أمكن لتمييز الفشل القديم من الجديد.
- اربط نتيجة كل check بنسخة الكود/hash التي اختُبرت؛ أي تعديل لاحق يبطل checks المتأثرة.
- خزّن الأمر الفعلي والنتيجة وعدد الاختبارات والتقارير. Exit code صفر مع صفر tests قد لا يحقق المطلوب.
- لا تسمح بالإصلاح عبر تعطيل test أو security rule أو تغيير assertion لإخفاء الفشل دون مبرر ومراجعة.
- بعد 3 محاولات إصلاح افتراضية، أخرج partial/blocked مع آخر سبب وفارق الكود.
- فشل تنزيل dependency أو غياب Docker لا يساوي فشل الكود ولا نجاحه؛ سجّل check على أنه غير منفذ.
- Diff review يفحص scope creep، الأسرار، breaking changes، generated files والتغييرات غير المطلوبة.

نهاية كل مهمة: ما تغير، لماذا، الملفات، الاختبارات ونتائجها، checks غير المنفذة، المخاطر المتبقية وطريقة التراجع. لا توجد عبارة «تم التحقق» دون artifacts داعمة.

## 12. MCP كطبقة تكامل

Core tools محلية للبداية. أضف MCP Client لتكاملات GitLab/GitHub وJira وSonarQube وConfluence وواجهات البنك عند وجود حاجة وserver موثوق ومتاح؛ القائمة تصور تكاملات وليست ضمانًا لتوفر خوادمها.

```text
MCP Server discovery → Approved registry → Tool schema normalization
                   → Policy → Approval if needed → Invocation
                   → Output validation / redaction → Agent context
```

ثبت server identity ونسخته والأدوات المسموحة وschemas. تغيّر schema أو ظهور tool جديد يحتاج مراجعة. metadata مثل read-only وdestructive إشارات غير كافية لإثبات الأمان؛ نحن نصنف المخاطر بأنفسنا.

في local stdio يبدأ الـRuntime عملية محددة مع environment محدود. في remote transport استخدم الهوية والتفويض الملائمين للإصدار المختار، scopes دنيا، وتحقيق الوجهة. لا تمرر tokens بين خدمات دون تصميم تفويض صحيح، وامنع SSRF والوصول إلى metadata endpoints. [مرجع أمان MCP](https://modelcontextprotocol.io/docs/2025-11-25/tutorials/security/security_best_practices).

لا نربط التصميم بادعاء أن إصدارًا معينًا هو «الأحدث». ثبّت protocol version متوافقًا مع client/server واختبر negotiation وtimeouts والإلغاء وتغير الأدوات. فشل MCP اختياري لا يعطّل الأدوات الأساسية؛ تعذر دليل تحقق مطلوب يمنع إعلان نجاح المهمة.

## 13. Online / Offline deployment profiles

| البند | Air-gapped | Bank on-prem | Online controlled |
|---|---|---|---|
| inference | محلي داخل البيئة المعزولة | خادم داخلي معتمد | مزود سحابي عبر gateway معتمد |
| الشبكة | لا مسارات خارج المنطقة؛ loopback فقط إذا كان inference محليًا | allowlist داخلية دون إنترنت | egress محدود إلى جهات معتمدة |
| dependencies | حزم وimages مجهزة مسبقًا | artifact mirror داخلي | mirror معتمد وتنزيلات بسياسة |
| MCP | محلي داخل المنطقة | خوادم داخلية محددة | خوادم محددة لكل تكامل |
| embeddings | محلية أو معطلة | داخلية | تخضع لتصنيف البيانات نفسه |
| telemetry | محلية فقط | منصة البنك | منقحة ووجهتها معتمدة |
| fallback cloud | ممنوع | ممنوع افتراضيًا | فقط ضمن سياسة صريحة |

Offline ليس مجرد تغيير model URL. يشمل package registries وtokenizers وmodel weights وlicense checks وtelemetry وcrash reporting وMCP وDNS. حتى شبكة on-prem ليست air gap كاملًا.

مثال schema مقترح يطبقه تطبيقنا، وليس ملف إعداد جاهزًا لمكتبة خارجية:

```yaml
profile: bank-onprem
model:
  provider: ollama
  modelRef: approved-coding-model
  endpointRef: internal-inference
  cloudFallback: false
network:
  default: deny
  allowedServiceRefs: [internal-inference, artifact-mirror]
execution:
  sandboxRequired: true
  rawShell: false
  maxToolCalls: 60
  maxRepairAttempts: 3
mcp:
  enabled: true
  allowedServerRefs: [internal-readonly-docs]
telemetry:
  destination: internal
  rawPrompts: false
policy:
  bundleRef: approved-bank-policy
  planApproval: required
```

حل `*Ref` من registry يديره المسؤول. لا تُخزن tokens في YAML. في profile الهواء المعزول استبدل الخدمات بموارد محلية وجهّز dependencies والـJDK والـbuild plugins مسبقًا. نفّذ اختبار تشغيل من صورة نظيفة مع قطع egress على مستوى الشبكة، وأثبت أن التحقق لا يعتمد على cache شخصي مجهول.

التشغيل الأول: CLI على جهاز المطور مع worker معزول وmodel server محلي أو داخلي. عند المشاركة: Python API/control plane + PostgreSQL للحالة + artifact store داخلي + workers مؤقتة لكل مهمة وهوية. Kubernetes اختياري إذا كان منصة البنك المعتمدة، وليس شرطًا للـMVP.

## 14. هيكل المشروع المقترح

```text
ai-code-engineer/
  pyproject.toml                  # Package metadata and dependencies
  src/ai_code_engineer/
    domain/                       # Task, Plan, ToolCall, Events
    agent/                        # Orchestrator, Planner, Verifier
    models/
      provider.py                 # Protocol and capability contracts
      ollama.py
      openrouter.py
    context/                      # Search, symbols, repo map, budgets
    tools/                        # Registry, filesystem, Git, build/test
    policy/                       # Decisions, approvals, data egress
    skills/                       # Discovery, routing, trusted versions
    mcp/                          # Client and approved registry
    memory/                       # Events, checkpoints, recovery
    worker/                       # Isolated execution and cancellation
    cli/                          # First user interface
    server/                       # Optional API/events for team use
  skills/                         # SKILL.md packages
  policies/                       # Permission/command/path rules
  profiles/                       # Offline/on-prem/online references
  tests/
    unit/
    contract/                     # Provider and tool contracts
    integration/
    security/
  evals/                          # Representative tasks and rubrics
  docs/                           # ADRs, threat model, runbooks
```

نثبت إصدار Python المدعوم وإصدارات الحزم في ملف قفل عند بدء التنفيذ، ونجهز wheelhouse للاستخدام المعزول. أدوات JDK/Maven/Gradle تخص بيئة اختبار المستودع المستهدف، وليست build system للـAgent نفسه.

المجلدات حدود منطقية؛ يمكن دمج modules الصغيرة في MVP. اتجاه dependencies: applications/adapters → core → domain. الـCore يعتمد على interfaces ولا يستورد classes خاصة بمزود نموذج. يبقى التحقق من السياسة على مسار كل استدعاء فعلي، حتى إذا وفرت مكتبة AI تنفيذ tools تلقائيًا.

واجهات خدمة مقترحة: إنشاء task، قراءة الحالة/events، قراءة plan/diff، إرسال approval محدد، cancel، والحصول على artifacts. طبّق authorization على كل task/artifact، لا على تسجيل الدخول فقط.

## 15. Workflow عملي: validation لتحويل بنكي

**الطلب:** «أضف رفض المبلغ صفرًا أو السالب في Transfer API دون تغيير error contract».

1. يتحقق agent من workspace والتغييرات الحالية ثم يقرأ TransferController وTransferRequest وservice والاختبارات.
2. يحمل skills الخاصة بـJava/Spring/testing/API compatibility.
3. يكتشف نمط Bean Validation وglobal exception handler الموجود، بدل اختراع نمط جديد.
4. يعرض خطة: تعديل validation على DTO، اختبارات zero/negative/valid ومراجعة null حسب العقد الحالي، مع التأكد من عدم استدعاء service للطلب غير الصالح.
5. بعد الموافقة، يطبق patch محميًا بالـhash داخل worktree/sandbox معزول.
6. يشغّل compile والاختبارات المحددة، يقرأ تقارير الفشل، ويصلح ضمن الميزانية.
7. يشغّل contract checks المطلوبة ويفحص diff والأسرار.
8. يعرض patch ونتائج فعلية. إنشاء branch أو PR أو push يتم فقط إذا كان مصرحًا ضمن workflow المستخدم.

**مثال التشفير المذكور في المحادثة:** عند طلب تشفير login response يبدأ العمل بتحديد threat model والعقد مع العملاء وإدارة المفاتيح وسبب الحاجة فوق TLS. وجود `CryptoUtil` لا يكفي لإثبات سلامته. نستخدم مكونات تشفير معتمدة بعد المراجعة، ونختبر التوافق وتدوير المفاتيح ومعالجة الأخطاء وعدم تسريب plaintext. لا يبتكر الـAgent خوارزمية تشفير أو يدّعي أن إضافة encrypted fields وحدها تحقق الأمان.

## 16. Git وrollback وحماية عمل المطور

- سجّل baseline commit وdirty files قبل التنفيذ. لا تمسح uncommitted changes للمستخدم.
- يفضل worktree لكل مهمة؛ التغييرات غير المحفوظة لا تنتقل تلقائيًا، لذلك حدد snapshot صراحة إذا كانت جزءًا من الطلب.
- احفظ patches وfile hashes قبل وبعد التعديل. Rollback يعكس تغييرات المهمة فقط بعد فحص عدم وجود تعديل لاحق متعارض.
- لا تستخدم reset/clean واسعًا كطريقة إصلاح. عند التعارض توقف وقدم الملفات المعنية.
- rollback المحلي لا يعكس آثارًا خارجية؛ push أو تحديث issue يحتاج إجراء تعويض مستقل ومصرح.

## 17. Roadmap بمخرجات قابلة للقبول

ننقل أساس السياسة والعزل إلى البداية بدل تأجيلهما لما بعد التنفيذ. باقي محاور الخطة الأصلية محفوظة ومجمعة لتكوين مراحل قابلة للتسليم.

| المرحلة | التنفيذ | بوابة الخروج |
|---|---|---|
| 0 — Foundation | threat model، scope، data classification، worker isolation، read-only CLI، baseline eval set | منع الخروج من workspace وegress غير المصرح في اختبارات فعلية |
| 1 — Providers & tools | model SPI، Ollama adapter، OpenRouter adapter على كود تجريبي، typed tool registry | نفس سيناريو tool calling يمر عبر العقود لكل مزود، ورفض malformed calls؛ تثبيت نموذجين سحابيين بعد تقييمهما |
| 2 — Agent loop | state machine، budgets، events، cancellation، limited tools | مهمة صغيرة end-to-end واستئناف آمن بعد interruption |
| 3 — Context | repo map، search، Java/Spring metadata، token budgets | استرجاع ملفات وأماكن التعديل الصحيحة في benchmark مع تكلفة سياق مقاسة |
| 4 — Plan & edit | خطة/موافقة، scoped patching، worktree، diff/rollback | لا تعديل خارج النطاق، وحماية edits المستخدم |
| 5 — Verify & repair | build/test/lint/security recipes، bounded repair | bug-fix fixtures تتحقق؛ timeout/missing tests لا ينتجان نجاحًا زائفًا |
| 6 — Skills & policy hardening | skill routing، trusted packages، hooks، adversarial cases | prompt injection لا يوسع permissions، والموافقة لا يعاد استخدامها خارج نطاقها |
| 7 — MCP | registry، auth، tool normalization، audit | server غير معتمد أو tool متغيرة تُحجب، وفشل التكامل يُعالج بوضوح |
| 8 — Deployment profiles | offline packaging، internal mirrors، online egress gateway | تشغيل offline من بيئة نظيفة دون اتصالات خارجية؛ اختبار منع تسرب cloud |
| 9 — Team pilot | Web/IDE، SSO/RBAC، metrics، operational runbooks | نجاح تجريبي وفق عتبات محددة مسبقًا ومراجعة بشرية للتغييرات |
| 10 — Scale | tuning، caching، queues، optional sub-agents | تحسن مثبت في الجودة أو الزمن دون تراجع العزل أو تضاعف تكلفة غير مبرر |

ابدأ بـvertical slice واحد: قراءة مشروع Spring صغير → خطة → تعديل validation → tests → diff، داخل sandbox. لا تبدأ بكل integration أو vector store أو multi-agent. إذا أضفنا sub-agents لاحقًا فلكل منها scope وbudget وأدوات محدودة، وتظل السياسة المركزية مطبقة وتمنع تعارض الكتابة.

ترتيب أول backlog: domain contracts → sandbox/policy deny-default → read tools → provider adapter → task loop → scoped patch → verification → durable recovery → repo map → approved external integrations.

## 18. التقييم والمراقبة والتشغيل

أنشئ corpus أوليًا من 20–30 مهمة منزوعة الحساسية: validation، regression fix، refactor محدود، إضافة test، تغيير Kotlin إن وجد، وفشل build سابق. أضف مهام عدائية منفصلة لاختبار المسارات والـprompt injection وتسريب البيانات.

قارن configurations على نفس baseline وبنفس معايير القبول، مع تكرار المهام لقياس تفاوت النتيجة. لا تستخدم إجابة model judge وحدها؛ اجمع اختبارات مستقلة ومراجعة diff بشرية. اختيار النموذج مبني على نجاح التغيير لا جمال الشرح.

المقاييس: نسبة قبول المهام، نسبة نجاح الاختبارات، regressions، tools denied، approval frequency، repair count، context size، زمن المهمة p50/p95، token/cost، ومقدار تعديل الإنسان بعد التسليم. ضع عتبات مناسبة مع الفريق قبل pilot؛ لا توجد أرقام نجاح مضمونة من هذه الخطة.

Audit event يحتوي actor/task/tool ونسخة السياسة والقرار والوقت والمدة ومرجع artifacts؛ يُنقح قبل التخزين. لا تسجل raw prompts أو source code أو tokens افتراضيًا. ضع retention وaccess controls وسجلات مقاومة للعبث، مع فصل بيانات المشاريع. استرجاع logs أو memory لا يتجاوز صلاحيات المستخدم الحالية.

جهّز runbooks لفشل inference، امتلاء القرص، worker عالق، انقطاع artifact mirror، فشل الاستئناف وحادثة تسرب. وفر kill switch يوقف التنفيذ والـegress، وخطة نشر تدريجي وتراجع لإصدارات runtime/model/policy/skills.

## 19. Security considerations

| التهديد | التحكم المطلوب | دليل التحقق |
|---|---|---|
| Prompt injection في repo/docs/tool result | فصل الثقة، سياسة خارج النموذج، عدم تنفيذ نص حر | fixture تطلب تسريبًا وتُحجب |
| تسريب source/secrets للـcloud | classification، تصفية السياق، egress gateway، منع fallback | اختبارات منع upload واتصالات غير مسموحة |
| قراءة خارج workspace | canonical paths، منع links/mount escapes، قيود نظام التشغيل | traversal وjunction/symlink tests |
| build أو test خبيث | worker منخفض الصلاحيات وحدود موارد وشبكة | محاولة قراءة host secret أو إنشاء child process خارج السياسة |
| MCP server أو skill مخترق | registry موثوق، تثبيت versions/hashes، scopes محدودة | schema drift وserver غير معتمد محجوبان |
| اعتماديات ونماذج ملوثة | مصدر معتمد، integrity checks، SBOM، vulnerability/license review | تثبيت قابل للتكرار وقائمة مكونات معتمدة |
| إعادة استخدام approval | ربطها بالخطة والفعل والوجهة والمدة | تبديل target/args يبطل الموافقة |
| فساد edits أو تعارضها | expected hashes، قفل لكل workspace، checkpoint | concurrent edit لا يُكتب فوقه |
| عبور مستخدم/مشروع لآخر | authorization لكل artifact وcache namespace | اختبارات cross-tenant access |
| نجاح مزيف | verifier حتمي وتقارير مرتبطة بـcode hash | tests المتخطاة أو القديمة لا تحقق بوابة الإكمال |

الـOffline يقلل خروج البيانات لكنه لا يمنع كودًا خبيثًا أو إساءة الصلاحيات. Secret scanning طبقة مساعدة وليس ضمانًا؛ لا تدخل secrets إلى سياق الموديل من الأصل. البيانات الاختبارية صناعية، وأي استثناء لبيانات حقيقية يخضع لمسار المؤسسة خارج صلاحيات الـAgent الافتراضية.

الخطة ليست شهادة امتثال. يحدد فريق أمن البنك الضوابط التنظيمية ذات الصلة وتصنيف البيانات والموردين المقبولين وretention، ويعتمد التهديدات المتبقية قبل التوسع.

## 20. Production Definition of Done

### جاهزية الـRuntime

- [ ] العقود منفصلة عن provider وتم اجتياز contract tests لكل configuration منشورة.
- [ ] السياسة deny-default وتُطبق على built-in tools وMCP وhooks وخروج بيانات الموديل.
- [ ] العزل مثبت باختبارات هروب وegress؛ worker لا يحمل credentials إنتاجية.
- [ ] الموافقات محددة ومسجلة ولا تتسع أو تتكرر خارج نطاقها.
- [ ] cancel وtimeout وrestart/recovery لا يسببون replay غير آمن.
- [ ] edits المستخدم محمية وrollback محدد ويمكن اختباره.
- [ ] لا اكتمال ناجح مع check مطلوب فاشل أو غير منفذ.
- [ ] offline يعمل من installation package نظيفة مع dependencies مجهزة ومصدرها معلوم.
- [ ] online يستخدم جهات معالجة بيانات معتمدة مع ضوابط واضحة.
- [ ] عزل المستخدمين والمشاريع وartifacts والذاكرة مختبر.
- [ ] logs منقحة، retention محدد، audit متاح للمخولين.
- [ ] evals ممثلة اجتازت العتبات المتفق عليها، مع مراجعة جودة بشرية.
- [ ] vulnerability/license checks وSBOM وتثبيت الإصدارات ضمن release pipeline.
- [ ] SLOs ودعم وتشغيل وkill switch وخطة incident response معتمدة.

### اكتمال كل مهمة تطوير

- [ ] الهدف ومعايير القبول واضحان والخطة ضمن الصلاحيات.
- [ ] التغيير محدود ومراجع ولا يحتوي أسرارًا أو scope creep.
- [ ] build/tests/checks المطلوبة ناجحة على النسخة النهائية نفسها.
- [ ] القيود والفحوص غير المنفذة معلنة؛ إذا كانت إلزامية فالحالة غير مكتملة.
- [ ] diff والأدلة وملخص الأثر وطريقة التراجع متاحة للمطور.
- [ ] أي نشر أو أثر خارجي تم عبر مسار مصرح ومحدد.

## 21. مراجع التنفيذ

مصدر نطاق الخطة هو محادثة «ترحيب ودي». المراجع التالية تدعم التفاصيل التقنية المرتبطة بها، وليست اعتمادًا لكل اختيارات هذه المعمارية:

1. [Spring AI Reference](https://docs.spring.io/spring-ai/reference/) — abstractions وتكاملات JVM.
2. [OpenAI Function Calling](https://developers.openai.com/api/docs/guides/function-calling) — دورة tool calls ونتائجها.
3. [Ollama Tool Calling](https://docs.ollama.com/capabilities/tool-calling) — تشغيل الأدوات مع النماذج الداعمة.
4. [Aider Repository Map](https://aider.chat/docs/repomap.html) — اختيار سياق عبر خريطة رموز المستودع.
5. [MCP Security Best Practices](https://modelcontextprotocol.io/docs/2025-11-25/tutorials/security/security_best_practices) — اعتبارات أمان التكامل والتفويض.

## 22. تحديث معتمد: Cloud مجاني داخل التول

هذا القسم يعتمد آخر إضافة في الشات القديم، ويحدد نطاق أول إصدار: **Python + Ollama Local + OpenRouter**. نخدم عدة cloud models عبر محول OpenRouter واحد، ونؤجل التكامل المباشر مع Gemini وGroq وغيرهما.

### اختيار الموديل

تقدم التول ثلاثة اختيارات، مع بقاء الـAgent Core مستقلًا عن المزود:

| الاختيار | الاستخدام |
|---|---|
| `local` | نموذج محلي معتمد عبر Ollama؛ الافتراضي للكود البنكي |
| `cloud-free` | نموذج مجاني محدد ومختبر عبر OpenRouter، لكود تجريبي مسموح |
| `cloud-auto` | `openrouter/free` لاستكشاف النماذج على بيانات عامة أو صناعية |

وثائق OpenRouter تؤكد أن `openrouter/free` يختار نموذجًا مجانيًا عشوائيًا من النماذج المتاحة المتوافقة مع خصائص الطلب مثل tool calling. لذلك نستخدم نموذجًا محددًا لاختبارات المقارنة القابلة للتكرار، ونسجل الموديل الذي خدم كل رد عند استخدام الوضع التلقائي. [Free Models Router](https://openrouter.ai/docs/guides/routing/routers/free-router).

مثال إعدادات لتطبيقنا؛ قيم `modelRef` أسماء داخل registry وليست model IDs جاهزة للإرسال:

```yaml
models:
  local:
    provider: ollama
    modelRef: approved-local-coder
  cloud-free:
    provider: openrouter
    modelRef: evaluated-free-coder-primary
    credentialRef: openrouter-api-key
  cloud-auto:
    provider: openrouter
    model: openrouter/free
    credentialRef: openrouter-api-key
    allowedDataClasses: [public, synthetic]
routing:
  default: local
  paidFallback: false
  localToCloudFallback: false
  cloudFreeFallbackChain:
    - evaluated-free-coder-secondary
    - local
```

```text
agent --model local
agent --model cloud-free
agent --model cloud-auto
```

خيارات CLI وإعدادات YAML عقد مقترح للتنفيذ وليست أوامر أداة مبنية بالفعل. تعرض واجهة الاختيار الاسم الفعلي، المزود، قدرات الأدوات، وسياسة البيانات؛ الاختيار لا يغير صلاحيات المهمة.

### Fallback وحدود الاستخدام

مسار التجربة: نموذج مجاني أساسي → بديل مجاني معتمد → Ollama محلي. يطبق الـRuntime هذا المسار على أخطاء التوفر أو rate limit المؤقتة فقط، مع retry محدود واحترام تعليمات الانتظار من المزود. بلوغ quota مشتركة قد يؤثر على جميع النماذج المجانية؛ تغيير النموذج لا يضمن استعادة الخدمة. [Limits](https://openrouter.ai/docs/api_reference/limits).

قبل التحويل افحص السياسة وtool capabilities وحجم السياق، ثم أعد بناء request من الحالة المحفوظة بالصيغة المناسبة للمزود الجديد. لا تُعد تشغيل أداة نُفذت بالفعل. أخطاء credentials أو رفض السياسة تتوقف بتفسير واضح؛ ولا نتحول إلى نموذج مدفوع تلقائيًا. لا انتقال من offline إلى cloud، ويُعرض أي تبديل للموديل في سجل المهمة.

الميزانية المجانية تحد استدعاءات الموديل، لا عدد المهام المكتملة؛ مهمة agent واحدة قد تحتاج استدعاءات عديدة. نخزن limits كإعدادات قابلة للتحديث ونراقب الاستهلاك، ولا نبني سلوك التطبيق على عدد ثابت للنماذج أو requests/day.

### اختيار النماذج للـPOC

الأسماء التي طُرحت في آخر رسالة قديمًا — North Mini Code، Laguna S 2.1، Nemotron 3 Ultra، وNemotron 3.5 Lightning — **مرشحات وردت في المحادثة وليست قائمة توفر معتمدة في هذه الوثيقة**. لا نعتمد model IDs أو context sizes المذكورة قبل التحقق من الكتالوج والصفحة الرسمية لكل نموذج وقت التنفيذ.

خطوة التنفيذ التالية ضمن مرحلة providers: تكوين shortlist من 5–7 نماذج مجانية متاحة بالفعل، أو أقل إذا لم تتوفر خيارات تستوفي الشروط، ثم اختيار أساسي واحتياطي بناءً على:

1. توفر API مجاني فعلي ودعم tool calling صالح في اختباراتنا.
2. جودة مهام Java/Spring: validation، bug fixing، اختبارات، وتعديل عدة ملفات.
3. معدل نجاح patches والاختبارات، latency، context الفعلي، واستقرار التوفر.
4. شروط معالجة البيانات والاحتفاظ والتدريب لكل provider route.
5. اجتياز نفس policy/contract tests المستخدمة للموديل المحلي.

تُحفظ النتائج بتاريخ القياس وmodel ID وprovider الفعلي. لا نعتبر اتساع context وحده دليل جودة أو ملاءمة للـAgent.

### فصل تجربة المجاني عن كود البنك

Cloud free في المرحلة الأولى مخصص لمستودع تجريبي وبيانات عامة أو صناعية. شروط معالجة البيانات تختلف بين المزودين، وتوجد إعدادات خصوصية على OpenRouter يجب مراجعتها مع شروط الجهة التي تنفذ inference. [Data Collection](https://openrouter.ai/docs/guides/privacy/data-collection).

لا يكفي وصف الخدمة بأنها مجانية أو تغيير إعداد logging لإجازة إرسال source code بنكي. يظل هذا الكود على local/on-prem ما لم تعتمد المؤسسة جهة المعالجة والمسار صراحة. إذا تعذر تقييد الوجهة الفعلية وفق السياسة، يُمنع `cloud-auto` لهذا التصنيف.

**قرار البداية المعتمد:** Python Agent Runtime + Ollama Local + OpenRouter + أدوات صغيرة + sandbox وسياسة من اليوم الأول + verification loop. تُختار النماذج السحابية بعد قياسها، وتبقى إضافة providers مباشرة وتعدد الـAgents مراحل لاحقة.
