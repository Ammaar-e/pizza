"use client";
import { PageTitle } from "@/components/PortalShell";
import CashManagement from "@/components/portal/CashManagement";

export default function Page() {
  return (
    <>
      <PageTitle title="Cash management" sub="Register sessions, discrepancies and shift closings for your stores." />
      <CashManagement />
    </>
  );
}
