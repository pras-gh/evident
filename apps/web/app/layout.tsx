import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Company Memory — Evident",
  description: "Structured company intelligence from SEC filings.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
