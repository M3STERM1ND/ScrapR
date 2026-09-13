import type { Metadata } from "next";

import { AuthForm } from "@/components/account/AuthForm";
import { Eyebrow } from "@/components/ui/primitives";
import { safeNext } from "@/lib/account";

export const metadata: Metadata = {
  title: "Create an account — ScrapR",
  robots: { index: false, follow: false },
};

/**
 * Create an account (Flow E, E-1 and E-2).
 *
 * An account is a place to keep research, not a gate in front of it
 * (`REQ-AUTH-001`), and the copy says exactly that.
 */
export default async function SignUpPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { next } = await searchParams;
  return (
    <div className="shell py-20 md:py-28">
      <Eyebrow>Save your research</Eyebrow>
      <h1 className="display-l mt-6 max-w-[16ch]">Keep what you found</h1>
      <p className="measure mt-6 text-lead text-ink-soft">
        An account keeps your research, its versions and its conversation, so
        you can come back to it. Nothing you can do without one changes.
      </p>
      <AuthForm mode="signup" next={safeNext(next)} />
    </div>
  );
}
