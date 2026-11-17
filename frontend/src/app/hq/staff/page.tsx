"use client";
import { PageTitle } from "@/components/PortalShell";
import Staff from "@/components/portal/Staff";

export default function Page() {
  return (
    <>
      <PageTitle title="Users & roles" sub="Headquarters employees and all franchise users." />
      <Staff />
    </>
  );
}
