"use client";
import { PageTitle } from "@/components/PortalShell";
import Providers from "@/components/portal/Providers";

export default function Page() {
  return (
    <>
      <PageTitle title="Delivery providers" sub="Control which providers franchisees may connect, and monitor integration traffic." />
      <Providers />
    </>
  );
}
