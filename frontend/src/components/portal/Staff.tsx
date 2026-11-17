"use client";
import { useEffect, useState } from "react";
import { portalApi, usePortalSession } from "@/components/PortalShell";
import { Badge, Button, Card, ErrorBanner, Field, Input, Modal, Select, Table } from "@/components/ui";
import { ApiError, results } from "@/lib/api";
import { useStores } from "./useStores";

export default function Staff() {
  const s = usePortalSession();
  const { stores } = useStores();
  const [users, setUsers] = useState<any[]>([]);
  const [roles, setRoles] = useState<any[]>([]);
  const [franchisees, setFranchisees] = useState<any[]>([]);
  const [edit, setEdit] = useState<any | null>(null);
  const load = () => portalApi.get("/users/?page_size=200").then((d) => setUsers(results(d)));
  useEffect(() => {
    load();
    portalApi.get("/roles/").then(setRoles);
    portalApi.get("/franchisees/").then((d) => setFranchisees(results(d)));
  }, []);
  const allowedRoles = roles.filter((r) => s.user.role_level === "hq" || r.level !== "hq");
  return (
    <Card title="Employees & users" actions={<Button onClick={() => setEdit({ role: "cashier", store_ids: [], is_active: true })}>Add employee</Button>}>
      <Table head={["Username", "Name", "Role", "Stores", "PIN / manager code", "Status", ""]} empty={!users.length}>
        {users.map((u) => (
          <tr key={u.id}>
            <td className="px-3 py-2 font-medium">{u.username}</td>
            <td className="px-3">{u.first_name} {u.last_name}</td>
            <td className="px-3">{u.role}</td>
            <td className="px-3 text-xs">{u.stores.map((x: any) => x.number).join(", ")}</td>
            <td className="px-3 text-xs">{u.has_pin ? "PIN ✓" : "—"} {u.has_manager_code ? "· Mgr ✓" : ""}</td>
            <td className="px-3">{u.is_active ? <Badge tone="connected">active</Badge> : <Badge tone="disabled">inactive</Badge>}</td>
            <td className="px-3"><Button size="sm" variant="ghost" onClick={() => setEdit({ ...u, store_ids: u.stores.map((x: any) => x.id) })}>Edit</Button></td>
          </tr>
        ))}
      </Table>
      {edit && <UserModal user={edit} roles={allowedRoles} stores={stores} franchisees={franchisees} onClose={() => { setEdit(null); load(); }} />}
    </Card>
  );
}

function UserModal({ user, roles, stores, franchisees, onClose }: any) {
  const [u, setU] = useState<any>(user);
  const [error, setError] = useState<string | null>(null);
  const role = roles.find((r: any) => r.code === u.role);
  async function save() {
    setError(null);
    const body: any = {
      username: u.username, first_name: u.first_name || "", last_name: u.last_name || "", email: u.email || "",
      role: u.role, is_active: u.is_active, store_ids: u.store_ids, franchisee: role?.level === "franchise" ? u.franchisee : null,
    };
    for (const k of ["password", "pin", "manager_code"]) if (u[k]) body[k] = u[k];
    try {
      if (u.id) await portalApi.patch(`/users/${u.id}/`, body);
      else await portalApi.post("/users/", body);
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.describe() : String(e));
    }
  }
  return (
    <Modal open title={u.id ? `Edit ${u.username}` : "New employee"} onClose={onClose}>
      <div className="space-y-3">
        <ErrorBanner error={error} />
        <div className="grid grid-cols-2 gap-3">
          <Field label="Username"><Input value={u.username || ""} disabled={!!u.id} onChange={(e) => setU({ ...u, username: e.target.value })} /></Field>
          <Field label="Role"><Select value={u.role} onChange={(e) => setU({ ...u, role: e.target.value })}>{roles.map((r: any) => <option key={r.code} value={r.code}>{r.name}</option>)}</Select></Field>
          <Field label="First name"><Input value={u.first_name || ""} onChange={(e) => setU({ ...u, first_name: e.target.value })} /></Field>
          <Field label="Last name"><Input value={u.last_name || ""} onChange={(e) => setU({ ...u, last_name: e.target.value })} /></Field>
          <Field label={u.id ? "New password (optional)" : "Password"}><Input type="password" autoComplete="new-password" onChange={(e) => setU({ ...u, password: e.target.value })} /></Field>
          <Field label="POS PIN (4–8 digits)"><Input inputMode="numeric" type="password" onChange={(e) => setU({ ...u, pin: e.target.value })} /></Field>
          <Field label="Manager authorization code"><Input inputMode="numeric" type="password" onChange={(e) => setU({ ...u, manager_code: e.target.value })} /></Field>
          {role?.level === "franchise" && (
            <Field label="Franchisee"><Select value={u.franchisee ?? ""} onChange={(e) => setU({ ...u, franchisee: Number(e.target.value) })}><option value="">—</option>{franchisees.map((f: any) => <option key={f.id} value={f.id}>{f.code} {f.name}</option>)}</Select></Field>
          )}
        </div>
        {role?.level === "store" && (
          <Field label="Authorized stores">
            <div className="flex flex-wrap gap-2">
              {stores.map((s: any) => (
                <label key={s.id} className="flex items-center gap-1 rounded border px-2 py-1 text-sm">
                  <input type="checkbox" checked={u.store_ids.includes(s.id)} onChange={(e) => setU({ ...u, store_ids: e.target.checked ? [...u.store_ids, s.id] : u.store_ids.filter((x: number) => x !== s.id) })} />
                  {s.number}
                </label>
              ))}
            </div>
          </Field>
        )}
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={u.is_active} onChange={(e) => setU({ ...u, is_active: e.target.checked })} /> Active</label>
        <Button onClick={save}>Save</Button>
      </div>
    </Modal>
  );
}
