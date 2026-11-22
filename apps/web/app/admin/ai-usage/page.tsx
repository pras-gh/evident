import type { Metadata } from "next";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { UsageDashboard } from "@/components/admin/UsageDashboard";
import {
  ADMIN_COOKIE,
  AdminApiError,
  adminGet,
  parseFilters,
  summaryPath,
  type UsageSummary,
} from "@/lib/admin";

export const metadata: Metadata = { title: "AI usage — Parallx admin" };
export const dynamic = "force-dynamic";

export default async function AiUsagePage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const key = (await cookies()).get(ADMIN_COOKIE)?.value;
  if (!key) redirect("/admin/login");
  const filters = parseFilters(await searchParams);

  let data: UsageSummary;
  try {
    data = await adminGet<UsageSummary>(summaryPath(filters), key);
  } catch (e) {
    // a revoked key, or one demoted since sign-in
    if (e instanceof AdminApiError && (e.status === 401 || e.status === 403)) {
      redirect("/admin/login?error=expired");
    }
    throw e;
  }
  const today = new Date().toISOString().slice(0, 10);
  return <UsageDashboard data={data} filters={filters} today={today} />;
}
