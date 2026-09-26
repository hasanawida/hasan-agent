# 🧠 Hassan AI OS

**نظام تشغيل ذكاء اصطناعي خاص بحسن:** عدة نماذج تفكّر، الأدلة تحكم، وجهازك ينفّذ.
Model-agnostic multi-agent control plane — many models think, evidence decides, your machine executes.

```text
أنت ─► Hassan Manager ─► Analyst ─► Planner ─► ( Researcher ∥ Coder×N ) ─► Reviewer + Cross-Reviewer
                                                                               │
          Decision ◄── Verify (Build/Tests) ◄── Apply ◄── موافقتك ◄── Diff ◄── Judge (بالأدلة)
                              │
                     فشل؟ ─► Coder يصلح ─► Diff جديد ─► موافقتك ─► Verify   (حتى HASSAN_MAX_REPAIR_ROUNDS)
```

## ما الذي يعمل فعلاً (v0.3)

| الجزء | الحالة |
|---|---|
| 8 وكلاء + مراجع مستقل: Manager, Analyst, Planner, Researcher, Coder, Reviewer, Judge, Decision | ✅ |
| أوضاع `fast` / `auto` (المدير يختار) / `consensus` (3 مبرمجين بنماذج مختلفة + مراجعة متقاطعة) | ✅ |
| **Structured Change Plan**: المبرمج يرجع JSON مُتحقق منه (ملف، محتوى، سبب) | ✅ |
| عرض **Diff** قبل أي تعديل + أزرار موافقة/رفض | ✅ |
| Checkpoint قبل التنفيذ (`git stash create` بدون لمس ملفاتك) + نسخة احتياطية لكل ملف + **Rollback** | ✅ |
| كشف نوع المشروع: .NET/Visual Studio، Python، Node، Rust، Go، Unity، ملفات Blender | ✅ |
| Build/Test تلقائي من قائمة أوامر مسموحة فقط — **لا يوجد shell مفتوح** | ✅ |
| **حلقة إصلاح**: إذا فشل الاختبار بعد التعديل، المبرمج يصلح ويطلب موافقة جديدة | ✅ |
| Fallback تلقائي بين النماذج لكل وكيل (إذا سقط مزوّد يكمل بغيره) | ✅ |
| Capability Registry مُقاس: نسبة نجاح وزمن كل نموذج لكل دور (`/api/models/stats`) | ✅ |
| ذاكرة مشاريع (SQLite): القرارات تُحفظ وتُعطى للوكلاء في المهام القادمة | ✅ |
| صلاحيات ثلاثية: auto / approval / forbidden + منع ملفات الأسرار (.env, مفاتيح…) + منع الخروج من المجلد | ✅ |
| MCP v2: عميل stdio / HTTP + خادم Hassan Local Project MCP | ✅ |
| واجهة عربية كاملة | ✅ |
| **Claude (Claude Code CLI) + ChatGPT (Codex CLI) باشتراكك** كعقول للوكلاء | ✅ |
| اكتشاف OpenHands | ✅ (قراءة فقط) |
| Visual Studio / Blender الحقيقيين | ⚙️ الموصل جاهز — تحتاج تثبيت خادم MCP لكل برنامج على جهازك وتفعيله في `configs/mcp_servers.yaml` |

الاختبارات: **29 اختبار ناجح**، منها دورة كاملة حقيقية: مهمة → Diff → موافقة → كتابة الملف → pytest → قرار → Rollback.

## التشغيل على Windows

```text
start.bat          :: الأساسي
start-full.bat     :: مع دعم MCP
```

ثم افتح `http://127.0.0.1:8787`.

يبدأ بوضع `mock` — كل الدورة تعمل **بدون مفاتيح API وبدون تكلفة**، والتنفيذ الحقيقي (Checkpoint/Build/Tests/كتابة الملفات) حقيقي تماماً.
في وضع mock، لتجربة دورة التعديل اكتب في المهمة: `[[write path/file.py]]المحتوى[[/write]]`.

