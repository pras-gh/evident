import type { Metadata, Viewport } from "next";
import "./globals.css";

// icon.svg, apple-icon.png and opengraph-image.png sit next to this file and
// are picked up by Next's file conventions; metadataBase makes the share
// image's URL absolute, as link previews require.
export const metadata: Metadata = {
  metadataBase: new URL(process.env.APP_URL ?? "http://localhost:3000"),
  title: "Company Memory — Parallx",
  description: "Structured company intelligence from SEC filings. Every answer cites a "
    + "page and a paragraph.",
  applicationName: "Parallx",
  openGraph: { siteName: "Parallx", type: "website" },
  twitter: { card: "summary_large_image" },
};

export const viewport: Viewport = { themeColor: "#0a0a0b" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
