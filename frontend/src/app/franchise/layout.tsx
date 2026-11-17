import PortalShell from "@/components/PortalShell";

const NAV = [
  { href: "/franchise", label: "Sales & reporting" },
  { href: "/franchise/stores", label: "Stores & menu" },
  { href: "/franchise/integrations", label: "Delivery integrations", perm: "integrations.view" },
  { href: "/franchise/promotions", label: "Promotions", perm: "promotions.view" },
  { href: "/franchise/cash", label: "Cash management", perm: "cash.review" },
  { href: "/franchise/delivery", label: "Delivery & drivers", perm: "dispatch.view" },
  { href: "/franchise/devices", label: "Devices", perm: "devices.view" },
  { href: "/franchise/staff", label: "Employees" },
  { href: "/franchise/requests", label: "Product requests" },
  { href: "/franchise/api-clients", label: "API clients", perm: "api.manage_clients" },
  { href: "/franchise/audit", label: "Audit log", perm: "audit.view" },
];

export default function Layout({ children }: { children: React.ReactNode }) {
  return <PortalShell title="Franchise portal" nav={NAV} level="franchise">{children}</PortalShell>;
}
