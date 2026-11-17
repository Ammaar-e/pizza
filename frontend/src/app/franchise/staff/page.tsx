"use client";
import { PageTitle } from "@/components/PortalShell";
import Staff from "@/components/portal/Staff";

export default function Page() {
  return (
    <>
      <PageTitle title="Employees" sub="Managers, cashiers, kitchen and delivery staff for your stores." />
      <Staff />
    </>
  );
}
