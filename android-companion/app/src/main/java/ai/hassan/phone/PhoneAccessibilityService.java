package ai.hassan.phone;

import android.accessibilityservice.AccessibilityService;
import android.Manifest;
import android.content.pm.PackageManager;
import android.accessibilityservice.GestureDescription;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Intent;
import android.content.res.Configuration;
import android.graphics.Point;
import android.view.WindowManager;
import java.util.function.BooleanSupplier;
import android.graphics.Path;
import android.graphics.Rect;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import org.json.JSONArray;
import org.json.JSONObject;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;

public final class PhoneAccessibilityService extends AccessibilityService {
    static volatile PhoneAccessibilityService instance;
    private final Handler main = new Handler(Looper.getMainLooper());
    private RemoteControlLoop remote;
    @Override protected void onServiceConnected() { instance = this; updateDimensions(); refreshControl(); }
    @Override public void onConfigurationChanged(Configuration config) { super.onConfigurationChanged(config); updateDimensions(); }
    @SuppressWarnings("deprecation") private void updateDimensions() {
        WindowManager windows = getSystemService(WindowManager.class);
        if (Build.VERSION.SDK_INT >= 30) {
            Rect bounds = windows.getMaximumWindowMetrics().getBounds(); SessionState.width = bounds.width(); SessionState.height = bounds.height();
        } else { Point size = new Point(); windows.getDefaultDisplay().getRealSize(size); SessionState.width = size.x; SessionState.height = size.y; }
    }
    void refreshControl() {
        main.post(() -> {
            syncControl();
            if (remote != null) remote.refreshMetadata();
        });
    }
    private void syncControl() {
        boolean allowed = SessionState.preferences(this).getBoolean("control_allowed", false);
        boolean connected = !SessionState.preferences(this).getString("token", "").isEmpty() && (allowed || SessionState.active);
        if (!allowed) getSystemService(NotificationManager.class).cancel(43);
        if (!connected) {
            if (remote != null) { remote.close(); remote = null; }
            getSystemService(NotificationManager.class).cancel(43); return;
        }
        try {
            updateDimensions();
            NotificationManager manager = getSystemService(NotificationManager.class);
            manager.createNotificationChannel(new NotificationChannel("phone_control", "السماح الدائم بالتحكّم", NotificationManager.IMPORTANCE_LOW));
            PendingIntent stop = PendingIntent.getBroadcast(this, 2, new Intent(this, StopControlReceiver.class), PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
            PendingIntent open = PendingIntent.getActivity(this, 3, new Intent(this, MainActivity.class), PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
            if (allowed && (Build.VERSION.SDK_INT < 33 || checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED)) manager.notify(43, new Notification.Builder(this, "phone_control").setSmallIcon(android.R.drawable.ic_menu_view)
                .setContentTitle("Hassan: السماح الدائم بالتحكّم مفعّل")
                .setContentText("الكمبيوتر المربوط يستطيع التحكّم عند فتح قفل الهاتف. عرض الشاشة مستقل.")
                .setContentIntent(open).setOngoing(true).addAction(new Notification.Action.Builder(null, "إيقاف كل التحكّم", stop).build()).build());
            if (remote == null) { remote = new RemoteControlLoop(this); remote.start(); }
        } catch (Exception ex) { SessionState.status = "تعذّر اتصال التحكّم. تأكد من الربط والأذونات."; }
    }
    @Override public void onAccessibilityEvent(AccessibilityEvent event) { /* No background collection. */ }
    @Override public void onInterrupt() { /* Android interrupted feedback; each action checks current consent and lock. */ }
    @Override public void onDestroy() { if (instance == this) instance = null; if (remote != null) remote.close(); getSystemService(NotificationManager.class).cancel(43); super.onDestroy(); }

    JSONObject execute(String action, JSONObject args, long expiry, BooleanSupplier sessionRunning) throws Exception {
        CompletableFuture<JSONObject> result = new CompletableFuture<>();
        main.post(() -> {
            try {
                if (result.isDone()) return;
                if (!sessionRunning.getAsBoolean() || !SessionState.mayControl(this)) throw new IllegalStateException("Sharing is stopped, locked or unavailable");
                updateDimensions();
                if (System.currentTimeMillis() >= expiry) throw new IllegalStateException("Command expired");
                switch (action) {
                    case "inspect": result.complete(inspect()); break;
                    case "tap": gesture(args, false, result); break;
                    case "swipe": gesture(args, true, result); break;
                    case "text": result.complete(setText(args.getString("text"))); break;
                    case "key": result.complete(key(args.getString("key"))); break;
                    default: throw new IllegalArgumentException("Unsupported action");
                }
            } catch (Exception ex) { result.completeExceptionally(ex); }
        });
        try { return result.get(8, TimeUnit.SECONDS); }
        finally { if (!result.isDone()) result.cancel(false); }
    }
    private float coordinate(JSONObject args, String name, int length) throws Exception {
        double value = args.getDouble(name);
        if (!Double.isFinite(value) || value < 0 || value > 1) throw new IllegalArgumentException("Invalid coordinate");
        return (float) (value * Math.max(0, length - 1));
    }
    private void gesture(JSONObject args, boolean swipe, CompletableFuture<JSONObject> result) throws Exception {
        Path path = new Path();
        path.moveTo(coordinate(args, swipe ? "x1" : "x", SessionState.width), coordinate(args, swipe ? "y1" : "y", SessionState.height));
        int duration = 65;
        if (swipe) {
            duration = args.optInt("duration_ms", 300);
            if (duration < 50 || duration > 2000) throw new IllegalArgumentException("Swipe duration must be 50..2000 ms");
            path.lineTo(coordinate(args, "x2", SessionState.width), coordinate(args, "y2", SessionState.height));
        }
        GestureDescription gesture = new GestureDescription.Builder().addStroke(new GestureDescription.StrokeDescription(path, 0, duration)).build();
        boolean queued = dispatchGesture(gesture, new GestureResultCallback() {
            @Override public void onCompleted(GestureDescription gesture) { result.complete(success("gesture_completed")); }
            @Override public void onCancelled(GestureDescription gesture) { result.completeExceptionally(new IllegalStateException("Gesture cancelled")); }
        }, main);
        if (!queued) throw new IllegalStateException("Android rejected the gesture");
    }
    private JSONObject key(String value) throws Exception {
        boolean ok;
        switch (value) {
            case "home": ok = performGlobalAction(GLOBAL_ACTION_HOME); break;
            case "back": ok = performGlobalAction(GLOBAL_ACTION_BACK); break;
            case "recents": ok = performGlobalAction(GLOBAL_ACTION_RECENTS); break;
            case "enter":
                if (Build.VERSION.SDK_INT < 30) throw new IllegalStateException("Enter requires Android 11 or later");
                AccessibilityNodeInfo root = getRootInActiveWindow();
                if (root == null) throw new IllegalStateException("No active window");
                AccessibilityNodeInfo focused = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
                try {
                    if (focused == null || passwordAncestor(focused)) throw new IllegalStateException("No accessible non-password field is focused");
                    ok = focused.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.getId());
                } finally { if (focused != null) focused.recycle(); root.recycle(); }
                break;
            default: throw new IllegalArgumentException("Unsupported key");
        }
        if (!ok) throw new IllegalStateException("Android or this app rejected the key");
        return success("key_sent");
    }
    private JSONObject setText(String text) throws Exception {
        if (text.isEmpty() || text.length() > 1000) throw new IllegalArgumentException("Text must contain 1..1000 characters");
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) throw new IllegalStateException("No active window");
        AccessibilityNodeInfo focused = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
        try {
            if (focused == null || !focused.isEditable() || !focused.isEnabled() || passwordAncestor(focused))
                throw new IllegalStateException("Tap an editable non-password field first");
            Bundle arguments = new Bundle(); arguments.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text);
            if (!focused.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, arguments)) throw new IllegalStateException("This app does not support accessible text entry");
            return success("field_replaced");
        } finally { if (focused != null) focused.recycle(); root.recycle(); }
    }
    private boolean passwordAncestor(AccessibilityNodeInfo node) {
        if (node.isPassword()) return true;
        AccessibilityNodeInfo parent = node.getParent();
        int depth = 0;
        while (parent != null && depth++ < 50) {
            boolean password = parent.isPassword();
            AccessibilityNodeInfo next = password ? null : parent.getParent(); parent.recycle();
            if (password) return true;
            parent = next;
        }
        if (parent != null) { parent.recycle(); return true; }
        return false;
    }
    private JSONObject inspect() throws Exception {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) throw new IllegalStateException("No accessible window");
        try {
            JSONArray nodes = new JSONArray(); int[] visited = {0}; walk(root, nodes, 0, visited);
            JSONObject result = new JSONObject().put("package", trim(root.getPackageName())).put("width", SessionState.width)
                .put("height", SessionState.height).put("nodes", nodes).put("truncated", nodes.length() >= 100 || visited[0] >= 500);
            while (nodes.length() > 0 && result.toString().getBytes(java.nio.charset.StandardCharsets.UTF_8).length > 48 * 1024) {
                nodes.remove(nodes.length() - 1); result.put("truncated", true);
            }
            return result;
        } finally { root.recycle(); }
    }
    private void walk(AccessibilityNodeInfo node, JSONArray output, int depth, int[] visited) throws Exception {
        if (node == null || depth > 35 || output.length() >= 100 || visited[0]++ >= 500 || !node.isVisibleToUser()) return;
        Rect rect = new Rect(); node.getBoundsInScreen(rect);
        JSONArray bounds = new JSONArray().put(normalize(rect.left, SessionState.width)).put(normalize(rect.top, SessionState.height))
            .put(normalize(rect.right, SessionState.width)).put(normalize(rect.bottom, SessionState.height));
        if (node.isPassword()) {
            output.put(new JSONObject().put("redacted", true).put("bounds", bounds)); return;
        }
        JSONObject item = new JSONObject().put("class", trim(node.getClassName())).put("text", trim(node.getText()))
            .put("description", trim(node.getContentDescription())).put("bounds", bounds)
            .put("clickable", node.isClickable()).put("editable", node.isEditable()).put("scrollable", node.isScrollable())
            .put("enabled", node.isEnabled()).put("focused", node.isFocused());
        output.put(item);
        for (int i = 0; i < node.getChildCount() && output.length() < 100 && visited[0] < 500; i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            try { walk(child, output, depth + 1, visited); } finally { if (child != null) child.recycle(); }
        }
    }
    private static double normalize(int coordinate, int size) { return Math.max(0, Math.min(1, coordinate / (double) Math.max(1, size))); }
    private static String trim(CharSequence value) { if (value == null) return ""; String text = value.toString(); return text.substring(0,Math.min(160,text.length())); }
    private static JSONObject success(String outcome) {
        JSONObject value = new JSONObject(); try { value.put("outcome", outcome); } catch (Exception ignored) {} return value;
    }
}
