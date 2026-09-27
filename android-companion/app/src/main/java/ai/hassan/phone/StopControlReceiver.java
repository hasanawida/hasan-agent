package ai.hassan.phone;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
public final class StopControlReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context context, Intent intent) { stopAll(context); }
    static void stopAll(Context context) {
        SessionState.preferences(context).edit().putBoolean("control_allowed", false).apply();
        SessionState.active = false;
        context.stopService(new Intent(context, ShareService.class));
        if (PhoneAccessibilityService.instance != null) PhoneAccessibilityService.instance.refreshControl();
        SessionState.status = "تم إيقاف العرض وكل التحكّم. الربط محفوظ ويمكنك تفعيل الإذن مجددًا.";
    }
}
