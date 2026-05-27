export function defer(cleanup: () => unknown): { [Symbol.asyncDispose](): Promise<void> } {
  return {
    async [Symbol.asyncDispose]() {
      await cleanup()
    },
  }
}
