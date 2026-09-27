"""Read-only local readiness checks. Never sends model requests or returns secrets."""
import importlib.util
import shutil

from .providers import APIBackend, CLIBackend
from .llm import MockLLM


def readiness(settings, llm, desktop, mcp, active_jobs, phones=None):
    rows = []
    def add(name, state, detail):
        rows.append({"name": name, "state": state, "detail": detail})
    add("وضع التشغيل", "ready" if settings.mode == "live" else "limited",
        "وضع فعلي" if settings.mode == "live" else "وضع تجريبي: الإجابات محاكاة")
    ffmpeg = shutil.which("ffmpeg") is not None
    add("عرض الشاشة", "ready" if ffmpeg else "setup",
        "أداة البث مثبّتة؛ جرّب عرض الشاشة" if ffmpeg else "ثبّت FFmpeg ثم أعد تشغيل الإيجنت")
    ds = desktop.status()
    add("الماوس والكيبورد", "ready" if ds["enabled"] else "limited" if ds["supported"] else "setup",
        ("السماح دائم؛ الجلسة تبدأ بزر تحكّم" if ds["persistent"] else "التحكّم مسموح مؤقتًا") if ds["enabled"] else "فعّل السماح من صفحة الكمبيوتر" if ds["supported"] else
        "يحتاج ويندوز ومكتبة التحكّم")
    windows = mcp.servers.get("windows")
    configured = bool(windows and windows.enabled and importlib.util.find_spec("mcp"))
    add("تحكّم الإيجنت بالتطبيقات", "configured" if configured else "setup",
        "موصل ويندوز معدّ؛ الاتصال يُفحص عند تنفيذ مهمة" if configured else "فعّل موصل Windows من إعدادات التطبيقات")
    for name, backend in getattr(llm, "backends", {}).items():
        if isinstance(backend, CLIBackend):
            available = backend.executable() is not None
            add(f"عقل: {name}", "configured" if available else "setup",
                "مثبّت؛ تسجيل الدخول والرصيد غير مفحوصين هنا" if available else "البرنامج غير مثبّت أو غير متاح")
        elif isinstance(backend, APIBackend):
            add(f"عقل: {name}", "configured" if backend.configured else "setup",
                "المفتاح معدّ؛ صلاحية الاتصال والرصيد غير مفحوصة هنا" if backend.configured else "أضف مفتاح الخدمة")
        elif isinstance(backend, MockLLM):
            add(f"عقل: {name}", "limited", "إجابات تجريبية")
        else:
            add(f"عقل: {name}", "configured", "الخدمة معدّة؛ الاتصال غير مفحوص هنا")
    if phones is not None:
        devices = phones.list_devices()
        online = sum(bool(row.get("online")) for row in devices)
        controllable = sum(bool(row.get("online") and row.get("control_enabled")) for row in devices)
        add("الهاتف من الكمبيوتر", "ready" if controllable else "limited" if devices else "setup",
            f"{online} متصل · {controllable} يسمح بالتحكّم" if devices else "ثبّت تطبيق Hassan على هاتفك ثم اربطه من كرت تلفونك من الكمبيوتر")
        apk = settings.data_dir / "phone-app" / "hassan-phone.apk"
        add("تطبيق الهاتف", "ready" if apk.is_file() else "setup",
            "نسخة التثبيت جاهزة للتنزيل من صفحة الربط" if apk.is_file() else "نسخة التثبيت لم تُجهّز على هذا الخادم بعد")
    roots_ok = bool(settings.allowed_roots) and all(p.is_dir() for p in settings.allowed_roots)
    add("مجلدات العمل", "ready" if roots_ok else "setup",
        "المجلدات المحددة موجودة" if roots_ok else "راجع المجلدات المسموح العمل فيها")
    return {"checks": rows, "active_tasks": active_jobs,
            "note": "فحص إعدادات محلي دون تشغيل مهام أو استهلاك رصيد الذكاء الاصطناعي."}
