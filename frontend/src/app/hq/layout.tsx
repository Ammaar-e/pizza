import PortalShell from "@/components/PortalShell";

const NAV = [
  { href: "/hq", label: "Dashboard" },
  { href: "/hq/network", label: "Franchisees & stores", perm: "org.manage_franchisees" },
  { href: "/hq/catalog", label: "Master menu", perm: "catalog.manage_master" },
  { href: "/hq/requests", label: "Product requests" },
  { href: "/hq/stores", label: "Store configuration", perm: "org.configure_store" },
  { href: "/hq/providers", label: "Delivery providers" },
  { href: "/hq/integrations", label: "Store integrations", perm: "integrations.configure_store" },
  { href: "/hq/staff", label: "Users & roles" },
  { href: "/hq/api-clients", label: "API clients", perm: "api.manage_clients" },
  { href: "/hq/audit", label: "Audit log", perm: "audit.view" },
];

export default function Layout({ children }: { children: React.ReactNode }) {
  return <PortalShell title="Headquarters" nav={NAV} level="hq">{children}</PortalShell>;
}
