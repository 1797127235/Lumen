// Stub for @/reference/reference — file reference completions (not needed in Lumen)

export namespace Reference {
  export type Resolved = {
    name: string
    kind: "valid" | "invalid"
    path: string
    message?: string
  }
}

export const Reference = {
  resolveAll(_opts: unknown): Reference.Resolved[] {
    return []
  },
  resolve(_ref: string): null {
    return null
  },
  normalize(_refs: unknown): unknown {
    return {}
  },
}
