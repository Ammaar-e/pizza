"use client";
import { PageTitle } from "@/components/PortalShell";
import ApiClients from "@/components/portal/ApiClients";

export default function Page() {
  return (
    <>
      <PageTitle title="API clients" sub="Network-wide external applications such as the call centre." />
      <ApiClients />
    </>
  );
}
