export function errorMessage(error: unknown): string {
  if (typeof error === "string") return error
  if (error instanceof Error) return error.message
  if (typeof error === "object" && error !== null && "message" in error) {
    return String((error as { message: unknown }).message)
  }
  return String(error)
}

export function errorData(error: unknown): Record<string, unknown> | undefined {
  if (typeof error === "object" && error !== null && "data" in error) {
    const d = (error as { data: unknown }).data
    if (typeof d === "object" && d !== null) return d as Record<string, unknown>
  }
  return undefined
}

export function FormatError(_error: unknown): string | undefined {
  return undefined
}

export function FormatUnknownError(error: unknown): string {
  return errorMessage(error)
}

export function errorFormat(error: unknown): string {
  return errorMessage(error)
}
