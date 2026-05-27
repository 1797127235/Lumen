export type ConsoleState = {
  switchableOrgCount: number
  activeOrgName?: string
}

export function emptyConsoleState(): ConsoleState {
  return { switchableOrgCount: 0 }
}
