# Ron uses only platform APIs. Keep the custom view constructors for safety.
-keep public class com.alexcodesthings.ronface.RonFaceView {
    public <init>(...);
}
