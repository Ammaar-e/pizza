import type { Metadata } from "next";

export const metadata: Metadata = { title: "POS · Pizza Network", manifest: "/manifest.webmanifest" };

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
