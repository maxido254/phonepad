package com.phonepad;

import android.app.Activity;
import android.content.Context;
import android.content.SharedPreferences;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Looper;
import android.text.InputType;
import android.view.Gravity;
import android.view.Surface;
import android.view.View;
import android.view.WindowManager;
import android.webkit.JavascriptInterface;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.nio.charset.StandardCharsets;

/**
 * PCStation Gamepad: connect screen + full-screen PS5-style controller.
 * The controller UI lives in assets/index.html; input is sent straight to the
 * PCStation Gamepad PC app over UDP on the local network. No internet, no server.
 */
public class MainActivity extends Activity implements SensorEventListener {

    private static final int PORT = 8765;
    private static final String PREFS = "phonepad";
    private static final String KEY_HOST = "host";

    private WebView web;
    private WebView splash;               // startup animation, shown once at launch
    private boolean splashDone = false;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private EditText hostInput;
    private TextView hint;

    private SensorManager sensors;
    private Sensor gyro;
    private boolean gyroOn = false;

    private HandlerThread netThread;
    private Handler net;
    private DatagramSocket socket;      // used on the net thread only
    private InetAddress target;         // used on the net thread only
    private volatile long lastPong = 0;
    private volatile int slot = 0;           // which player the PC gave us (1-4)
    private volatile boolean pcFull = false; // PC already has 4 phones
    private volatile byte[] pingBytes = "PING".getBytes(StandardCharsets.UTF_8);
    private volatile boolean running = false;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        if (Build.VERSION.SDK_INT >= 28) {
            getWindow().getAttributes().layoutInDisplayCutoutMode =
                    WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES;
        }
        netThread = new HandlerThread("phonepad-net");
        netThread.start();
        net = new Handler(netThread.getLooper());
        sensors = (SensorManager) getSystemService(Context.SENSOR_SERVICE);
        gyro = sensors.getDefaultSensor(Sensor.TYPE_GYROSCOPE);
        showSplash();
    }

    /** Stable per-phone id so the PC can tell phones apart and give us the same player slot back. */
    private String deviceId() {
        SharedPreferences p = getSharedPreferences(PREFS, MODE_PRIVATE);
        String id = p.getString("device_id", null);
        if (id == null) {
            id = java.util.UUID.randomUUID().toString().substring(0, 8);
            p.edit().putString("device_id", id).apply();
        }
        return id;
    }

    // ------------------------------------------------------------ startup screen

    /** Plays assets/splash.html (light ribbons, then the gamepad logo and app name), then opens the connect screen. */
    private void showSplash() {
        splash = new WebView(this);
        splash.setBackgroundColor(0xFF05070B);
        WebSettings st = splash.getSettings();
        st.setJavaScriptEnabled(true);
        st.setAllowFileAccess(true);
        splash.setOverScrollMode(View.OVER_SCROLL_NEVER);
        splash.setHapticFeedbackEnabled(false);
        splash.setLongClickable(false);
        splash.addJavascriptInterface(new SplashBridge(), "SplashNative");
        splash.loadUrl("file:///android_asset/splash.html");
        setContentView(splash);
        immersive();
        ui.postDelayed(new Runnable() {          // safety net: never get stuck on the startup screen
            @Override public void run() { finishSplash(); }
        }, 8000);
    }

    private void finishSplash() {
        if (splashDone || isFinishing()) return;
        splashDone = true;
        if (splash != null) {
            splash.destroy();
            splash = null;
        }
        showConnect();
    }

    private class SplashBridge {
        @JavascriptInterface
        public void done() {
            runOnUiThread(new Runnable() { @Override public void run() { finishSplash(); } });
        }
    }

    // ------------------------------------------------------------ connect screen

    private int dp(int v) {
        return (int) (v * getResources().getDisplayMetrics().density);
    }

    private void showConnect() {
        getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_VISIBLE);

        ScrollView sv = new ScrollView(this);
        sv.setBackgroundColor(0xFF05070B);

        LinearLayout col = new LinearLayout(this);
        col.setOrientation(LinearLayout.VERTICAL);
        col.setGravity(Gravity.CENTER_HORIZONTAL);
        col.setPadding(dp(32), dp(20), dp(32), dp(20));

        TextView title = new TextView(this);
        title.setText("PCStation Gamepad");
        title.setTextColor(0xFFFFFFFF);
        title.setTextSize(26);
        col.addView(title);

        TextView sub = new TextView(this);
        sub.setText("1. Open PCStation Gamepad on your computer\n2. Same Wi-Fi on both, then tap Find my PC\nUp to 4 phones can play at once \u2014 each is its own player");
        sub.setTextColor(0xFF9FB0C3);
        sub.setTextSize(13);
        sub.setGravity(Gravity.CENTER);
        sub.setPadding(0, dp(6), 0, dp(10));
        col.addView(sub);

        hostInput = new EditText(this);
        hostInput.setHint("PC address, e.g. 192.168.1.20");
        hostInput.setHintTextColor(0xFF6B7785);
        hostInput.setTextColor(0xFFFFFFFF);
        hostInput.setSingleLine(true);
        hostInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        hostInput.setText(prefs.getString(KEY_HOST, ""));
        col.addView(hostInput, new LinearLayout.LayoutParams(dp(320), LinearLayout.LayoutParams.WRAP_CONTENT));

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER);

        Button find = new Button(this);
        find.setText("Find my PC");
        find.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) { findPc(); }
        });
        row.addView(find);

        Button connect = new Button(this);
        connect.setText("Connect");
        connect.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) { connectClicked(); }
        });
        row.addView(connect);
        col.addView(row);

        hint = new TextView(this);
        hint.setTextColor(0xFFD29922);
        hint.setTextSize(13);
        hint.setGravity(Gravity.CENTER);
        hint.setPadding(0, dp(8), 0, 0);
        col.addView(hint);

        sv.addView(col);
        setContentView(sv);
    }

    private InetAddress directedBroadcast() {
        try {
            WifiManager wm = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
            int ip = wm.getDhcpInfo().ipAddress;
            int mask = wm.getDhcpInfo().netmask;
            if (ip == 0 || mask == 0) return null;
            int b = (ip & mask) | ~mask;
            return InetAddress.getByAddress(new byte[]{(byte) b, (byte) (b >> 8), (byte) (b >> 16), (byte) (b >> 24)});
        } catch (Exception e) {
            return null;
        }
    }

    private void findPc() {
        hint.setText("Searching…");
        final InetAddress directed = directedBroadcast();
        new Thread(new Runnable() {
            @Override public void run() {
                String found = null;
                try (DatagramSocket s = new DatagramSocket()) {
                    s.setBroadcast(true);
                    s.setSoTimeout(3000);
                    byte[] q = "PHONEPAD?".getBytes(StandardCharsets.UTF_8);
                    s.send(new DatagramPacket(q, q.length, InetAddress.getByName("255.255.255.255"), PORT));
                    if (directed != null) s.send(new DatagramPacket(q, q.length, directed, PORT));
                    DatagramPacket r = new DatagramPacket(new byte[64], 64);
                    s.receive(r);
                    if (new String(r.getData(), 0, r.getLength(), StandardCharsets.UTF_8).startsWith("PHONEPAD!")) {
                        found = r.getAddress().getHostAddress();
                    }
                } catch (Exception ignored) { }
                final String f = found;
                runOnUiThread(new Runnable() {
                    @Override public void run() {
                        if (hint == null) return;
                        if (f != null) {
                            hostInput.setText(f);
                            hint.setText("Found your PC at " + f + ". Tap Connect.");
                        } else {
                            hint.setText("PC not found. Check PCStation Gamepad is open and both are on the same Wi-Fi, or type the address shown on the PC.");
                        }
                    }
                });
            }
        }).start();
    }

    private void connectClicked() {
        String h = hostInput.getText().toString().trim();
        if (h.isEmpty()) {
            hint.setText("Enter the PC address or tap Find my PC.");
            return;
        }
        int colon = h.indexOf(':');
        if (colon > 0) h = h.substring(0, colon);
        getSharedPreferences(PREFS, MODE_PRIVATE).edit().putString(KEY_HOST, h).apply();
        startController(h);
    }

    // ------------------------------------------------------------ controller + networking

    private void startController(final String host) {
        running = true;
        lastPong = 0;
        slot = 0;
        pcFull = false;
        String model = (Build.MODEL == null ? "phone" : Build.MODEL).replace("|", " ");
        pingBytes = ("PING|" + deviceId() + "|" + model).getBytes(StandardCharsets.UTF_8);
        net.removeCallbacks(pinger);
        net.post(new Runnable() {
            @Override public void run() {
                closeSocket();
                try {
                    socket = new DatagramSocket();
                    target = InetAddress.getByName(host);
                } catch (Exception e) {
                    socket = null;
                    return;
                }
                final DatagramSocket s = socket;
                new Thread(new Runnable() {
                    @Override public void run() {
                        byte[] buf = new byte[64];
                        while (running && !s.isClosed()) {
                            try {
                                DatagramPacket p = new DatagramPacket(buf, buf.length);
                                s.receive(p);
                                String msg = new String(p.getData(), 0, p.getLength(), StandardCharsets.UTF_8);
                                if (msg.startsWith("PONG")) {
                                    lastPong = System.currentTimeMillis();
                                    pcFull = false;
                                    String[] parts = msg.trim().split("\\|");
                                    try { slot = parts.length > 1 ? Integer.parseInt(parts[1]) : 0; } catch (NumberFormatException e) { slot = 0; }
                                } else if (msg.startsWith("FULL")) {
                                    pcFull = true;
                                    slot = 0;
                                }
                            } catch (Exception e) {
                                break;
                            }
                        }
                    }
                }).start();
                net.post(pinger);
            }
        });

        web = new WebView(this);
        web.setBackgroundColor(0xFF05070B);
        WebSettings st = web.getSettings();
        st.setJavaScriptEnabled(true);
        st.setDomStorageEnabled(true);
        st.setAllowFileAccess(true);
        web.setOverScrollMode(View.OVER_SCROLL_NEVER);
        web.setHapticFeedbackEnabled(false);
        web.setLongClickable(false);
        web.addJavascriptInterface(new Bridge(), "PhonePadNative");
        web.loadUrl("file:///android_asset/index.html");
        setContentView(web);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        immersive();
    }

    private final Runnable pinger = new Runnable() {
        @Override public void run() {
            if (!running) return;
            sendPacket(pingBytes);
            final boolean ok = System.currentTimeMillis() - lastPong < 2500;
            final int sl = ok ? slot : 0;
            final String label = ok
                    ? (sl > 0 ? "Player " + sl + " \u00b7 connected" : "connected to PC")
                    : (pcFull ? "PC full (4/4)" : "searching for PC\u2026");
            runOnUiThread(new Runnable() {
                @Override public void run() {
                    if (web != null) web.evaluateJavascript("window.nativeStatus&&nativeStatus(" + ok + ",'" + label + "'," + sl + ")", null);
                }
            });
            net.postDelayed(this, 1000);
        }
    };

    private void sendPacket(byte[] b) {   // net thread only
        DatagramSocket s = socket;
        InetAddress t = target;
        if (s == null || t == null) return;
        try {
            s.send(new DatagramPacket(b, b.length, t, PORT));
        } catch (Exception ignored) { }
    }

    private void closeSocket() {          // net thread only
        if (socket != null) {
            socket.close();
            socket = null;
        }
    }

    private void immersive() {
        getWindow().getDecorView().setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY | View.SYSTEM_UI_FLAG_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                        | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN);
    }

    /** Methods the controller page calls. */
    private class Bridge {
        @JavascriptInterface
        public void send(String json) {
            final byte[] b = json.getBytes(StandardCharsets.UTF_8);
            net.post(new Runnable() { @Override public void run() { sendPacket(b); } });
            // UDP can drop packets: repeat button/trigger events once so a release is never lost.
            if (!json.contains("\"t\":\"s\"")) {
                net.postDelayed(new Runnable() { @Override public void run() { sendPacket(b); } }, 25);
            }
        }

        /** Saved control layout (position/size of every control). */
        @JavascriptInterface
        public void saveProfile(String name, String json) {
            getSharedPreferences(PREFS, MODE_PRIVATE).edit().putString("profile_" + name, json).apply();
        }

        @JavascriptInterface
        public String loadProfile(String name) {
            return getSharedPreferences(PREFS, MODE_PRIVATE).getString("profile_" + name, "");
        }

        @JavascriptInterface
        public void setGyro(final boolean on) {
            runOnUiThread(new Runnable() { @Override public void run() { setGyroEnabled(on); } });
        }
    }

    // ------------------------------------------------------------ gyro

    private void setGyroEnabled(boolean on) {
        gyroOn = on;
        sensors.unregisterListener(this);
        if (on && gyro != null) sensors.registerListener(this, gyro, SensorManager.SENSOR_DELAY_GAME);
    }

    @SuppressWarnings("deprecation")
    @Override
    public void onSensorChanged(SensorEvent e) {
        if (web == null) return;
        float gx = e.values[0] * 57.2958f;   // rad/s -> deg/s
        float gy = e.values[1] * 57.2958f;
        int rot = getWindowManager().getDefaultDisplay().getRotation();
        float x, y;
        if (rot == Surface.ROTATION_270) { x = gx; y = -gy; } else { x = -gx; y = gy; }
        web.evaluateJavascript("window.nativeGyro&&nativeGyro(" + x + "," + y + ")", null);
    }

    @Override public void onAccuracyChanged(Sensor sensor, int accuracy) { }

    @Override
    protected void onPause() {
        super.onPause();
        sensors.unregisterListener(this);
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (web != null) {
            immersive();
            if (gyroOn && gyro != null) sensors.registerListener(this, gyro, SensorManager.SENSOR_DELAY_GAME);
        }
    }

    @Override
    public void onBackPressed() {
        if (splash != null) {            // Back skips the startup animation
            finishSplash();
            return;
        }
        if (web != null) {
            setGyroEnabled(false);
            gyroOn = false;
            running = false;
            web.destroy();
            web = null;
            net.removeCallbacks(pinger);
            net.post(new Runnable() {
                @Override public void run() {
                    sendPacket("BYE".getBytes(StandardCharsets.UTF_8));
                    closeSocket();
                }
            });
            showConnect();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onDestroy() {
        running = false;
        ui.removeCallbacksAndMessages(null);
        if (splash != null) { splash.destroy(); splash = null; }
        netThread.quitSafely();
        super.onDestroy();
    }
}
