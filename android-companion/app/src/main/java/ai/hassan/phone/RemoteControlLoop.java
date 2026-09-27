package ai.hassan.phone;

import android.content.Context;
import org.json.JSONObject;
import java.util.LinkedHashSet;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Owned by the user-enabled, system-bound accessibility service; never a hidden projection service. */
final class RemoteControlLoop implements AutoCloseable {
    private final Context context;
    private final ApiClient api;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private static final LinkedHashSet<String> executed = new LinkedHashSet<>();
    private volatile boolean running = true;
    private volatile long metadataVersion;
    RemoteControlLoop(Context context) throws Exception {
        this.context = context;
        api = new ApiClient(SessionState.preferences(context).getString("origin", ""), SessionState.preferences(context).getString("token", ""));
    }
    void start() { worker.submit(this::pollLoop); }
    void refreshMetadata() {
        metadataVersion++;
        // A display update may occur while an already-executed action is uploading
        // its result. Only interrupt polls; never cancel that result delivery.
        api.cancelPoll();
    }
    private void pollLoop() {
        long retry = 1000;
        while (running && !Thread.currentThread().isInterrupted()) {
            long pollingVersion = metadataVersion;
            try {
                
                JSONObject state = new JSONObject().put("control_enabled", SessionState.mayControl(context))
                    .put("screen_enabled", SessionState.active && SessionState.captureReady && !SessionState.locked(context)).put("width", SessionState.width).put("height", SessionState.height);
                JSONObject reply = api.json("/api/phone/poll", state, 28000);
                if (!running) break;
                retry = 1000;
                JSONObject command = reply.optJSONObject("command");
                if (command != null) execute(command, pollingVersion);
                SessionState.status = SessionState.locked(context) ? "الهاتف مقفل؛ التحكّم متوقف حتى تفتحه بنفسك."
                    : SessionState.preferences(context).getBoolean("control_allowed", false)
                        ? "السماح الدائم مفعّل. تقدر توقف العرض وحده أو توقف كل التحكّم."
                        : "عرض الشاشة فقط؛ التحكّم غير مسموح.";
                // Also bounds request rate if a server returns empty polls immediately.
                pause(100);
            } catch (ApiClient.HttpError ex) {
                if (!running) break;
                if (pollingVersion != metadataVersion) continue;
                if (ex.status == 401) { unpair(); break; }
                SessionState.status = "الاتصال بالكمبيوتر متعذّر مؤقتًا. لا تُعاد الأوامر السابقة.";
                if (!pause(retry)) break; retry = Math.min(30000, retry * 2);
            } catch (Exception ex) {
                if (!running) break;
                if (pollingVersion != metadataVersion) continue;
                SessionState.status = "انقطع الاتصال. بانتظار عودة الإنترنت؛ لا تُعاد الأوامر السابقة.";
                if (!pause(retry)) break; retry = Math.min(30000, retry * 2);
            }
        }
    }
    private void execute(JSONObject command, long commandVersion) throws Exception {
        String id = command.optString("id", "");
        if (id.isEmpty() || id.length() > 128) return;
        JSONObject report = new JSONObject().put("id", id);
        boolean stopDisplay = false;
        try {
            synchronized (executed) {
                if (!executed.add(id)) throw new IllegalStateException("Duplicate command ignored");
                while (executed.size() > 512) executed.remove(executed.iterator().next());
            }
            long expires = command.getLong("expires_at"), now = System.currentTimeMillis();
            if (expires <= now || expires > now + 120000) throw new IllegalStateException("Command expired or invalid deadline");
            if (!running || metadataVersion != commandVersion) throw new IllegalStateException("Phone session changed before action execution");
            if ("stop_screen".equals(command.getString("action"))) {
                JSONObject args = command.optJSONObject("args");
                if (args != null && args.length() != 0) throw new IllegalArgumentException("stop_screen takes no arguments");
                if (!running) throw new IllegalStateException("Session stopped");
                stopDisplay = true; ShareService.pauseDisplayFrames();
                report.put("ok", true).put("result", new JSONObject().put("outcome", "screen_stopped"));
            } else {
            if (!SessionState.mayControl(context)) throw new IllegalStateException("Phone control is stopped or unavailable");
            PhoneAccessibilityService accessibility = PhoneAccessibilityService.instance;
            if (accessibility == null) throw new IllegalStateException("Accessibility is disabled");
            JSONObject result = accessibility.execute(command.getString("action"), command.optJSONObject("args") == null ? new JSONObject() : command.getJSONObject("args"), expires, () -> running && metadataVersion == commandVersion);
            report.put("ok", true).put("result", result);
            }
        } catch (Exception ex) {
            Throwable cause = ex.getCause() == null ? ex : ex.getCause();
            String error = cause instanceof IllegalArgumentException || cause instanceof IllegalStateException ? cause.getMessage() : "Phone could not complete the action";
            report.put("ok", false).put("result", new JSONObject()).put("error", error == null ? "Action failed" : error);
        }
        // A failed result upload is not retried: executing a gesture twice could send unintended input.
        try { if (running) api.json("/api/phone/result", report, 8000); }
        finally {
            // Frames have already stopped. Release projection after the acknowledgement so
            // display-only teardown cannot cancel this command's result upload.
            if (stopDisplay) context.stopService(new android.content.Intent(context, ShareService.class));
        }
    }

    private boolean pause(long millis) {
        try { Thread.sleep(millis); return running; }
        catch (InterruptedException ex) { Thread.currentThread().interrupt(); return false; }
    }
    private void unpair() {
        SessionState.preferences(context).edit().remove("token").remove("device_id").putBoolean("control_allowed", false).apply();
        SessionState.status = "أُلغي الربط من الكمبيوتر. اربط الهاتف برمز جديد.";
        if (PhoneAccessibilityService.instance != null) PhoneAccessibilityService.instance.refreshControl();
        context.stopService(new android.content.Intent(context, ShareService.class));
        close();
    }
    @Override public void close() { running = false; api.close(); worker.shutdownNow(); }
}
