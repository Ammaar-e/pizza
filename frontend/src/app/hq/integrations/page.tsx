"use client";
import { PageTitle } from "@/components/PortalShell";
import Integrations from "@/components/portal/Integrations";

export default function Page() {
  return (
    <>
      <PageTitle title="Store integrations" sub="Per-store provider connections." />
      <Integrations />
    </>
  );
}
