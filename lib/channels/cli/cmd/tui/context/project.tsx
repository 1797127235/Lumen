import { createSimpleContext } from "./helper"

export const { use: useProject, provider: ProjectProvider } = createSimpleContext({
  name: "Project",
  init: () => {
    const cwd = process.cwd()
    return {
      data: {
        project: { id: undefined as string | undefined },
        instance: {
          path: {
            home: "",
            state: "",
            config: "",
            worktree: "",
            directory: cwd,
          },
        },
        workspace: {
          current: undefined as string | undefined,
          list: [] as unknown[],
          status: {} as Record<string, string>,
        },
      },
      project() {
        return undefined as string | undefined
      },
      instance: {
        path() {
          return { home: "", state: "", config: "", worktree: "", directory: cwd }
        },
        directory() {
          return cwd
        },
      },
      workspace: {
        current() {
          return undefined as string | undefined
        },
        set(_next?: string | null) {},
        list() {
          return []
        },
        get(_workspaceID: string) {
          return undefined
        },
        status(_workspaceID: string) {
          return undefined
        },
        statuses() {
          return {}
        },
        async sync() {},
      },
      async sync() {},
    }
  },
})
