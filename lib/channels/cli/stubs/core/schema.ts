export type DeepMutable<T> = {
  -readonly [K in keyof T]: T[K] extends object ? DeepMutable<T[K]> : T[K]
}

// Runtime stubs for effect-like schema helpers used in event.ts
export const PositiveInt = {
  pipe(_schema?: unknown) { return this },
  withDecodingDefault(_fn: unknown) { return this },
  annotate(_meta?: unknown) { return this },
}