المجلدات المسموح للوكلاء العمل فيها تُحدد بـ `HASSAN_ALLOWED_ROOTS` (الافتراضي: مجلد المستخدم).

Linux/macOS: `./start.sh`

## التثبيت الدائم على Windows (يشتغل لحاله مع الويندوز)

بعد `git pull`، اضغط دبل كليك على **`install.bat`**. رح:
- يثبّت النظام ويشغّله **بالخلفية بدون نافذة سودا**
- يخليه **يشتغل لحاله كل ما يفتح الويندوز**
- يعمل أيقونة **Hassan AI** على سطح المكتب بتفتح الواجهة
- يضيف أمر **`hassan`** لأي Terminal (ومنها Terminal تبع VS Code)

الإعدادات في `hassan.env` (الوضع، المجلدات المسموحة). للتحديث: دبل كليك على **`update.bat`**. للإزالة: `scripts\uninstall-windows.ps1`.

| أمر | شو بعمل |
|---|---|
| `hassan open` | يشغّل النظام إذا مش شغّال ويفتح الواجهة |
| `hassan ask "افحص المشروع"` | يبعت مهمة، والمشروع = المجلد اللي أنت فيه |
| `hassan ask "..." --execute` | مع Checkpoint + Build + Tests |
| `hassan status` / `hassan stop` / `hassan start` | الحالة / إيقاف / تشغيل |

## VS Code

