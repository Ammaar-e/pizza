"use client";
import { PageTitle } from "@/components/PortalShell";
import ProductRequests from "@/components/portal/ProductRequests";

export default function Page() {
  return (
    <>
      <PageTitle title="Product requests" sub="Ask headquarters to add products to the master menu." />
      <ProductRequests />
    </>
  );
}
