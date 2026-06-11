/** Sorting + filtering hook for list tables (port of table-utils.js). */

import { useMemo, useState } from "preact/hooks";

export interface SortableTable<T> {
  filterText: string;
  setFilterText: (value: string) => void;
  sortBy: (key: string) => void;
  sortClass: (key: string) => string;
  /** Apply filter (over `filterKeys`) then sort to the given items. */
  view: (items: T[], ...filterKeys: (keyof T & string)[]) => T[];
}

export function useSortableTable<T extends Record<string, unknown>>(
  defaultKey = "",
  defaultDir: "asc" | "desc" = "asc",
): SortableTable<T> {
  const [sortKey, setSortKey] = useState(defaultKey);
  const [sortDir, setSortDir] = useState<"asc" | "desc">(defaultDir);
  const [filterText, setFilterText] = useState("");

  return useMemo(
    () => ({
      filterText,
      setFilterText,
      sortBy(key: string) {
        if (sortKey === key) {
          setSortDir(sortDir === "asc" ? "desc" : "asc");
        } else {
          setSortKey(key);
          setSortDir("asc");
        }
      },
      sortClass(key: string) {
        if (sortKey !== key) return "sortable";
        return `sortable ${sortDir}`;
      },
      view(items: T[], ...filterKeys: (keyof T & string)[]) {
        let out = items;
        if (filterText && filterKeys.length > 0) {
          const q = filterText.toLowerCase();
          out = out.filter((item) =>
            filterKeys.some((k) => String(item[k] ?? "").toLowerCase().includes(q)),
          );
        }
        if (sortKey) {
          const dir = sortDir === "asc" ? 1 : -1;
          out = [...out].sort((a, b) => {
            const va = a[sortKey] ?? "";
            const vb = b[sortKey] ?? "";
            if (typeof va === "number" && typeof vb === "number") return (va - vb) * dir;
            return String(va).localeCompare(String(vb)) * dir;
          });
        }
        return out;
      },
    }),
    [filterText, sortKey, sortDir],
  );
}
