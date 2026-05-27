export const BusEvent = {
  define<T extends string>(type: T, _schema?: unknown): { type: T } {
    return { type }
  },
}
