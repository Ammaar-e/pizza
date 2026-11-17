# Pizza POS — tablet & phone app (Android + iOS)

A native point-of-sale app for the restaurants on the Pizza platform. It runs on **Android tablets and
phones** and on **iPad / iPhone**. It takes card payments on a **BBPOS WisePad 3** reader through the
**bbpos** payment service (Stripe Terminal).

```
┌────────────── Pizza POS app (this folder) ──────────────┐
│ menu · customizer · cart · promotions · orders · cash   │──── HTTPS ───▶ Pizza platform API  (https://pos.amrt.ca/api/v1)
│ card payment flow (src/lib/cardFlow.ts)                 │                 orders, prices, tax, promotions, cash, devices
│ Stripe Terminal SDK ─── Bluetooth ─── BBPOS WisePad 3   │──── HTTPS ───▶ bbpos API           (https://<bbpos>/api)
└──────────────────────────────────────────────────────────┘                 Stripe keys, PaymentIntents, Terminal location
                                                     Pizza API ── server-to-server ──▶ bbpos  (verifies every card payment)
```

## How a card payment works

1. The **Pizza server** prices the order (menu, sizes, toppings, promotions, delivery fee, tax).
2. The app asks **bbpos** to charge exactly the amount due, plus an optional tip chosen on the tablet. It is
   sent as one custom-amount line with no extra tax, because tax is already in the Pizza total. A per-sale
   idempotency key makes retries safe.
3. The customer taps, inserts or swipes on the **WisePad 3** and enters any PIN on the reader. Card data
   never touches the app.
4. **bbpos verifies the result with Stripe.** Only `SUCCEEDED` continues. If the result is unclear (for
   example the network dropped), the app shows **Check again** and never assumes the outcome.
5. The app records the payment on the order with `reference = bbpos transaction id`. **The Pizza server
   re-verifies that transaction with bbpos** (status, amount + tip, currency, and that it is not already
   used on another order) before it marks the order paid.
6. If step 5 cannot reach the Pizza server, the approved payment is kept in a secure on-device queue and
   retried every 30 s. The **Orders** tab shows it, and it is never charged twice.

The bbpos project is used as-is (branch `claude/adoring-dirac-y07721`). This integration does not change it.

## Features

- Store sign-in with employee username + password, or **PIN** on a registered tablet.
- Device enrollment code, device registration by a manager, approval check, and heartbeat to the device
  dashboard (version, status, revocation).
- Menu by category; pizza customizer with sizes, crusts, removing included toppings, extra toppings,
  **left/right halves** and special instructions.
- Live server quote: subtotal, promotions (codes and suggested offers, with manager authorization when
  required), delivery eligibility, fees and tax.
- Pickup / delivery / dine-in, walk-in or phone orders, customer and address.
- Payments: **cash** with change and split amounts; **card via bbpos** with tip presets, custom tip,
  decline / cancel / retry and uncertain-result handling.
- Today's orders: status actions (accept, ready, out for delivery, complete, cancel), refunds (with
  manager code) and receipts.
- Cash drawer: open with float, drops / paid-outs / corrections, **blind close** with denomination count
  and variance explanation.
- Reader screen: hardware / simulated / mock modes, Bluetooth scan, connect, battery, firmware updates.
- Works on phones (stacked layout) and tablets (side-by-side menu and cart).

---

## 1. One-time setup

### 1a. bbpos (payments service)

In the **bbpos admin**:

1. **Stripe**: enter the Stripe **test** keys first (`sk_test_…`). Switch to live keys only after testing.
2. **Readers & Locations**: create or select a **Terminal location**. Readers register to it when they connect.
3. **Settings → Pricing**: keep **Allow custom amounts** turned on (the POS charges each order as one
   custom amount). Tips: turn on and set presets (for example 15 / 18 / 20 %) if you want the tip screen.
4. **Users**: create
   - one **Cashier** account for the tablets (for example `tablets@yourrestaurant.com`), and
   - one **Manager** account as the **service account** the Pizza server uses to verify and refund card
     payments (for example `pos-verifier@yourrestaurant.com`).
5. Optional, testing only: **Allow mock payments** (plus `ALLOW_MOCK_PAYMENTS=true` on the bbpos API) lets
   you test without Stripe or a reader. This is never possible in live mode.
6. The bbpos currency must match the store's currency (for example CAD).

### 1b. Pizza platform (franchise portal)

Open **Stores → (your store) → Pickup & payments**:

