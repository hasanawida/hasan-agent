package ai.hassan.phone;

import android.app.KeyguardManager;
import android.content.Context;
import android.content.SharedPreferences;
import android.os.PowerManager;

final class SessionState {
    static volatile boolean active;
    static volatile boolean enrolling;
    static volatile boolean captureReady;
    static volatile boolean coordinatesReady;
    static volatile int width, height;
    static volatile String status = "عرض الشاشة متوقف. راجع حالة السماح الدائم.";
    static SharedPreferences preferences(Context c) { return c.getSharedPreferences("paired_phone", Context.MODE_PRIVATE); }
    static boolean locked(Context c) {
        KeyguardManager keyguard = c.getSystemService(KeyguardManager.class);
        PowerManager power = c.getSystemService(PowerManager.class);
        return keyguard == null || keyguard.isKeyguardLocked() || power == null || !power.isInteractive();
    }
    static boolean mayControl(Context c) {
        return preferences(c).getBoolean("control_allowed", false) && !locked(c)
            && width > 0 && height > 0 && (!active || coordinatesReady) && PhoneAccessibilityService.instance != null;
    }
    private SessionState() {}
}
