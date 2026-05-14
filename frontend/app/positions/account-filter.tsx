"use client";

import { useRouter, useSearchParams, usePathname } from "next/navigation";

export function AccountFilter({ accountTypes }: { accountTypes: string[] }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  if (accountTypes.length <= 1) return null;

  const excludeParam = searchParams.get("exclude") ?? "";
  const excluded = new Set(excludeParam.split(",").filter(Boolean));

  const toggle = (type: string) => {
    const next = new Set(excluded);
    next.has(type) ? next.delete(type) : next.add(type);
    const params = new URLSearchParams(searchParams.toString());
    if (next.size > 0) {
      params.set("exclude", [...next].join(","));
    } else {
      params.delete("exclude");
    }
    router.replace(`${pathname}?${params}`);
  };

  return (
    <div className="flex items-center gap-2 flex-wrap">
      <span className="text-xs text-muted-foreground shrink-0">Accounts:</span>
      {accountTypes.map(type => {
        const hidden = excluded.has(type);
        return (
          <button
            key={type}
            onClick={() => toggle(type)}
            title={hidden ? `Show ${type}` : `Hide ${type}`}
            className={`inline-flex h-6 items-center rounded-full border px-2.5 text-xs font-medium transition-colors ${
              hidden
                ? "border-border/40 bg-transparent text-muted-foreground/40 line-through"
                : "border-primary/40 bg-primary/10 text-primary"
            }`}
          >
            {type}
          </button>
        );
      })}
    </div>
  );
}
