"use client";

import { useEffect, useState } from "react";

import { ACCOUNT_CHANGED } from "@/lib/account";
import { getAccount, type Account } from "@/lib/api/client";

/**
 * Who this browser is signed in as: `undefined` while asking, `null` for nobody.
 *
 * **Only the newest answer counts.** A check started before signing in can
 * finish after the one started just after it, and letting it land would tell a
 * reader who has just created an account that they are signed out. Each check
 * takes a number, and a response for anything but the latest is dropped.
 *
 * A failure reads as signed out. This is chrome: the page the reader is on says
 * when the service is unreachable, and the header offering sign-in is the
 * harmless default.
 */
export function useAccount(): Account | null | undefined {
  const [account, setAccount] = useState<Account | null | undefined>(undefined);

  useEffect(() => {
    let latest = 0;
    let active = true;

    const refresh = () => {
      const ticket = ++latest;
      getAccount()
        .then((found) => {
          if (active && ticket === latest) setAccount(found);
        })
        .catch(() => {
          if (active && ticket === latest) setAccount(null);
        });
    };

    refresh();
    window.addEventListener(ACCOUNT_CHANGED, refresh);
    return () => {
      active = false;
      window.removeEventListener(ACCOUNT_CHANGED, refresh);
    };
  }, []);

  return account;
}
