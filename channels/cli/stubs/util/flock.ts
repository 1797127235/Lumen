export const Flock = {
  async withLock<T>(_lock: string, fn: () => Promise<T>): Promise<T> {
    return fn()
  },
}
