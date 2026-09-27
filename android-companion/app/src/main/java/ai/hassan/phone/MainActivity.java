package ai.hassan.phone;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.media.projection.MediaProjectionConfig;
import android.media.projection.MediaProjectionManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.text.InputType;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Switch;
import org.json.JSONObject;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends Activity {
    private static final int CAPTURE = 10, NOTIFICATIONS = 11;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private EditText server, code, label;
    private TextView status;
    private Button pair, start, disconnect;
    private boolean pendingControl, syncingSwitch, capturePending;
    private Switch control;
    private final Runnable refresh = new Runnable() {
        @Override public void run() {
            if (status != null) {
                status.setText(SessionState.status + "\n" + (SessionState.preferences(MainActivity.this).getBoolean("control_allowed", false) ? "السماح الدائم بالتحكّم مفعّل." : "السماح بالتحكّم متوقف.") + "\n" + (PhoneAccessibilityService.instance == null
                    ? "إذن إمكانية الوصول غير مفعّل: التحكّم غير متاح." : "إذن إمكانية الوصول جاهز؛ يعمل حسب زر السماح الدائم."));
                boolean paired = !SessionState.preferences(MainActivity.this).getString("token", "").isEmpty();
                start.setEnabled(paired && !SessionState.active && !ShareService.isRunning() && !capturePending && !SessionState.enrolling);
                pair.setEnabled(!SessionState.active && !SessionState.enrolling);
                disconnect.setEnabled(paired && !SessionState.enrolling);
                syncingSwitch = true;
                control.setChecked(SessionState.preferences(MainActivity.this).getBoolean("control_allowed", false));
                control.setEnabled(paired && !SessionState.enrolling);
                syncingSwitch = false;
            }
            main.postDelayed(this, 1000);
        }
    };
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        capturePending = state != null && state.getBoolean("capture_pending", false);
        pendingControl = state != null && state.getBoolean("pending_control", false);
        LinearLayout content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(dp(22), dp(36), dp(22), dp(30));
        content.setLayoutDirection(View.LAYOUT_DIRECTION_RTL);
        content.setBackgroundColor(Color.rgb(245,247,252));
        ScrollView scroll = new ScrollView(this); scroll.setFillViewport(true); scroll.addView(content); setContentView(scroll);
        text(content, "Hassan على هاتفك", 28);
        text(content, "اربط الكمبيوتر بهاتفك عبر Tailscale. يمكنك منح التحكّم الدائم، وإيقاف عرض الشاشة بشكل مستقل. العرض يحتاج موافقتك عند بدء كل جلسة.", 17);
        SharedPreferences prefs = SessionState.preferences(this);
        server = field(content, "رابط الكمبيوتر HTTPS", InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        server.setLayoutDirection(View.LAYOUT_DIRECTION_LTR);
        server.setText(prefs.getString("origin", ""));
        server.setHint("https://your-computer.your-tailnet.ts.net");
        label = field(content, "اسم هاتفك", InputType.TYPE_CLASS_TEXT);
        label.setText(prefs.getString("label", Build.MODEL));
        code = field(content, "رمز الربط من صفحة الكمبيوتر", InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        code.setSingleLine(true);
        pair = button(content, "اربط هاتفي بالكمبيوتر", v -> pairPhone());
        text(content, "التحكّم يتيح النقر والسحب وكتابة النص وقراءة عناصر التطبيقات الظاهرة. قد تظهر بيانات حساسة على الشاشة؛ افتح فقط ما تريد مشاركته. لا نعرض حقول كلمات السر في وصف الشاشة، ولا نتحكّم بشاشة القفل.", 15);
        button(content, "١. افتح إعدادات إمكانية الوصول", v -> startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)));
        control = new Switch(this); control.setText("السماح الدائم بالتحكّم من الكمبيوتر المربوط"); control.setMinHeight(dp(56));
        control.setOnCheckedChangeListener((view, checked) -> {
            if (syncingSwitch) return;
            if (checked) enableControl();
            else {
                prefs.edit().putBoolean("control_allowed", false).apply();
                if (PhoneAccessibilityService.instance != null) PhoneAccessibilityService.instance.refreshControl();
                SessionState.status = "توقف التحكّم؛ تستطيع إبقاء عرض الشاشة فقط.";
            }
        });
        content.addView(control);
        text(content, "السماح الدائم يبقى بعد إغلاق الصفحة وإعادة تشغيل التطبيق، ما دامت خدمة إمكانية الوصول تعمل. قفل الهاتف يمنع التحكّم حتى تفتحه بنفسك. إيقاف العرض لا يلغي هذا الإذن.", 14);
        start = button(content, "ابدأ عرض الشاشة", v -> beginSharing());
        button(content, "أوقف عرض الشاشة فقط", v -> {
            SessionState.active = false;
            stopService(new Intent(this, ShareService.class));
            SessionState.status = "عرض الشاشة متوقف. التحكّم يبقى حسب زر السماح الدائم.";
        });
        button(content, "إيقاف العرض وكل التحكّم", v -> StopControlReceiver.stopAll(this));
        disconnect = button(content, "احذف الربط من هذا الهاتف", v -> {
            StopControlReceiver.stopAll(this);
            prefs.edit().remove("token").remove("device_id").apply();
            SessionState.status = "تم حذف الربط المحلي. احذف الجهاز أيضًا من صفحة الكمبيوتر لإلغاء رمزه نهائيًا.";
        });
        status = text(content, "", 16);
        text(content, "عرض الشاشة لا يبدأ تلقائيًا، وقفل الهاتف ينهي العرض. لبدء عرض جديد وافق على طلب Android. يمكن إلغاء التحكّم من هنا أو من إشعاره أو بإيقاف خدمة إمكانية الوصول.", 14);
    }
    @Override protected void onSaveInstanceState(Bundle state) {
        state.putBoolean("capture_pending", capturePending);
        state.putBoolean("pending_control", pendingControl); super.onSaveInstanceState(state);
    }
    @Override protected void onResume() { super.onResume(); main.removeCallbacks(refresh); main.post(refresh); }
    @Override protected void onPause() { main.removeCallbacks(refresh); super.onPause(); }
    @Override protected void onDestroy() { main.removeCallbacks(refresh); worker.shutdownNow(); super.onDestroy(); }
    private void pairPhone() {
        if (SessionState.active || SessionState.enrolling) return;
        if (SessionState.preferences(this).getBoolean("control_allowed", false)) {
            SessionState.status = "أوقف كل التحكّم أولًا قبل تغيير ربط الهاتف."; return;
        }
        final String origin, enteredCode = code.getText().toString().trim(), enteredLabel = label.getText().toString().trim();
        try { origin = ApiClient.validateOrigin(server.getText().toString()); }
        catch (Exception ex) { SessionState.status = "أدخل رابط HTTPS الأساسي الصحيح من صفحة الكمبيوتر."; return; }
        if (enteredCode.isEmpty() || enteredCode.length() > 128 || enteredLabel.isEmpty() || enteredLabel.length() > 80) {
            SessionState.status = "أدخل رمز الربط واسم الهاتف (حتى ٨٠ حرفًا)."; return;
        }
        SessionState.enrolling = true; pair.setEnabled(false); SessionState.status = "جارٍ الربط…";
        worker.submit(() -> {
            try (ApiClient api = new ApiClient(origin, "")) {
                JSONObject result = api.json("/api/phone/enroll", new JSONObject().put("code", enteredCode).put("label", enteredLabel), 10000);
                String token = result.getString("token"), device = result.getString("device_id");
                if (token.isEmpty() || token.length() > 4096 || device.isEmpty()) throw new IllegalStateException("Invalid enrollment");
                boolean stored = SessionState.preferences(this).edit().putString("origin", origin).putString("token", token)
                    .putString("device_id", device).putString("label", enteredLabel).putBoolean("control_allowed", false).commit();
                if (!stored) {
                    SessionState.preferences(this).edit().remove("token").remove("device_id").apply();
                    throw new IllegalStateException("Could not persist enrollment");
                }
                if (PhoneAccessibilityService.instance != null) PhoneAccessibilityService.instance.refreshControl();
                main.post(() -> { code.setText(""); SessionState.status = "تم الربط. فعّل إمكانية الوصول ثم ابدأ المشاركة."; });
            } catch (Exception ex) {
                SessionState.status = "تعذّر الربط. تأكد من Tailscale، رابط الكمبيوتر، ورمز ربط جديد غير منتهي.";
            } finally { SessionState.enrolling = false; }
        });
    }
    private void enableControl() {
        if (SessionState.preferences(this).getString("token", "").isEmpty()) { SessionState.status = "اربط الهاتف أولًا."; return; }
        if (PhoneAccessibilityService.instance == null) {
            SessionState.status = "فعّل خدمة Hassan من إعدادات إمكانية الوصول أولًا، ثم فعّل السماح الدائم."; return;
        }
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            pendingControl = true; requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, NOTIFICATIONS); return;
        }
        SessionState.preferences(this).edit().putBoolean("control_allowed", true).apply();
        PhoneAccessibilityService.instance.refreshControl();
        SessionState.status = "السماح الدائم مفعّل. تقدر توقف العرض وحده أو توقف كل التحكّم.";
    }
    private void beginSharing() {
        if (SessionState.active || ShareService.isRunning() || capturePending) return;
        if (SessionState.preferences(this).getString("token", "").isEmpty()) { SessionState.status = "اربط الهاتف أولًا."; return; }
        if (SessionState.locked(this)) { SessionState.status = "افتح قفل الهاتف أولًا."; return; }
        if (PhoneAccessibilityService.instance == null) {
            SessionState.status = "فعّل خدمة Hassan من إعدادات إمكانية الوصول لاتصال الهاتف بالكمبيوتر، ثم ارجع لبدء العرض. التحكّم نفسه يبقى حسب زر السماح."; return;
        }
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            pendingControl = false; requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, NOTIFICATIONS); return;
        }
        MediaProjectionManager manager = getSystemService(MediaProjectionManager.class);
        Intent capture = Build.VERSION.SDK_INT >= 34
            ? manager.createScreenCaptureIntent(MediaProjectionConfig.createConfigForDefaultDisplay())
            : manager.createScreenCaptureIntent();
        capturePending = true;
        startActivityForResult(capture, CAPTURE);
    }
    @Override public void onRequestPermissionsResult(int request, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(request, permissions, results);
        if (request == NOTIFICATIONS) {
            if (results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) { if (pendingControl) enableControl(); else beginSharing(); }
            else SessionState.status = "اسمح بإشعار المشاركة ليبقى زر الإيقاف ظاهرًا، ثم جرّب مجددًا.";
        }
    }
    @Override protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request == CAPTURE) capturePending = false;
        if (request == CAPTURE && result == RESULT_OK && data != null) {
            Intent service = new Intent(this, ShareService.class).setAction(ShareService.START)
                .putExtra("capture_result", result).putExtra("capture_data", data);
            startForegroundService(service);
            SessionState.status = "جارٍ بدء جلسة المشاركة…";
        } else if (request == CAPTURE) SessionState.status = "لم تبدأ المشاركة؛ لم يتم منح إذن الشاشة.";
    }
    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }
    private TextView text(LinearLayout parent, String value, int size) {
        TextView view = new TextView(this); view.setText(value); view.setTextSize(size); view.setTextColor(Color.rgb(20,30,50));
        view.setPadding(0,dp(8),0,dp(12)); parent.addView(view); return view;
    }
    private EditText field(LinearLayout parent, String title, int type) {
        text(parent,title,15); EditText view = new EditText(this); view.setInputType(type); view.setSingleLine(true); view.setTextSize(16);
        parent.addView(view, new LinearLayout.LayoutParams(-1,-2)); return view;
    }
    private Button button(LinearLayout parent, String title, View.OnClickListener listener) {
        Button view = new Button(this); view.setText(title); view.setAllCaps(false); view.setMinHeight(dp(52)); view.setOnClickListener(listener);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1,-2); params.topMargin = dp(8); parent.addView(view,params); return view;
    }
}
