"use strict";
(() => {
  const button = document.getElementById("readinessRefresh");
  button.addEventListener("click", async () => {
    button.disabled = true;
    const rows = document.getElementById("readinessRows");
    rows.textContent = "جارٍ فحص الإعدادات…";
    try {
      const response = await fetch("/api/diagnostics", {cache:"no-store"});
      if (!response.ok) throw new Error("تعذّر الفحص؛ حدّث الصفحة وتأكد من تسجيل الدخول.");
      const data = await response.json();
      document.getElementById("readinessNote").textContent = data.note;
      rows.replaceChildren();
      const labels = {ready:"جاهز",configured:"معدّ",limited:"محدود",setup:"يحتاج إعداد"};
      for (const check of data.checks) {
        const row = document.createElement("p");
        const title = document.createElement("b");
        title.textContent = `${check.name} — ${labels[check.state] || check.state}`;
        const detail = document.createElement("span");
        detail.className = "muted";
        detail.textContent = check.detail;
        row.append(title, document.createElement("br"), detail);
        rows.append(row);
      }
    } catch (error) { rows.textContent = error.message; }
    finally { button.disabled = false; }
  });
})();
