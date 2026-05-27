export const NamedError = {
  hasName(input: unknown, name: string): boolean {
    if (typeof input !== "object" || input === null) return false
    const obj = input as Record<string, unknown>
    return obj.name === name || obj._tag === name
  },
}
