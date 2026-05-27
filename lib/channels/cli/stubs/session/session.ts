export type Session = {
  id: string
  title?: string
  time: {
    created: number
    updated: number
  }
  path: {
    cwd: string
  }
  cost?: number
  parentID?: string
  workspaceID?: string
}

export const Session = {
  isDefaultTitle(title: string | undefined): boolean {
    return !title || title.startsWith("New Conversation")
  },
}
