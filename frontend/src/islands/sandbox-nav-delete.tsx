/** Delete button in the sandbox subnav (island). */

import { useEffect, useState } from "preact/hooks";

import { ensureAuth, hasRole } from "../lib/auth";
import { gwUrl, navigateTo } from "../lib/constants";
import { deleteSandbox } from "./sandboxes";

export default function SandboxNavDelete({ name }: { name: string }) {
  const [isOperator, setIsOperator] = useState(false);

  useEffect(() => {
    void ensureAuth().then(() => setIsOperator(hasRole("operator")));
  }, []);

  if (!isOperator) return null;

  const onDelete = async () => {
    if (await deleteSandbox(name)) {
      navigateTo(gwUrl("/sandboxes"));
    }
  };

  return (
    <button class="btn btn-sm text-muted" title="Delete sandbox" onClick={() => void onDelete()}>
      <i class="bi bi-trash3" />
    </button>
  );
}
