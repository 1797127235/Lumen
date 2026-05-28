export * as TuiConfig from "./tui"

import { createBindingLookup } from "@opentui/keymap/extras"
import { TuiKeybind } from "./keybind"

export type Info = {
  theme?: string
  mouse?: boolean
  scroll_speed?: number
  scroll_acceleration?: { enabled?: boolean }
  leader_timeout?: number
  keybinds?: Record<string, unknown>
  attention?: Record<string, unknown>
}

export type Resolved = Omit<Info, "keybinds" | "leader_timeout"> & {
  keybinds: TuiKeybind.BindingLookupView
  leader_timeout: number
}

export function createDefaultConfig(overrides?: Partial<Info>): Resolved {
  const keybinds = TuiKeybind.parse({})
  const bindingConfig = TuiKeybind.toBindingConfig(keybinds)
  const bindingLookup = createBindingLookup(bindingConfig, {
    defaults: TuiKeybind.bindingDefaults(),
    commandMap: TuiKeybind.CommandMap,
  })

  return {
    mouse: overrides?.mouse ?? true,
    scroll_speed: overrides?.scroll_speed,
    scroll_acceleration: overrides?.scroll_acceleration,
    theme: overrides?.theme,
    keybinds: bindingLookup,
    leader_timeout: 500,
  }
}
