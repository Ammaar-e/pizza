"use client";
import { PageTitle } from "@/components/PortalShell";
import StoreSettings from "@/components/portal/StoreSettings";

export default function Page() {
  return (
    <>
      <PageTitle title="Store configuration" sub="Any store in the network." />
      <StoreSettings />
    </>
  );
}
