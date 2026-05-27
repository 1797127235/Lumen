import { platform } from "node:os"
import { spawn } from "node:child_process"

export interface Content {
  data: string
  mime: string
}

function writeOsc52(text: string): void {
  if (!process.stdout.isTTY) return
  const base64 = Buffer.from(text).toString("base64")
  const osc52 = `\x1b]52;c;${base64}\x07`
  const passthrough = process.env["TMUX"] || process.env["STY"]
  const sequence = passthrough ? `\x1bPtmux;\x1b${osc52}\x1b\\` : osc52
  process.stdout.write(sequence)
}

async function runCommand(cmd: string, args: string[], stdin?: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, { stdio: ["pipe", "pipe", "pipe"] })
    let out = ""
    child.stdout.on("data", (d: Buffer) => (out += d.toString()))
    child.on("close", (code) => (code === 0 ? resolve(out) : reject(new Error(`Exit ${code}`))))
    child.on("error", reject)
    if (stdin !== undefined) {
      child.stdin.end(stdin, "utf8")
    }
  })
}

export async function copy(text: string): Promise<void> {
  writeOsc52(text)
  const os = platform()
  try {
    if (os === "win32") {
      await runCommand(
        "powershell.exe",
        ["-NonInteractive", "-NoProfile", "-Command",
          "[Console]::InputEncoding = [System.Text.Encoding]::UTF8; Set-Clipboard -Value ([Console]::In.ReadToEnd())"],
        text,
      )
    } else if (os === "darwin") {
      await runCommand("pbcopy", [], text)
    } else {
      const display = process.env["WAYLAND_DISPLAY"]
      if (display) {
        await runCommand("wl-copy", [], text)
      } else {
        await runCommand("xclip", ["-selection", "clipboard"], text)
      }
    }
  } catch {
    // OSC 52 already sent — clipboard write errors are non-fatal
  }
}

export async function read(): Promise<Content | undefined> {
  const os = platform()
  try {
    if (os === "win32") {
      const text = await runCommand("powershell.exe", [
        "-NonInteractive", "-NoProfile", "-Command", "Get-Clipboard",
      ])
      if (text.trim()) return { data: text.trim(), mime: "text/plain" }
    } else if (os === "darwin") {
      const text = await runCommand("pbpaste", [])
      if (text) return { data: text, mime: "text/plain" }
    }
  } catch {
    // ignore
  }
  return undefined
}

export * as Clipboard from "./clipboard"
