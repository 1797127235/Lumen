export namespace Snapshot {
  export type FileDiff = {
    path: string
    type: "added" | "modified" | "deleted"
  }
}
