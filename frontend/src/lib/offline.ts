// Encrypted local storage for the POS (IndexedDB + WebCrypto AES-GCM).
// The AES key is generated per device as a NON-EXTRACTABLE CryptoKey and persisted in IndexedDB,
// so cached menus/config and queued orders are unreadable outside this browser profile.
const DB_NAME = "pzp-pos";
const DB_VERSION = 1;

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains("keys")) db.createObjectStore("keys");
      if (!db.objectStoreNames.contains("kv")) db.createObjectStore("kv");
      if (!db.objectStoreNames.contains("queue")) db.createObjectStore("queue", { keyPath: "ref" });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function tx<T>(store: string, mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  return openDb().then(
    (db) =>
      new Promise<T>((resolve, reject) => {
        const t = db.transaction(store, mode);
        const r = fn(t.objectStore(store));
        r.onsuccess = () => resolve(r.result);
        r.onerror = () => reject(r.error);
      }),
  );
}

let keyPromise: Promise<CryptoKey> | null = null;
function getKey(): Promise<CryptoKey> {
  if (!keyPromise) {
    keyPromise = (async () => {
      const existing = await tx<CryptoKey | undefined>("keys", "readonly", (s) => s.get("aes"));
      if (existing) return existing;
      const key = await crypto.subtle.generateKey({ name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
      await tx("keys", "readwrite", (s) => s.put(key, "aes"));
      return key;
    })();
  }
  return keyPromise;
}

async function encrypt(value: unknown): Promise<{ iv: Uint8Array; data: ArrayBuffer }> {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const data = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, await getKey(), new TextEncoder().encode(JSON.stringify(value)));
  return { iv, data };
}

async function decrypt<T>(blob: { iv: Uint8Array; data: ArrayBuffer } | undefined): Promise<T | null> {
  if (!blob) return null;
  try {
    const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: blob.iv as BufferSource }, await getKey(), blob.data);
    return JSON.parse(new TextDecoder().decode(plain)) as T;
  } catch {
    return null;
  }
}

export async function cachePut(key: string, value: unknown) {
  const blob = await encrypt(value);
  await tx("kv", "readwrite", (s) => s.put(blob, key));
}

export async function cacheGet<T>(key: string): Promise<T | null> {
  return decrypt<T>(await tx<any>("kv", "readonly", (s) => s.get(key)));
}

export interface QueuedOrder {
  ref: string; // offline_client_ref — doubles as idempotency key server-side
  createdAt: string;
  order: any;
  payments: { method: "cash"; amount_cents?: number; tendered_cents: number }[]; // amount omitted = settle balance
  status: "queued" | "syncing" | "error";
  error?: string;
  summary: { total_cents: number; items: number; customer?: string };
}

export async function enqueue(o: QueuedOrder) {
  const blob = await encrypt(o);
  await tx("queue", "readwrite", (s) => s.put({ ref: o.ref, blob }));
}

export async function queued(): Promise<QueuedOrder[]> {
  const rows = await tx<any[]>("queue", "readonly", (s) => s.getAll());
  const out: QueuedOrder[] = [];
  for (const r of rows) {
    const o = await decrypt<QueuedOrder>(r.blob);
    if (o) out.push(o);
  }
  return out.sort((a, b) => a.createdAt.localeCompare(b.createdAt));
}

export async function dequeue(ref: string) {
  await tx("queue", "readwrite", (s) => s.delete(ref));
}