| Setting | Value |
|---|---|
| Card processor | **bbpos (WisePad 3 via POS tablet app)** |
| bbpos API URL (server-to-server) | how the Pizza server reaches bbpos, for example `https://pos-api.example.com` |
| bbpos URL for tablets | only if tablets use a different address |
| Accept bbpos MOCK transactions | **off** in production; on only while testing with mock mode |
| bbpos service account email / password | the bbpos **Manager** account from step 1a.4 (stored encrypted) |

Then click **Save** and **Save credentials**.

Devices: either create an **enrollment code** (Devices → Enrollment codes), or let a manager sign in on the
tablet once to register it. If HQ requires device approval, approve the tablet under **Devices**.

---

## 2. Get the app onto a device (development)

Requirements: **Node.js 22+**, npm, this repository, and a free **Expo account** (https://expo.dev/signup).

```bash
cd mobile
```

```bash
npm install
```

```bash
cp .env.example .env
```

Edit `.env`:

```
EXPO_PUBLIC_PIZZA_API_URL=https://pos.amrt.ca
EXPO_PUBLIC_BBPOS_API_URL=https://pos-api.example.com
```

These are only defaults. Both addresses can be changed on the device (sign-in screen → **Server settings**,
and **Settings → Card payments**).

> **Expo Go vs development build.** The card-reader SDK is native code, so a real WisePad 3 needs a
> **development build** of this app (options B–D below). **Expo Go** (option A) runs everything except the
> real reader: it uses the **mock** reader only.

### A. Quick look with Expo Go (no reader, 2 minutes)

1. Install **Expo Go** from Google Play or the App Store. It must support Expo SDK 58.
2. Run:
   ```bash
   npx expo start --go
   ```
3. Scan the QR code. On Android use Expo Go's scanner; on iPhone use the Camera app. The phone and
   computer must be on the same Wi-Fi; otherwise add `--tunnel`.

### B. Android tablet — development build from the cloud (no Android Studio needed)

1. Install the EAS command-line tool and sign in:
   ```bash
   npm install -g eas-cli
   ```
   ```bash
   eas login
   ```
2. Link the project once:
   ```bash
   eas init
   ```
   Copy the printed project id into `.env` as `EAS_PROJECT_ID=…`, **and** add
   `"EAS_PROJECT_ID": "…"` to the `env` block of each profile in `eas.json` (cloud builds do not read `.env`).
3. In `eas.json` → `development` → `env`, set the server addresses the tablet should use. On a local
   network, use your computer's LAN IP, for example `http://192.168.1.10:8000`.
4. Build the APK (about 10–20 minutes in the cloud):
   ```bash
   eas build --profile development --platform android
   ```
5. On the **tablet**, open the link or QR code printed at the end. Download the APK and install it; allow
   *Install unknown apps* for the browser when Android asks.
6. On your computer, start the development server:
   ```bash
   npx expo start --dev-client
   ```
   Open **Pizza POS** on the tablet. It finds the server on the same Wi-Fi, or you can scan the QR code from
   the app's launcher screen. Code changes reload instantly; rebuild only when native dependencies change.

### C. Android tablet over USB (Android Studio installed)

1. Install **Android Studio** (it includes the SDK and `adb`).
2. On the tablet: **Settings → About → tap "Build number" 7 times**, then **Developer options → USB
   debugging: on**. Connect the USB cable and accept the prompt on the tablet.
3. Check that the tablet is listed:
   ```bash
   adb devices
   ```
4. Build and install:
   ```bash
   npx expo run:android --device
   ```

### D. iPhone / iPad

A real iPhone or iPad needs an **Apple Developer Program** membership (USD 99 per year) for cloud builds.
For a local build on a Mac, a free Apple ID works (apps expire after 7 days).

**Cloud build (no Mac needed):**

1. Do steps B.1–B.3 above.
2. Register the device. Run the following, open the link **on the iPhone/iPad** and install the profile
   (Settings → General → VPN & Device Management):
   ```bash
   eas device:create
   ```
3. Build:
   ```bash
   eas build --profile development --platform ios
   ```
   Let EAS create or manage the certificates when it asks (sign in with your Apple ID).
4. Open the build link on the device and install it. On iOS 16 or later, turn on **Settings → Privacy &
   Security → Developer Mode** and restart when asked.
5. Start the development server and open the app:
   ```bash
   npx expo start --dev-client
   ```
6. When iOS asks, allow **Bluetooth** (for the reader) and **Local Network** (to reach the development server).

**Local build on a Mac with Xcode:**

```bash
npx expo run:ios --device
```

Pick the connected iPhone or iPad. If Xcode asks, choose your Apple ID team under *Signing & Capabilities*.

**Testers without registering devices:** use TestFlight:

```bash
eas build --profile production --platform ios
```

```bash
eas submit --platform ios
```

### E. A stand-alone test build (no development server)

For staff testing, use the `preview` profile. It installs like a normal app and talks to the URLs in
`eas.json` → `preview`:

```bash
eas build --profile preview --platform android
```

Install the APK from the link. For iOS, use `--platform ios` (registered devices) or TestFlight.

### Development networking checklist

- The device must reach both servers. With local servers, use the computer's **LAN IP**, never `localhost`.
- Android blocks `http://` unless the build has `ALLOW_CLEARTEXT=true`. The `development` profile already
  sets it. Production must use **HTTPS**.
- For local servers, allow the device's origin in the APIs if you also test in a browser
  (`CORS_ALLOWED_ORIGINS` on Pizza, `CORS_ORIGINS` on bbpos). Native apps are not affected by CORS.

---

## 3. First run on the tablet

1. **Sign in**: store number, employee username and password. To enroll the tablet first, use
   **Enroll with a code**.
2. If you are a manager, tap **Register this tablet**. Once registered and approved, staff can sign in with a **PIN**.
3. **Settings → Card payments**: check the bbpos URL, then **Sign in to bbpos** with the tablet Cashier
   account. The card shows Stripe mode, Terminal location and any configuration problems.
4. **Reader tab**: choose **WisePad 3 (Bluetooth)**. Turn the reader on (hold the power button) and keep it
   close, then tap **Scan for readers** → **Connect**. Android asks for **Nearby devices** and **Location**
   permissions (Stripe requires both). The first connection may install a reader update; keep the reader
   charged and nearby.
5. **Cash tab**: open the register with the starting float. Cash is only allowed on a registered, approved tablet.
6. Take a test order: **New order** → add items → **Pay** → **Card**.
   - Stripe test mode with a real WisePad 3 needs a **Stripe physical test card**.
   - Without hardware, use **Stripe simulated reader** mode (needs bbpos test mode) or **Mock** mode
     (no Stripe, no money; turn on "Accept bbpos MOCK transactions" for the store while testing).

## 4. Going live

1. In bbpos admin, switch Stripe to **live** keys. Simulated and mock modes then disappear automatically.
2. In the portal, turn **off** "Accept bbpos MOCK transactions".
3. Build production apps with HTTPS URLs (`eas.json` → `production`):
   ```bash
   eas build --profile production --platform all
   ```
4. Distribute through Google Play (internal testing or managed Google Play) and the App Store / TestFlight,
   or Apple Business Manager for company-owned iPads.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| "The server is unavailable (502)…" or "The POS API service is not responding" | The Pizza API container is down; the website may still load. On the server run `sh deploy/scripts/diagnose.sh` and follow `deploy/TROUBLESHOOTING.md`. |
| "Cannot reach the server" | Wrong URL, no Wi-Fi, or `http://` on Android without cleartext. Use **Server settings → Save & test**. |
| "Sign-in is only permitted from a registered, approved device" | HQ policy: enroll or register the tablet first. |
| Card tab says bbpos is not set up | Sign in to bbpos in **Settings**. Check that the store's card processor is **bbpos** in the portal. |
| "No Stripe Terminal location is configured" | bbpos admin → Readers & Locations. |
| "Custom amounts are disabled" | bbpos admin → Settings → Pricing → allow custom amounts. |
| Payment approved but "Saving it to the order failed" | The Pizza server or its link to bbpos is down. The payment is queued and retried; see **Orders**. Check the store's bbpos service account in the portal. |
| "Mock payments are only available in test mode…" | bbpos is live or mock is disabled: use a real reader. |
| No readers found | Reader off, out of range, or connected to another phone. Check Bluetooth and Location permissions. |
| Only "Mock" is offered | You are in Expo Go or a build without the native SDK: install a development build. |

## Development

```bash
npm run typecheck
```

```bash
npm test
```

`npm test` runs the card payment flow unit tests in `test/cardFlow.test.ts`.

| Path | What it is |
|---|---|
| `src/lib/pizzaApi.ts` | Pizza API client: POS JWT with refresh, device credential |
| `src/lib/bbposApi.ts` | bbpos API client and the contract used |
| `src/lib/cardFlow.ts` | Card payment orchestration, idempotency and the pending-record queue |
| `src/terminal/` | Stripe Terminal SDK bridge, mock reader and mode selection (adapted from bbpos) |
| `src/screens/` | Login, New order, Customizer, Payment, Orders, Cash, Reader, Settings |
| `app.config.ts`, `eas.json` | Native configuration and build profiles |

Security: tokens and device credentials are stored in the iOS Keychain / Android Keystore
(`expo-secure-store`). No card data is handled by the app. Every card payment is verified by both bbpos
(with Stripe) and the Pizza server (with bbpos) before an order is marked paid.
