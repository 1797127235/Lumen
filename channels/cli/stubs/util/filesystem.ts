import { readFile, writeFile, mkdir } from "node:fs/promises"
import path from "node:path"

export const Filesystem = {
  async readJson<T>(filePath: string): Promise<T> {
    const text = await readFile(filePath, "utf-8")
    return JSON.parse(text) as T
  },

  async writeJson(filePath: string, data: unknown): Promise<void> {
    await mkdir(path.dirname(filePath), { recursive: true })
    await writeFile(filePath, JSON.stringify(data, null, 2), "utf-8")
  },

  async readText(filePath: string): Promise<string> {
    return readFile(filePath, "utf-8")
  },

  async readBytes(filePath: string): Promise<Buffer> {
    return readFile(filePath) as Promise<Buffer>
  },

  async write(filePath: string, content: string): Promise<void> {
    await mkdir(path.dirname(filePath), { recursive: true })
    await writeFile(filePath, content, "utf-8")
  },
}
