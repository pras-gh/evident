import type { Metadata } from "next";
import { AppHeader } from "@/components/brand/AppHeader";

export const metadata: Metadata = { title: "About — Parallx" };

export default function About() {
  return (
    <>
      <AppHeader />
      <main className="doc">
        <h1>About Parallx</h1>
        <p>Parallx reads what public companies file with the SEC — annual and quarterly reports,
          current reports, financial statements — and lets you research them with the evidence
          attached. Every answer cites the filing, the page and the paragraph it came from.</p>
        <p>It tracks the S&amp;P 500, keeps each company&rsquo;s filing list and financial
          statements current within minutes of a filing, and reads a filing in full the first
          time someone researches the company.</p>
        <p>Parallx is a research tool, not investment advice.</p>
        <p><a className="btn-primary" href="/pricing">See plans</a></p>
      </main>
    </>
  );
}
