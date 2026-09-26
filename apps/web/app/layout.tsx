import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import { ClockBadge } from "@/components/ClockBadge";

export const metadata: Metadata = {
  title: "Autonomous Price-Watch Buyer",
  description: "Durable price watcher with delegated purchase authority (UCP + AP2 / Verifiable Intent)",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="top">
          <nav>
            <Link href="/">Watches</Link>
            <Link href="/watches/new">Create watch</Link>
          </nav>
          <ClockBadge />
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
