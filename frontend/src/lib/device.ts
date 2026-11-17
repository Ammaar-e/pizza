// Device heartbeat + centrally managed configuration for registered terminals (POS, KDS, driver devices).
// The server decides online/offline from the time it RECEIVES heartbeats; this client only reports.
"use client";
import { useEffect, useState } from "react";
import { DEVICE_KEY, deviceInfo } from "./api";

export const APP_VERSION = "1.1.0";

export interface DeviceConfig {
  config_version: number;
  settings: { features?: Record<string, boolean>; heartbeat_interval_seconds?: number; receipt_copies?: number; idle_lock_minutes?: number; [k: string]: any };
  profile: { id: number; name: string; version: number } | null;
  approved_versions: string[];
  minimum_version: string;
}

const CFG_KEY = "pzp.device.config";

async function deviceFetch(path: string, init: RequestInit = {}) {
  const dev = deviceInfo();
  if (!dev) return null;
  const r = await fetch(`/api/v1${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", "X-Device-Token": dev.token, ...(init.headers || {}) },
  });
  if (r.status === 401 || r.status === 403) {
    const body = await r.json().catch(() => ({}));
    return { revoked: true, body };
  }
  return r.ok || r.status === 202 ? { body: await r.json().catch(() => ({})), status: r.status } : null;
}

export function cachedConfig(): DeviceConfig | null {
  try {
    return JSON.parse(localStorage.getItem(CFG_KEY) || "null");
  } catch {
    return null;
  }
}

export function featureOn(cfg: DeviceConfig | null, feature: string) {
  return cfg?.settings?.features?.[feature] !== false;
}

/** Runs the heartbeat loop and keeps the device configuration in sync. */
export function useDeviceAgent(opts: { appStatus: string; syncStatus: () => string; pending: () => number; username?: string }) {
  const [config, setConfig] = useState<DeviceConfig | null>(null);
  const [state, setState] = useState<{ status?: string; versionStatus?: string; revoked?: string } | null>(null);

  useEffect(() => {
    setConfig(cachedConfig());
    let stopped = false;
    let timer: any;
    const beat = async () => {
      const cfg = cachedConfig();
      try {
        const res = await deviceFetch("/device-auth/heartbeat/", {
          method: "POST",
          body: JSON.stringify({
            software_version: APP_VERSION,
            os_info: navigator.userAgent.slice(0, 180),
            app_status: opts.appStatus,
            sync_status: opts.syncStatus(),
            pending_offline_count: opts.pending(),
            config_version: cfg?.config_version ?? 0,
            signed_in_username: opts.username ?? "",
          }),
        });
        if (res && "revoked" in res && res.revoked) {
          setState({ revoked: res.body?.error?.message || "Device access revoked" });
          if (res.body?.error?.code && ["device_revoked", "device_deactivated", "invalid_device"].includes(res.body.error.code)) {
            localStorage.removeItem(DEVICE_KEY);
          }
        } else if (res?.body) {
          setState({ status: res.body.status, versionStatus: res.body.version_status });
          if (res.body.config_stale || !cfg) {
            const c = await deviceFetch("/device-auth/config/");
            if (c?.body?.config_version) {
              localStorage.setItem(CFG_KEY, JSON.stringify(c.body));
              setConfig(c.body);
              await deviceFetch("/device-auth/config/", { method: "POST", body: JSON.stringify({ config_version: c.body.config_version }) });
            }
          }
        }
      } catch {
        /* offline: the server will mark this device offline from missing heartbeats */
      }
      const interval = (cachedConfig()?.settings?.heartbeat_interval_seconds ?? 60) * 1000;
      if (!stopped) timer = setTimeout(beat, interval);
    };
    if (deviceInfo()) beat();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opts.username]);
  return { config, state };
}
