export type ConsoleState = {
  switchableOrgCount: number
  activeOrgName?: string
  consoleManagedProviders: string[]
}

export function emptyConsoleState(): ConsoleState {
  return { switchableOrgCount: 0, consoleManagedProviders: [] }
}
