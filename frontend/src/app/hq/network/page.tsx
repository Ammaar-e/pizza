"use client";
import { PageTitle } from "@/components/PortalShell";
import Network from "@/components/portal/Network";

export default function Page() {
  return (
    <>
      <PageTitle title="Franchisees & stores" sub="Create franchisees and stores, and assign stores to franchisees." />
      <Network />
    </>
  );
}
