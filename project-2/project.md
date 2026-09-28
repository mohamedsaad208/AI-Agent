أنا أعمل على مشروع Spring Boot 3 مع Java 17 و Maven لنظام تسجيل دخول ومصادقة (Authentication & User Management Service).

المطلوب منك الآن:
⚠️ تنبيه هام: لا تكتب أي كود برمجي فعلي للبرنامج الآن نهائياً، بل قم بدور مهندس البرمجيات (Software Architect) واكتب خطة تنفيذية كاملة ومفصلة للمشروع.

قم بتقسيم العمل إلى مراحل (Phases) وخطوات مرقمة (Step 1, Step 2, ...) بصيغة Markdown، بحيث تحتوي كل خطوة على:
1. [اسم الخطوة]: ما الذي سيتم إنجازه بالتحديد.
2. [الملفات المستهدفة]: المسار الدقيق لكل ملف (Class / DTO / Controller / Repository / Service / Test).
3. [المسؤوليات]: الدوال (Methods) والـ Annotations والـ Endpoints المطلوبة لكل كلاس.
4. [معايير القبول والتحقق]: كيف نتأكد أن هذه الخطوة اكتملت بنجاح قبل الانتقال للخطوة التالية.

اجعل الخطة تغطي:
- المرحلة 1: الـ Models و DTOs (مثل User, LoginRequest, RegisterRequest, AuthResponse).
- المرحلة 2: طبقة التخزين والـ Service (مثل UserRepository و AuthService مع التحقق من صحة البيانات).
- المرحلة 3: طبقة الـ REST Controller و Endpoints (مثل /api/auth/login و /api/auth/register).
- المرحلة 4: معالجة الأخطاء (Global Exception Handling) واختبارات الـ Unit Tests لكل Endpoint.

اكتب الخطة بتنسيق Markdown منظم وواضح لنعتمدها كـ `plan.md` للمشروع.