- **من VS Code لـHassan:** افتح Terminal جوّا VS Code (`` Ctrl+` ``) واكتب `hassan ask "شو المشكلة بهاد المشروع؟"`، والمهمة بتفتح بالمتصفح. أو انسخ `configs/vscode/tasks.json` لـ`.vscode/tasks.json` وشغّلها من `Ctrl+Shift+P → Tasks: Run Task → Hassan`.
- **من Hassan لـVS Code:** زر **VS Code ↗** جنب مسار المشروع، وعلى كل ملف بطلب الموافقة، بيفتح الملف/المجلد في VS Code.
- **اختياري:** `configs/vscode/mcp.json` بخلي الـAgent تبع VS Code نفسه يقرأ المشروع من خلال خادم Hassan.

## احكي معه بصوتك 🎤

زر 🎤 جنب مربع المهمة (Edge أو Chrome): احكي بالعربي/English/עברית والكلام بينكتب لحاله.
- **أرسل لما أسكت:** يبعت المهمة أوتوماتيك لما توقف حكي.
- **🔊 اقرألي الرد:** يقرأ القرار النهائي بصوت. (لصوت عربي أوضح: Windows Settings → Time & language → Speech → أضف العربية.)
- ملاحظة: التعرف على الصوت بالمتصفح بيمر على خدمة Microsoft/Google السحابية.

## الاستهلاك

كرت **الاستهلاك** بالواجهة (و`/api/usage`): عدد المهام والطلبات والـtokens اليوم وآخر 7 أيام، ولكل مهمة لحالها، وحسب كل نموذج.
المبلغ **≈$** هو سعر الـAPI المكافئ (Claude بيحسبه بنفسه). مع اشتراك Claude/ChatGPT **ما بتدفع لكل طلب** — الاشتراك سعره ثابت وله حدود استخدام، والرقم بيساعدك تعرف قديش بتستهلك منها.

## تشغيل Claude و ChatGPT الموجودين عندك (باشتراكك، بدون API)

النظام يشغّل **Claude Code** و **Codex (ChatGPT)** على جهازك كـ"عقول" للوكلاء، باستخدام تسجيل الدخول باشتراكك.

```text
npm i -g @anthropic-ai/claude-code   ثم  claude        (سجّل دخول بحساب Claude Pro/Max مرة واحدة)
npm i -g @openai/codex               ثم  codex login   (Sign in with ChatGPT)
start-live.bat
```

توزيع الوكلاء في `configs/providers.yaml` (افتراضياً: Claude يبرمج ويحكم، ChatGPT يخطط ويراجع، وكل واحد احتياط للثاني).

- العقول تعمل في مجلد مؤقت فارغ، بدون أدوات (Claude: `--tools ""`، Codex: `--sandbox read-only`) — **تفكر وتجاوب فقط**؛ أي تعديل على مشروعك يمر عبر Diff وموافقتك.
- `use_subscription: true` يخفي `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` عن الـCLI حتى يُحسب الاستخدام على اشتراكك وليس على API.
- الاشتراك له حدود استخدام؛ وضع Consensus يستهلك أسرع. إذا وصل Claude للحد، النظام ينتقل تلقائياً لـChatGPT والعكس.
- حالة العقول ظاهرة في الواجهة وفي `/api/providers`.

## تشغيل النماذج عبر API (Grok + DeepSeek + محلي …)

1. `copy configs\litellm.example.yaml configs\litellm.yaml` وعدّل أسماء النماذج (الموجودة placeholders).
2. ضع المفاتيح في متغيرات البيئة: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `XAI_API_KEY`.
3. `scripts\start-litellm.ps1`
4. `set HASSAN_AI_MODE=live` ثم `start.bat`

الوكلاء يشيرون إلى **أسماء مستعارة** (`coder`, `judge`…) وليس إلى شركة. تبديل النموذج = تعديل سطر في `litellm.yaml`، بدون لمس الكود.
سلسلة الـfallback لكل وكيل في `configs/agents.yaml`.

## ربط Visual Studio و Blender

`configs/mcp_servers.yaml`:

```yaml
visual-studio:
  transport: http
  enabled: true
  url: http://127.0.0.1:5050/mcp     # عنوان إضافة MCP داخل Visual Studio
blender:
  transport: stdio
  enabled: true
  command: uvx
  args: ["blender-mcp"]              # حسب خادم Blender MCP الذي تثبته
```

أدوات القراءة (get/list/find/diagnostics…) تعمل تلقائياً. أي أداة تعدّل (execute_python، rename_symbol…) تُمنع من الـAPI المباشر وتحتاج موافقة.

## خادم MCP المحلي (ليستخدمه Claude/ChatGPT/Grok على نفس المشروع)

```powershell
$env:HASSAN_MCP_WORKSPACE="C:\Users\Hassan\Projects\AlAqsaVR"
python connectors\local-project-mcp\server.py
```

الأدوات: `inspect_project`, `list_files`, `read_file`, `git_status`, `verify` — لا كتابة ولا shell.

## API

| Method | Path | |
|---|---|---|
| POST | `/api/tasks` | `{prompt, mode, workspace, execute, project}` |
| GET | `/api/tasks/{id}` | الحالة + الأدلة + مخرجات الوكلاء + الموافقات |
| GET | `/api/tasks/{id}/events?after=N` | السجل الحي |
| POST | `/api/approvals/{id}` | `{approve: true/false}` |
| POST | `/api/tasks/{id}/rollback` | استرجاع الملفات من الـCheckpoint |
| GET/POST | `/api/memory/{project}` | ذاكرة المشروع |
| GET | `/api/models/stats` | أداء كل نموذج لكل دور |
| GET | `/api/mcp`, `/api/mcp/{server}/tools` | الموصلات |
| GET | `/api/openhands` | اكتشاف OpenHands |

التوثيق التفاعلي: `http://127.0.0.1:8787/docs`

## الاختبارات

```powershell
scripts\verify.ps1          # Windows
python -m pytest -q         # أي نظام
```

## الخطوة القادمة

- ربط أدوات Visual Studio (Roslyn/Debugger) و Blender كخطوات داخل الخطة، وليس فقط كموصلات.
- Vision Reviewer: مقارنة Render من Blender مع الصور المرجعية.
- تنفيذ طويل لا يضيع عند إعادة التشغيل (Temporal أو Agent Framework checkpoints).
- تسليم المهام البرمجية الطويلة لـOpenHands مع بقاء الصلاحيات والقاضي عند Hassan.

انظر [ARCHITECTURE.md](ARCHITECTURE.md).
