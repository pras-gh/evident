// Types for next.config.mjs, which is JavaScript (Next loads it before any
// TypeScript is compiled), so its production check can be tested from TS.
import type { NextConfig } from "next";

export declare function productionProblems(
  env?: Record<string, string | undefined>,
): string[];

declare const nextConfig: NextConfig;
export default nextConfig;
