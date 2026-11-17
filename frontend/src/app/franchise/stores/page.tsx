"use client";
import { PageTitle } from "@/components/PortalShell";
import StoreSettings from "@/components/portal/StoreSettings";

export default function Page() {
  return (
    <>
      <PageTitle title="Stores & menu" sub="Configure each of your stores independently." />
      <StoreSettings />
    </>
  );
}
