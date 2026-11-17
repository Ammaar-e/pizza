"use client";
import { PageTitle } from "@/components/PortalShell";
import Catalog from "@/components/portal/Catalog";

export default function Page() {
  return (
    <>
      <PageTitle title="Master menu" sub="Headquarters-controlled products, sizes, crusts, modifiers and permitted pricing. Every save creates a new version." />
      <Catalog />
    </>
  );
}
