"use client";
import { useEffect, useState } from "react";
import { portalApi } from "@/components/PortalShell";
import { Badge, Button, Card, ErrorBanner, Field, Input, Modal, Select } from "@/components/ui";
import { ApiError, results } from "@/lib/api";

export default function Network() {
  const [tree, setTree] = useState<any[]>([]);
  const [franchisees, setFranchisees] = useState<any[]>([]);
  const [newF, setNewF] = useState<any | null>(null);
  const [newS, setNewS] = useState<any | null>(null);
  const [move, setMove] = useState<any | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = () => {
    portalApi.get("/network/").then(setTree);
    portalApi.get("/franchisees/").then((d) => setFranchisees(results(d)));
  };
  useEffect(load, []);
  const run = async (fn: () => Promise<any>, done: () => void) => {
    setError(null);
    try {
      await fn();
      done();
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.describe() : String(e));
    }
  };
  return (
    <div className="space-y-6">
      <div className="flex gap-2">
        <Button onClick={() => setNewF({ code: "", name: "" })}>New franchisee</Button>
        <Button variant="secondary" onClick={() => setNewS({ number: "", name: "", franchisee: franchisees[0]?.id, address_line1: "", city: "", region: "ON", postal_code: "", phone: "", timezone: "America/Toronto" })}>New store</Button>
      </div>
      <ErrorBanner error={error} />
      <Card title="Headquarters">
        <div className="space-y-4">
          {tree.map((f) => (
            <div key={f.id} className="rounded-lg border p-3">
              <div className="flex items-center gap-2"><span className="font-semibold">Franchisee {f.code}</span><span className="text-slate-500">{f.name}</span>{!f.is_active && <Badge tone="disabled">inactive</Badge>}</div>
              <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {f.stores.map((s: any) => (
                  <div key={s.id} className="flex items-center justify-between rounded border bg-slate-50 px-3 py-2 text-sm">
                    <span><b>Store {s.number}</b> · {s.name}</span>
                    <span className="flex items-center gap-2">
                      <Badge tone={s.is_open_now ? "connected" : "disabled"}>{s.is_open_now ? "open" : "closed"}</Badge>
                      <button className="text-xs text-brand-600 hover:underline" onClick={() => setMove({ ...s, franchisee: f.id })}>Reassign</button>
                    </span>
                  </div>
                ))}
                {!f.stores.length && <span className="text-sm text-slate-500">No stores</span>}
              </div>
            </div>
          ))}
        </div>
      </Card>
      {newF && (
        <Modal open title="New franchisee" onClose={() => setNewF(null)}>
          <div className="space-y-3">
            {["code", "name", "legal_name", "contact_email", "contact_phone"].map((k) => (
              <Field key={k} label={k.replace("_", " ")}><Input value={newF[k] || ""} onChange={(e) => setNewF({ ...newF, [k]: e.target.value })} /></Field>
            ))}
            <Button onClick={() => run(() => portalApi.post("/franchisees/", newF), () => setNewF(null))}>Create</Button>
          </div>
        </Modal>
      )}
      {newS && (
        <Modal open title="New store" onClose={() => setNewS(null)}>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Franchisee"><Select value={newS.franchisee} onChange={(e) => setNewS({ ...newS, franchisee: Number(e.target.value) })}>{franchisees.map((f) => <option key={f.id} value={f.id}>{f.code} {f.name}</option>)}</Select></Field>
            {["number", "name", "address_line1", "city", "region", "postal_code", "phone", "timezone"].map((k) => (
              <Field key={k} label={k.replace("_", " ")}><Input value={newS[k] || ""} onChange={(e) => setNewS({ ...newS, [k]: e.target.value })} /></Field>
            ))}
          </div>
          <Button className="mt-4" onClick={() => run(() => portalApi.post("/stores/", newS), () => setNewS(null))}>Create store</Button>
        </Modal>
      )}
      {move && (
        <Modal open title={`Reassign store ${move.number}`} onClose={() => setMove(null)}>
          <Field label="Franchisee"><Select value={move.franchisee} onChange={(e) => setMove({ ...move, franchisee: Number(e.target.value) })}>{franchisees.map((f) => <option key={f.id} value={f.id}>{f.code} {f.name}</option>)}</Select></Field>
          <Button className="mt-4" onClick={() => run(() => portalApi.patch(`/stores/${move.id}/`, { franchisee: move.franchisee }), () => setMove(null))}>Reassign</Button>
        </Modal>
      )}
    </div>
  );
}
