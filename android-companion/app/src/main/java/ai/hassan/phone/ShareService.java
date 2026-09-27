package ai.hassan.phone;

import android.app.Activity;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.content.pm.ServiceInfo;
import android.graphics.Bitmap;
import android.graphics.PixelFormat;
import android.graphics.Point;
import android.graphics.Rect;
import android.hardware.display.DisplayManager;
import android.hardware.display.VirtualDisplay;
import android.media.Image;
import android.media.ImageReader;
import android.media.projection.MediaProjection;
import android.media.projection.MediaProjectionManager;
import android.os.Build;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.IBinder;
import android.os.Looper;
import android.os.SystemClock;
import android.view.WindowManager;
import org.json.JSONObject;
import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;
import java.util.LinkedHashSet;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicReference;

public final class ShareService extends Service {
    static final String START = "ai.hassan.phone.START", STOP = "ai.hassan.phone.STOP";
    private static final String CHANNEL = "phone_sharing";
    private static final int NOTIFICATION = 42;
    private static volatile ShareService instance;
    static boolean isRunning() { return instance != null; }
    static void pauseDisplayFrames() {
        SessionState.active = false; SessionState.captureReady = false; SessionState.coordinatesReady = false;
        ShareService current = instance;
        if (current != null) { current.latestFrame.set(null); if (current.api != null) current.api.close(); }
        SessionState.status = "أوقف الكمبيوتر عرض الشاشة. إذن التحكّم الدائم لم يتغيّر.";
    }
    private final Handler main = new Handler(Looper.getMainLooper());
    private final Object captureLock = new Object();
    private final AtomicReference<byte[]> latestFrame = new AtomicReference<>();
    private final ExecutorService uploading = Executors.newSingleThreadExecutor();
    private HandlerThread imageThread;
    private Handler imageHandler;
    private MediaProjection projection;
    private MediaProjection.Callback projectionCallback;
    private VirtualDisplay virtualDisplay;
    private ImageReader reader;
    private ApiClient api;
    private long lastFrameAt;
    private int captureWidth, captureHeight;
    private boolean started, receiverRegistered, displayRegistered;
    private final BroadcastReceiver screenOff = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) { stopSession("انتهت المشاركة عند إطفاء الشاشة. افتح الهاتف وابدأ جلسة جديدة."); }
    };
    private final DisplayManager.DisplayListener displays = new DisplayManager.DisplayListener() {
        @Override public void onDisplayAdded(int id) {}
        @Override public void onDisplayRemoved(int id) {}
        @Override public void onDisplayChanged(int id) {
            if (!SessionState.active || id != android.view.Display.DEFAULT_DISPLAY) return;
            Point size = screenSize();
            if (size.x != captureWidth || size.y != captureHeight) {
                SessionState.coordinatesReady = false; latestFrame.set(null);
                if (Build.VERSION.SDK_INT < 34) stopSession("تغيّر اتجاه الهاتف. ابدأ المشاركة مجددًا لتطابق الشاشة والنقرات.");
            }
        }
    };
    @Override public IBinder onBind(Intent intent) { return null; }
    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent == null || STOP.equals(intent.getAction())) {
            stopSession("المشاركة متوقفة. عرض الشاشة متوقف. إذن التحكّم الدائم يبقى حسب اختيارك."); return START_NOT_STICKY;
        }
        if (!START.equals(intent.getAction()) || started) return START_NOT_STICKY;
        started = true; instance = this;
        try {
            SharedPreferences prefs = SessionState.preferences(this);
            String token = prefs.getString("token", "");
            if (token.isEmpty() || SessionState.locked(this)) throw new IllegalStateException("Phone not ready");
            NotificationManager notifications = getSystemService(NotificationManager.class);
            notifications.createNotificationChannel(new NotificationChannel(CHANNEL, "مشاركة هاتفك مع الكمبيوتر", NotificationManager.IMPORTANCE_LOW));
            Intent open = new Intent(this, MainActivity.class);
            PendingIntent show = PendingIntent.getActivity(this, 0, open, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
            PendingIntent stop = PendingIntent.getService(this, 1, new Intent(this, ShareService.class).setAction(STOP), PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
            Notification notification = new Notification.Builder(this, CHANNEL)
                .setSmallIcon(android.R.drawable.ic_menu_view).setContentTitle("Hassan: هاتفك مشارَك الآن")
                .setContentText("الشاشة تُعرض على الكمبيوتر. إيقاف العرض لا يلغي إذن التحكّم الدائم.")
                .setContentIntent(show).setOngoing(true).addAction(new Notification.Action.Builder(null, "إيقاف العرض", stop).build()).build();
            if (Build.VERSION.SDK_INT >= 29) startForeground(NOTIFICATION, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION);
            else startForeground(NOTIFICATION, notification);
            Intent captureData = Build.VERSION.SDK_INT >= 33 ? intent.getParcelableExtra("capture_data", Intent.class) : intent.getParcelableExtra("capture_data");
            if (captureData == null || intent.getIntExtra("capture_result", 0) != Activity.RESULT_OK) throw new IllegalStateException("Fresh screen permission required");
            api = new ApiClient(prefs.getString("origin", ""), token);
            imageThread = new HandlerThread("hassan-screen"); imageThread.start(); imageHandler = new Handler(imageThread.getLooper());
            projection = getSystemService(MediaProjectionManager.class).getMediaProjection(Activity.RESULT_OK, captureData);
            projectionCallback = new MediaProjection.Callback() {
                @Override public void onStop() { stopSession("انتهت مشاركة الشاشة. ابدأ جلسة جديدة من الهاتف عند الحاجة."); }
                @Override public void onCapturedContentResize(int width, int height) {
                    if (!SessionState.active || width <= 0 || height <= 0) return;
                    Point actual = screenSize();
                    if (width != actual.x || height != actual.y) {
                        stopSession("اختر مشاركة الشاشة كاملة لتطابق النقرات؛ ثم ابدأ جلسة جديدة."); return;
                    }
                    try { resizeCapture(width, height); } catch (Exception ex) { stopSession("تعذّر تحديث أبعاد الشاشة. أعد بدء المشاركة."); }
                }
            };
            projection.registerCallback(projectionCallback, main);
            SessionState.active = true;
            Point size = screenSize();
            resizeCapture(size.x, size.y);
            DisplayManager displayManager = getSystemService(DisplayManager.class);
            displayManager.registerDisplayListener(displays, main); displayRegistered = true;
            IntentFilter filter = new IntentFilter(Intent.ACTION_SCREEN_OFF);
            if (Build.VERSION.SDK_INT >= 33) registerReceiver(screenOff, filter, Context.RECEIVER_NOT_EXPORTED);
            else registerReceiver(screenOff, filter);
            receiverRegistered = true;
            if (PhoneAccessibilityService.instance != null) PhoneAccessibilityService.instance.refreshControl();
            uploading.submit(this::uploadLoop);
            SessionState.status = "المشاركة تعمل. تقدر توقفها فورًا من هنا أو من الإشعار.";
        } catch (Exception ex) { stopSession("تعذّر بدء المشاركة. تأكد من الأذونات والربط، ثم جرّب جلسة جديدة."); }
        return START_NOT_STICKY;
    }
    @SuppressWarnings("deprecation") private Point screenSize() {
        WindowManager windows = getSystemService(WindowManager.class);
        if (Build.VERSION.SDK_INT >= 30) { Rect bounds = windows.getMaximumWindowMetrics().getBounds(); return new Point(bounds.width(), bounds.height()); }
        Point size = new Point(); windows.getDefaultDisplay().getRealSize(size); return size;
    }
    private void resizeCapture(int width, int height) {
        if (width < 1 || height < 1 || width > 12000 || height > 12000) throw new IllegalArgumentException("Invalid screen size");
        synchronized (captureLock) {
            if (reader != null && width == captureWidth && height == captureHeight) {
                SessionState.coordinatesReady = true; return;
            }
            SessionState.captureReady = false; SessionState.coordinatesReady = false; latestFrame.set(null);
            double scale = Math.min(1.0, Math.min(720.0 / width, 1280.0 / height));
            int frameWidth = Math.max(1,(int)Math.round(width * scale)), frameHeight = Math.max(1,(int)Math.round(height * scale));
            ImageReader previous = reader;
            ImageReader next = ImageReader.newInstance(frameWidth, frameHeight, PixelFormat.RGBA_8888, 2);
            next.setOnImageAvailableListener(this::captureFrame, imageHandler);
            int density = getResources().getConfiguration().densityDpi;
            if (virtualDisplay == null) {
                virtualDisplay = projection.createVirtualDisplay("Hassan visible phone session", frameWidth, frameHeight, density,
                    DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR, next.getSurface(), null, imageHandler);
            } else {
                virtualDisplay.setSurface(null); virtualDisplay.resize(frameWidth, frameHeight, density); virtualDisplay.setSurface(next.getSurface());
            }
            reader = next; captureWidth = width; captureHeight = height; SessionState.width = width; SessionState.height = height;
            SessionState.coordinatesReady = true;
            if (previous != null) previous.close();
        }
    }
    private void captureFrame(ImageReader source) {
        synchronized (captureLock) {
            if (source != reader || !SessionState.active) return;
            try (Image image = source.acquireLatestImage()) {
                if (image == null) return;
                if (SessionState.locked(this)) { stopSession("قفل الهاتف أوقف عرض الشاشة والتحكّم مؤقتًا. افتحه وابدأ جلسة جديدة."); return; }
                if (!SessionState.coordinatesReady) return;
                long now = SystemClock.elapsedRealtime();
                if (now - lastFrameAt < 500) return;
                lastFrameAt = now;
                Image.Plane plane = image.getPlanes()[0]; ByteBuffer pixels = plane.getBuffer();
                int width = image.getWidth(), height = image.getHeight();
                if (plane.getPixelStride() != 4) throw new IllegalStateException("Unsupported capture pixel layout");
                int paddedWidth = plane.getRowStride() / plane.getPixelStride();
                int expectedBytes = paddedWidth * height * 4;
                if (pixels.remaining() < expectedBytes) {
                    // Some devices omit padding after the last row in the image buffer.
                    ByteBuffer complete = ByteBuffer.allocate(expectedBytes); complete.put(pixels); complete.rewind(); pixels = complete;
                }
                Bitmap padded = Bitmap.createBitmap(paddedWidth, height, Bitmap.Config.ARGB_8888);
                Bitmap cropped = null;
                try {
                    padded.copyPixelsFromBuffer(pixels);
                    cropped = Bitmap.createBitmap(padded, 0, 0, width, height);
                    ByteArrayOutputStream bytes = new ByteArrayOutputStream(); cropped.compress(Bitmap.CompressFormat.JPEG, 65, bytes);
                    if (bytes.size() <= 1024 * 1024 && SessionState.active && !SessionState.locked(this)) {
                        latestFrame.set(bytes.toByteArray());
                        if (!SessionState.captureReady) {
                            SessionState.captureReady = true;
                            if (PhoneAccessibilityService.instance != null) PhoneAccessibilityService.instance.refreshControl();
                        }
                    }
                } finally { if (cropped != null && cropped != padded) cropped.recycle(); padded.recycle(); }
            } catch (Exception ex) {
                if (SessionState.active) stopSession("تعذّر قراءة الشاشة. أعد بدء المشاركة من الهاتف.");
            }
        }
    }
    private void uploadLoop() {
        long retry = 500;
        while (SessionState.active && !Thread.currentThread().isInterrupted()) {
            try {
                byte[] jpeg = latestFrame.getAndSet(null);
                if (jpeg != null && SessionState.active && !SessionState.locked(this)) api.frame(jpeg);
                retry = 500;
            } catch (ApiClient.HttpError ex) {
                if (!SessionState.active) break;
                if (ex.status == 401) { unpair(); break; }
                // A frame can reach the PC before the next status poll enables display.
                // 403 is transient here; keep the pairing and projection consent.
                retry = Math.min(15000, Math.max(1000, retry * 2));
            } catch (Exception ex) { retry = Math.min(15000, Math.max(1000, retry * 2)); }
            if (!pause(retry)) break;
        }
    }
    private boolean pause(long millis) { try { Thread.sleep(millis); return SessionState.active; } catch (InterruptedException ex) { Thread.currentThread().interrupt(); return false; } }
    private void unpair() {
        SessionState.preferences(this).edit().remove("token").remove("device_id").putBoolean("control_allowed", false).apply();
        if (PhoneAccessibilityService.instance != null) PhoneAccessibilityService.instance.refreshControl();
        stopSession("انتهى أو أُلغي ربط الهاتف. اربطه مجددًا برمز جديد من الكمبيوتر.");
    }
    private void stopSession(String status) {
        SessionState.active = false; SessionState.captureReady = false; SessionState.coordinatesReady = false;
        SessionState.status = status; latestFrame.set(null);
        main.post(this::stopSelf);
    }
    @Override public void onDestroy() {
        if (instance == this) instance = null;
        SessionState.active = false; SessionState.captureReady = false; SessionState.coordinatesReady = false;
        latestFrame.set(null);
        if (api != null) api.close();
        uploading.shutdownNow();
        if (receiverRegistered) unregisterReceiver(screenOff);
        if (displayRegistered) getSystemService(DisplayManager.class).unregisterDisplayListener(displays);
        synchronized (captureLock) {
            if (virtualDisplay != null) { virtualDisplay.release(); virtualDisplay = null; }
            if (reader != null) { reader.close(); reader = null; }
            if (projection != null) {
                if (projectionCallback != null) projection.unregisterCallback(projectionCallback);
                projection.stop(); projection = null;
            }
        }
        if (imageThread != null) imageThread.quitSafely();
        if (PhoneAccessibilityService.instance != null) PhoneAccessibilityService.instance.refreshControl();
        stopForeground(STOP_FOREGROUND_REMOVE);
        super.onDestroy();
    }
}
