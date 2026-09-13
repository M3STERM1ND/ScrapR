import type { Metadata } from "next";

import { AuthForm } from "@/components/account/AuthForm";
import { Eyebrow } from "@/components/ui/primitives";
import { safeNext } from "@/lib/account";

export const metadata: Metadata = {
  title: "Sign in — ScrapR",
  robots: { index: false, follow: false },
};

/**
 * Sign in (Flow F, F-1).
 *
 * `searchParams` is read here, on the server, and handed to the form as a
 * checked path. Reading it in the client would need a Suspense boundary for
 * no benefit, and the check belongs before the value reaches a component.
 */
export default async function SignInPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { next } = await searchParams;
  return (
    <div className="shell py-20 md:py-28">
      <Eyebrow>Your account</Eyebrow>
      <h1 className="display-l mt-6 max-w-[16ch]">Welcome back</h1>
      <p className="measure mt-6 text-lead text-ink-soft">
        Sign in to see the research you saved, with every version and the
        conversation you had about it.
      </p>
      <AuthForm mode="signin" next={safeNext(next)} />
    </div>
  );
}
