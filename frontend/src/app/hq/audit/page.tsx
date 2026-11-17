"use client";
import { PageTitle } from "@/components/PortalShell";
import Audit from "@/components/portal/Audit";

export default function Page() {
  return (
    <>
      <PageTitle title="Audit log" sub="Every privileged action across the network." />
      <Audit />
    </>
  );
}
