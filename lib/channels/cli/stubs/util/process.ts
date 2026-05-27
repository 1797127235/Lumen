import { spawn as nodeSpawn } from "node:child_process"

type SpawnOptions = {
  stdin?: "inherit" | "pipe"
  stdout?: "inherit" | "pipe"
  stderr?: "inherit" | "pipe"
  shell?: boolean
  nothrow?: boolean
}

type SpawnResult = {
  exited: Promise<void>
  stdout: Buffer
}

export const Process = {
  spawn(cmd: string[], options?: SpawnOptions): SpawnResult {
    const [exe, ...args] = cmd
    let stdoutBufs: Buffer[] = []
    const child = nodeSpawn(exe!, args, {
      shell: options?.shell,
      stdio: [options?.stdin ?? "pipe", options?.stdout ?? "pipe", options?.stderr ?? "pipe"],
    })
    if (child.stdout) child.stdout.on("data", (d: Buffer) => stdoutBufs.push(d))
    const exited = new Promise<void>((resolve, reject) => {
      child.on("close", (code) => {
        if (code === 0 || options?.nothrow) resolve()
        else reject(new Error(`Exit ${code}`))
      })
      child.on("error", (e) => (options?.nothrow ? resolve() : reject(e)))
    })
    return {
      exited,
      get stdout() {
        return Buffer.concat(stdoutBufs)
      },
    }
  },

  async run(cmd: string[], options?: SpawnOptions): Promise<{ stdout: Buffer }> {
    const result = Process.spawn(cmd, options)
    await result.exited
    return { stdout: result.stdout }
  },

  async text(cmd: string[], options?: SpawnOptions): Promise<{ text: string }> {
    const result = await Process.run(cmd, options)
    return { text: result.stdout.toString("utf-8") }
  },
}
