"use client";

import { useEffect, useRef, useState } from "react";
import type { Category } from "@/lib/api";
import { Button, IconPlus, IconWand, IconX, Spinner, cx } from "./ui";

function slug(name: string, taken: Set<string>): string {
  const base = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "category";
  let id = base;
  let n = 2;
  while (taken.has(id)) id = `${base}-${n++}`;
  return id;
}

export function makeCategory(name: string, color: string, existing: Category[], description = ""): Category {
  return { id: slug(name, new Set(existing.map((c) => c.id))), name, color, description };
}

function ColorPicker({
  value,
  palette,
  onChange,
  label,
}: {
  value: string;
  palette: Record<string, string>;
  onChange: (color: string) => void;
  label: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        aria-label={`${label}: change color`}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="grid size-8 place-items-center rounded-md border border-line bg-surface transition-colors hover:border-line-strong"
      >
        <span className="size-4 rounded-[4px] ring-1 ring-black/10" style={{ background: value }} />
      </button>
      {open && (
        <div className="absolute left-0 top-10 z-20 w-[196px] animate-rise rounded-lg border border-line bg-surface p-2.5 shadow-raised">
          <div className="grid grid-cols-5 gap-1.5">
            {Object.entries(palette).map(([name, hex]) => (
              <button
                key={name}
                type="button"
                title={name}
                aria-label={name}
                onClick={() => {
                  onChange(hex);
                  setOpen(false);
                }}
                className={cx(
                  "size-7 rounded-md ring-1 ring-black/10 transition-transform hover:scale-105",
                  value.toUpperCase() === hex.toUpperCase() && "outline-2 outline-offset-2 outline-ink",
                )}
                style={{ background: hex }}
              />
            ))}
          </div>
          <label className="mt-2.5 flex items-center justify-between gap-2 border-t border-line pt-2.5 text-xs text-muted">
            Custom
            <input
              type="color"
              value={value}
              onChange={(e) => onChange(e.target.value.toUpperCase())}
              className="h-7 w-12 cursor-pointer rounded border border-line bg-surface"
            />
          </label>
        </div>
      )}
    </div>
  );
}

export function CategoriesEditor({
  categories,
  palette,
  onChange,
  canDetect,
  detecting,
  onDetect,
  detectNotes,
  aiReady,
}: {
  categories: Category[];
  palette: Record<string, string>;
  onChange: (next: Category[]) => void;
  canDetect: boolean;
  detecting: boolean;
  onDetect: () => void;
  detectNotes: string[];
  aiReady: boolean;
}) {
  const update = (i: number, patch: Partial<Category>) =>
    onChange(categories.map((c, j) => (j === i ? { ...c, ...patch } : c)));
  const remove = (i: number) => onChange(categories.filter((_, j) => j !== i));
  const add = () => {
    const used = new Set(categories.map((c) => c.color.toUpperCase()));
    const color = Object.values(palette).find((h) => !used.has(h.toUpperCase())) ?? "#FFE066";
    onChange([...categories, makeCategory(`Category ${categories.length + 1}`, color, categories)]);
  };

  return (
    <div>
      <div className="flex items-center justify-between gap-3">
        <p className="text-[13px] text-muted">Each highlight is filed under one category.</p>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={onDetect}
          disabled={!canDetect || detecting || !aiReady}
          title={canDetect ? "Read your instructions and suggest categories" : "Add instructions first"}
        >
          {detecting ? <Spinner className="size-3.5" /> : <IconWand className="size-3.5" />}
          Detect from instructions
        </Button>
      </div>

      <ul className="mt-3 space-y-2">
        {categories.map((c, i) => (
          <li key={c.id} className="flex items-center gap-2 animate-fade-in">
            <ColorPicker value={c.color} palette={palette} label={c.name} onChange={(color) => update(i, { color })} />
            <input
              aria-label="Category name"
              value={c.name}
              maxLength={40}
              onChange={(e) => update(i, { name: e.target.value })}
              className="h-8 w-36 shrink-0 rounded-md border border-line bg-surface px-2.5 text-[13px] font-medium text-ink transition-colors hover:border-line-strong focus:border-accent focus:outline-none sm:w-44"
            />
            <input
              aria-label={`What belongs in ${c.name}`}
              value={c.description}
              maxLength={200}
              placeholder="What belongs here (optional)"
              onChange={(e) => update(i, { description: e.target.value })}
              className="h-8 min-w-0 flex-1 rounded-md border border-transparent bg-transparent px-2 text-[13px] text-ink-2 placeholder:text-faint transition-colors hover:border-line focus:border-accent focus:bg-surface focus:outline-none"
            />
            <button
              type="button"
              aria-label={`Remove ${c.name}`}
              onClick={() => remove(i)}
              disabled={categories.length <= 1}
              className="grid size-8 shrink-0 place-items-center rounded-md text-faint transition-colors hover:bg-hover hover:text-ink disabled:invisible"
            >
              <IconX className="size-3.5" />
            </button>
          </li>
        ))}
      </ul>

      {categories.length < 12 && (
        <Button type="button" size="sm" variant="ghost" onClick={add} className="mt-2 -ml-1">
          <IconPlus className="size-3.5" />
          Add category
        </Button>
      )}

      {detectNotes.length > 0 && (
        <ul className="mt-3 space-y-1 rounded-lg bg-subtle px-3 py-2.5 text-[13px] text-ink-2">
          {detectNotes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
