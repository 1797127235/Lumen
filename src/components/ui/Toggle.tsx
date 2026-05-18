import type { MouseEvent } from 'react';

interface ToggleProps {
  on: boolean;
  onChange: (on: boolean) => void;
  label?: string;
  disabled?: boolean;
}

export function Toggle({ on, onChange, label, disabled = false }: ToggleProps) {
  return (
    <div className="flex items-center gap-2">
      <button
        className={`relative w-9 h-5 rounded-full transition-colors ${on ? 'bg-ink' : 'bg-border'} disabled:opacity-40`}
        type="button"
        disabled={disabled}
        aria-label={label}
        role="switch"
        aria-checked={on}
        onClick={(e: MouseEvent<HTMLButtonElement>) => {
          e.stopPropagation();
          onChange(!on);
        }}
      >
        <span
          className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-bg transition-transform ${on ? 'translate-x-4' : ''}`}
        />
      </button>
      {label && <span className="text-sm text-text-subtle">{label}</span>}
    </div>
  );
}
