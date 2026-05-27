export const SessionID = {
  pipe(_schema?: unknown) { return this },
  annotate(_meta?: unknown) { return this },
}

let _msgCounter = Date.now()
let _partCounter = Date.now()

export const MessageID = {
  ascending(): string {
    return `msg_${++_msgCounter}`
  },
}

export const PartID = {
  ascending(): string {
    return `part_${++_partCounter}`
  },
}
