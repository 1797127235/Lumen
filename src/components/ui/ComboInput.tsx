import { useState, useRef, useEffect } from 'react';

interface Preset {
  label: string;
  value: number;
}

interface ComboInputProps {
  presets: Preset[];
  value: string;
  onChange: (val: string) => void;
  placeholder?: string;
}

export function ComboInput({ presets, value, onChange, placeholder }: ComboInputProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('click', handler);
    return () => document.removeEventListener('click', handler);
  }, [open]);

  return (
    <div className="relative flex items-center" ref={ref}>
      <input
        type="number"
        className="flex-1 px-sm py-2 border border-border rounded-l-lg text-sm bg-surface-elevated outline-none focus:border-ink transition-colors"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
      />
      <button
        type="button"
        className="px-2 py-2 border border-l-0 border-border rounded-r-lg text-sm text-text-subtle hover:text-text bg-surface-elevated"
        onClick={(e) => { e.stopPropagation(); setOpen(!open); }}
      >
        ▾
      </button>
      {open && (
        <div className="absolute top-full right-0 mt-1 w-40 max-h-48 overflow-y-auto rounded-lg border border-border bg-surface shadow-lg z-50">
          {presets.map(p => (
            <button
              key={p.value}
              type="button"
              className="w-full flex items-center justify-between px-3 py-2 text-sm hover:bg-surface-elevated transition-colors"
              onClick={(e) => {
                e.stopPropagation();
                onChange(String(p.value));
                setOpen(false);
              }}
            >
              <span>{p.label}</span>
              <span className="text-text-subtle text-xs">{p.value.toLocaleString()}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
