# TERRA-CORE AgriSat — Mobile App Build Guide

## Option A: PWA (Easiest — No Installation Required)

### Install on Android
1. Open Chrome on your Android phone
2. Navigate to `http://<your-computer-ip>:7432` (run `npm start` first)
3. Tap the **three-dot menu** → **"Add to Home Screen"**
4. Tap **"Add"** — app icon appears on your home screen
5. Open the app → enter ESP32 IP → CONNECT

### Install on iPhone / iPad
1. Open Safari on your iPhone
2. Navigate to `http://<your-computer-ip>:7432`
3. Tap the **Share button** (box with arrow) → **"Add to Home Screen"**
4. Tap **"Add"** — app appears like a native app
5. Open the app → enter ESP32 IP → CONNECT

> ✅ PWA mode works on any device with a browser — no app store needed!

---

## Option B: Android APK (Full Native App)

### Prerequisites
```
1. Node.js 18+         → https://nodejs.org
2. Android Studio      → https://developer.android.com/studio
3. Java 17 (JDK)       → bundled with Android Studio
```

### Step-by-Step Build

#### 1. Install dependencies
```bash
cd /Users/rudrakshpatel/Desktop/SatelliteSoilMonitoring
npm install
```

#### 2. Add Android platform
```bash
npx cap add android
```

#### 3. Sync web files to Android project
```bash
npx cap sync android
```

#### 4. Open in Android Studio
```bash
npx cap open android
```

#### 5. Build APK in Android Studio
```
Build → Build Bundle(s) / APK(s) → Build APK(s)
```
APK will be at:
```
android/app/build/outputs/apk/debug/app-debug.apk
```

#### 6. Install on phone
```bash
# Connect phone via USB, enable USB debugging
adb install android/app/build/outputs/apk/debug/app-debug.apk
```
Or: share the APK file directly to your phone and open it.

---

### Build Release APK (for distribution)

#### 1. Generate a keystore
```bash
keytool -genkey -v -keystore agrisat-release.jks \
  -keyalg RSA -keysize 2048 -validity 10000 \
  -alias agrisat
```

#### 2. Add to android/app/build.gradle
```groovy
android {
    signingConfigs {
        release {
            storeFile file('../../agrisat-release.jks')
            storePassword 'YOUR_STORE_PASSWORD'
            keyAlias 'agrisat'
            keyPassword 'YOUR_KEY_PASSWORD'
        }
    }
    buildTypes {
        release {
            signingConfig signingConfigs.release
            minifyEnabled false
        }
    }
}
```

#### 3. Build release APK
```bash
cd android
./gradlew assembleRelease
```
APK at: `android/app/build/outputs/apk/release/app-release.apk`

---

## Option C: iOS IPA (Requires Mac + Xcode)

```bash
npx cap add ios
npx cap sync ios
npx cap open ios
```
Then in Xcode: Product → Archive → Distribute App

---

## App Features (All Platforms)

| Feature | PWA | Android APK | iOS IPA |
|---------|-----|-------------|---------|
| Home screen icon | ✅ | ✅ | ✅ |
| Offline support | ✅ | ✅ | ✅ |
| ESP32 WebSocket (LOCAL) | ✅ | ✅ | ✅ |
| **MQTT Cloud (ANYWHERE)** | ✅ | ✅ | ✅ |
| Push notifications | ❌ | ✅ (future) | ✅ (future) |
| Background tasks | ❌ | ✅ (future) | ✅ (future) |
| No app store needed | ✅ | ✅ | ❌ |
| Auto-updates | ✅ | Manual | Manual |

---

## Cloud MQTT Setup (Remote Access from Anywhere)

### How it works
- Dashboard + ESP32 both connect to **HiveMQ** (free public MQTT broker)
- No port forwarding, no VPN, no server to rent
- Works from any network worldwide

### ESP32 Cloud Firmware
```bash
# Flash: hardware/esp32/esp32_agrisat_cloud.ino
# Required library: PubSubClient by Nick O'Leary (v2.8+)
# Edit these in the .ino file:
#   const char* WIFI_SSID = "YOUR_WIFI";
#   const char* FARM_ID   = "your-unique-farm-id";
```

### Dashboard (any device, anywhere)
```
1. Open dashboard → click [HW] button top-right
2. Switch to "☁ CLOUD MQTT" tab
3. Enter the same Farm ID as in firmware
4. Click CONNECT — green = live from anywhere!
```

---

## Generate PWA Icons

Open `icons/gen-icons.html` in any browser and click **GENERATE ALL ICONS**.
Move the downloaded PNGs to the `icons/` folder for full PWA compatibility.

---

## Troubleshooting APK Build

**"SDK not found"**
```bash
# In Android Studio: SDK Manager → Install Android 13 (API 33)
```

**"Capacitor not found"**
```bash
npm install @capacitor/core @capacitor/cli @capacitor/android
```

**"Mixed content blocked" (HTTP WebSocket)**
```bash
# Already configured in capacitor.config.json:
# "allowMixedContent": true
```

**App can't reach ESP32**
- Ensure phone and ESP32 are on the SAME WiFi network
- For Capacitor: configured in allowNavigation: ["192.168.*.*"]
- Check ESP32 Serial Monitor for current IP address

---

## Quick Cheatsheet

```bash
# Start local server (for PWA testing)
npm start                          # → http://localhost:7432

# Update app after code changes
npx cap sync

# Rebuild APK
npx cap build android

# Run on connected Android device
npx cap run android
```
