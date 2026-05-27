// Simplified TuiEvent definitions — no Effect Schema, just type-safe event names.

export const TuiEvent = {
  PromptAppend: { type: "tui.prompt.append" as const },
  CommandExecute: { type: "tui.command.execute" as const },
  ToastShow: { type: "tui.toast.show" as const },
  SessionSelect: { type: "tui.session.select" as const },
}
