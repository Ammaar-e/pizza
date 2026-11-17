"use client";
import { PageTitle } from "@/components/PortalShell";
import Audit from "@/components/portal/Audit";

export default function Page() {
  return (
    <>
      <PageTitle title="Audit log" sub="Actions taken in your stores." />
      <Audit />
    </>
  );
}
