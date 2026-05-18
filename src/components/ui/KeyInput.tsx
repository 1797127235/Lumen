import { useState } from 'react';

interface KeyInputProps {
  value: string;
  onChange: (val: string) => void;
  placeholder?: string;
  onBlur?: () => void;
}

export function KeyInput({ value, onChange, placeholder, onBlur }: KeyInputProps) {
  const [visible, setVisible] = useState(false);

  return (
    <div className="flex items-center border border-border rounded-sm overflow-hidden bg-surface-elevated focus-within:border-ink transition-colors w-[240px]">
      <input
        className="flex-1 px-3 py-2 text-sm bg-transparent outline-none min-w-0"
        type={visible ? 'text' : 'password'}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        onBlur={onBlur}
      />
      <button
        className="px-3 py-2 text-xs text-text-subtle hover:text-text transition-colors border-l border-border whitespace-nowrap"
        type="button"
        onClick={() => setVisible(!visible)}
      >
        {visible ? '隐藏' : '显示'}
      </button>
    </div>
  );
}
